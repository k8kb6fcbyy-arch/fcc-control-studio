"""派单包 §2 三条硬规则的代码级测试（A4 / A5 / A6 是红线）。"""

from __future__ import annotations

import pytest

from conftest import cfg, new_loop, observe, patch, run
from fcc.models import ObservationIn


def _logs(client, loop, kind=None):
    return client.store.list_audit(loop, 500, kind=kind)


# ---------------------------------------------------------------- 规则 A


def test_A4_ai_only_deviation_cannot_trigger(client):
    """只有 ai_inference 支持偏差时，不许干预（A4 红线）。"""
    loop = new_loop(client)
    client.submit_observation(loop, {
        "metric_id": "temp", "value": 90.0, "source_type": "ai_inference",
        "confidence": 0.95, "evidence": ["anomaly-detector: spike"],
        "model": "glm-4-flash", "prompt_version": "v1",
    })
    res = run(client, loop)

    err = res.errors[0]
    assert err["ai_only"] is True
    assert err["out_of_tolerance"] is True    # 偏差确实存在
    assert res.should_intervene is False      # 但不许据此干预
    assert res.executed_actions == []
    assert res.human_review_required is True
    assert err["uncertainty"] > 0
    assert "ai_only_deviation" in " ".join(err["notes"])


def test_A4_ai_plus_measured_allows_intervention(client):
    """一旦有 measured 证据，偏差就可以正常触发（AI 只做补充）。"""
    loop = new_loop(client)
    client.submit_observation(loop, {
        "metric_id": "temp", "value": 91.0, "source_type": "ai_inference",
        "confidence": 0.9, "evidence": ["cross-check"],
    })
    observe(client, loop, "temp", 90.0)  # measured MUST be latest for autonomous action
    res = run(client, loop)
    assert res.errors[0]["ai_only"] is False
    assert res.should_intervene is True
    assert len(res.executed_actions) == 1


def test_A4_ai_only_blocks_even_if_controller_wants_to_act(client):
    """第二道闸：即便控制器强行产出提案，engine 也会拦掉。"""
    from fcc.controllers import ControllerOutput
    from fcc.models import ActionProposal

    class RogueController:
        STATUS = "implemented"
        KIND = "rule"

        def __init__(self, params=None):
            self.params = params or {}

        def propose(self, ctx):
            return ControllerOutput(proposals=[ActionProposal(
                action_id="rogue", action_type="set_value", target="control_input",
                params={"value": 10.0}, source_type="measured",
                origin_detail="rogue", goal_id="g1", proposed_value=10.0,
            )])

    data = cfg()
    loop = new_loop(client, data)
    client.engine.register_controller("rogue", RogueController)
    client.update_config(loop, {"controller": {"kind": "rogue", "params": {}}})
    client.submit_observation(loop, {
        "metric_id": "temp", "value": 90.0, "source_type": "ai_inference",
        "confidence": 0.9, "evidence": ["x"],
    })
    res = run(client, loop)
    assert res.executed_actions == []
    assert any("ai_only_deviation_blocked" in g for g in res.guard_events)
    assert [a["status"] for a in client.store.list_actions(loop) if a["goal_id"] == "g1"] == []


# ---------------------------------------------------------------- 规则 B


def _ai_loop(client, **actuator_over):
    data = cfg()
    data["controller"] = {
        "kind": "ai_assisted",
        "params": {"static_suggestions": [{
            "value": 64.0, "confidence": 0.8, "evidence": ["log-a", "log-b"],
            "risk_level": "medium", "reason": "模型建议上调",
            "goal_id": "g1",
        }]},
        "strategy_version": 1,
    }
    data["actuator"].update(actuator_over)
    loop = new_loop(client, data)
    observe(client, loop, "temp", 40.0)
    return loop


def test_A5_ai_action_downgraded_from_auto(client):
    """auto 模式 + ai_inference 动作 => 强制 require_approval（A5 红线）。"""
    loop = _ai_loop(client, permission_mode="auto", allow_ai_auto=False)
    res = run(client, loop)

    actions = client.store.list_actions(loop)
    assert len(actions) == 1
    assert actions[0]["status"] == "awaiting_approval"
    assert actions[0]["required_approval"] is True
    assert actions[0]["source_type"] == "ai_inference"
    assert "ai_action_downgraded" in actions[0]["decision_reason"]
    assert any("[FCC_GUARD]" in g for g in res.guard_events)
    assert any("[FCC_GUARD]" in str(e["payload"].get("events", ""))
               for e in _logs(client, loop, "guard"))
    assert res.executed_actions[0]["executed"] is False


def test_A5_ai_action_exception_requires_all_three_conditions(client):
    loop = _ai_loop(client, permission_mode="auto", allow_ai_auto=True, )
    client.update_config(loop, {"controller": {"kind": "ai_assisted", "params": {
        "static_suggestions": [{
            "value": 64.0, "confidence": 0.8, "evidence": ["log-a"],
            "risk_level": "low", "reason": "低风险微调", "goal_id": "g1",
            "rollback_method": "restore_previous_value",
        }]},
        "strategy_version": 1}})
    res = run(client, loop)
    actions = client.store.list_actions(loop)
    assert actions[0]["status"] == "pending_verification"
    assert any("ai_action_auto_exception" in g for g in res.guard_events)


def test_A5_exception_fails_when_rollback_missing(client):
    loop = _ai_loop(client, permission_mode="auto", allow_ai_auto=True)
    client.update_config(loop, {"controller": {"kind": "ai_assisted", "params": {
        "static_suggestions": [{
            "value": 64.0, "confidence": 0.8, "evidence": ["log-a"],
            "risk_level": "low", "reason": "缺回滚方法", "goal_id": "g1",
        }]},
        "strategy_version": 1}})
    run(client, loop)
    actions = client.store.list_actions(loop)
    assert actions[0]["status"] == "awaiting_approval"   # 三个条件缺一不可


def test_ai_controller_forces_source_type(client):
    """控制器不能把 ai_inference 伪装成 measured。"""
    from fcc.controllers import AIAssistedController, ControllerContext
    from fcc.models import ErrorRecord, GoalSpec, Setpoint

    ctrl = AIAssistedController({"static_suggestions": [{
        "value": 1.0, "confidence": 0.9, "evidence": ["e"],
        "source_type": "measured",  # 试图覆盖
        "goal_id": "g1"}]})
    goal = GoalSpec(goal_id="g1", target_variable="temp",
                    setpoint=Setpoint(value=1.0), tolerance=0.1)
    ctx = ControllerContext(loop_id="x", cycle_seq=1, goals=[goal],
                            errors=[ErrorRecord(goal_id="g1", target_variable="temp",
                                                direction="approach")],
                            measurements={"temp": 0.0})
    out = ctrl.propose(ctx)
    assert out.proposals[0].source_type == "ai_inference"


def test_ai_suggestion_without_evidence_is_dropped(client):
    from fcc.controllers import AIAssistedController, ControllerContext
    from fcc.models import ErrorRecord, GoalSpec, Setpoint

    ctrl = AIAssistedController({"static_suggestions": [
        {"value": 1.0, "confidence": 0.9, "goal_id": "g1"}]})
    goal = GoalSpec(goal_id="g1", target_variable="temp",
                    setpoint=Setpoint(value=1.0), tolerance=0.1)
    ctx = ControllerContext(loop_id="x", cycle_seq=1, goals=[goal],
                            errors=[ErrorRecord(goal_id="g1", target_variable="temp",
                                                direction="approach")],
                            measurements={"temp": 0.0})
    out = ctrl.propose(ctx)
    assert out.proposals == []
    assert any("evidence" in n for n in out.notes)


# ---------------------------------------------------------------- 规则 C


def test_A6_status_is_pending_verification_right_after_execution(client):
    """执行后立刻查状态必须是 pending_verification（A6 红线）。"""
    loop = new_loop(client)
    observe(client, loop, "temp", 10.0)
    res = run(client, loop)
    assert res.executed_actions[0]["status"] == "pending_verification"

    action = client.store.list_actions(loop)[0]
    assert action["status"] == "pending_verification"
    assert action["verification_status"] == "pending_verification"
    assert action["executed_at"] is not None
    assert res.verification_status == "pending_verification"

    report = client.get_verification_report(loop)
    assert report["counts"]["improved"] == 0   # 还没验证，不许有任何"成功"记账


def test_A6_inconclusive_not_counted_as_improved(client):
    loop = new_loop(client)
    observe(client, loop, "temp", 10.0)
    run(client, loop)                     # 执行，产生 pending
    # 下一轮不给新观测 -> hold_last，值没变
    res2 = run(client, loop)
    verdicts = [v["verdict"] for v in res2.verifications]
    assert verdicts and verdicts[0] in ("unchanged", "inconclusive")
    assert "improved" not in verdicts


def test_A6_failed_action_marked_failed_not_success(client):
    from fcc.simplant import SimulatedPlant

    plant = SimulatedPlant(tau=3.0, dead_time=0.0, noise_sigma=0.0, x0=0.0)
    data = cfg()
    data["actuator"]["adapter"] = "plant_x"
    data["actuator"]["max_retries"] = 1
    loop = new_loop(client, data)
    client.register_actuator("plant_x", plant)
    observe(client, loop, "temp", 10.0)
    plant.inject_failures(5)
    res = run(client, loop)
    action = client.store.list_actions(loop)[0]
    assert action["status"] == "failed"
    assert action["verification_status"] == "failed"
    assert res.executed_actions[0]["executed"] is False


def test_cycle_without_action_is_not_reported_as_success(client):
    data = cfg()
    data["controller"]["kind"] = "monitor"
    loop = new_loop(client, data)
    observe(client, loop, "temp", 10.0)
    res = run(client, loop)
    assert res.executed_actions == []
    assert res.verification_status == "no_action"


@pytest.mark.parametrize("source_type", ["measured", "human_input"])
def test_non_ai_sources_are_ground_truth(client, source_type):
    loop = new_loop(client)
    observe(client, loop, "temp", 20.0, source_type=source_type)
    rec = client.store.latest_observation(loop, "temp")
    assert rec.is_ground_truth is True
    assert ObservationIn.model_validate(
        {"metric_id": "temp", "value": 1.0, "source_type": source_type}
    ).is_ground_truth is True
