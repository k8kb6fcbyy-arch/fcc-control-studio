"""Actuator：权限模式、幂等、范围限制、重试、回滚。"""

from __future__ import annotations

import pytest

from conftest import cfg, new_loop, observe, run
from fcc.actuator import Actuator, ManualAdapter
from fcc.models import ActionProposal, ActuatorConfig


def _proposal(value: float, source_type: str = "measured", risk: str = "medium",
              rollback: str | None = "restore_previous_value") -> ActionProposal:
    return ActionProposal(action_id="a1", action_type="set_value", target="control_input",
                          params={"value": value}, source_type=source_type,
                          risk_level=risk, rollback_method=rollback, goal_id="g1",
                          proposed_value=value)


def test_suggest_only_never_executes(client):
    data = cfg()
    data["actuator"]["permission_mode"] = "suggest_only"
    loop = new_loop(client, data)
    observe(client, loop, "temp", 10.0)
    res = run(client, loop)
    assert res.executed_actions[0]["status"] == "proposed"
    assert client.store.list_actions(loop)[0]["status"] == "proposed"


def test_range_limit_blocks_execution(client):
    """规则控制器直接给出超范围值 -> 执行器拒绝，而不是偷偷改小。"""
    data = cfg()
    data["actuator"]["action_range"] = [0, 30]
    data["controller"] = {"kind": "rule", "params": {"rules": [{
        "id": "r1", "if": {"metric": "temp", "op": "lt", "value": 50},
        "action": {"value": 500.0, "goal_id": "g1"}, "reason": "超范围"}]},
        "strategy_version": 1}
    loop = new_loop(client, data)
    observe(client, loop, "temp", 10.0)
    run(client, loop)
    action = client.store.list_actions(loop)[0]
    assert action["status"] == "failed"
    assert "超出允许范围" in (action["result"] or {}).get("error", "")


def test_threshold_clamps_to_action_range(client):
    """阈值控制器自身也会夹到范围内（两道闸，第一道是软的）。"""
    data = cfg()
    data["actuator"]["action_range"] = [0, 30]
    loop = new_loop(client, data)
    observe(client, loop, "temp", 10.0)   # tracking 律算得 70，被夹到 30
    run(client, loop)
    action = client.store.list_actions(loop)[0]
    assert action["params"]["value"] == 30.0
    assert action["status"] == "pending_verification"


def test_missing_adapter_fails_cleanly(client):
    data = cfg()
    data["actuator"]["adapter"] = "not-registered"
    loop = new_loop(client, data)
    observe(client, loop, "temp", 10.0)
    run(client, loop)
    action = client.store.list_actions(loop)[0]
    assert action["status"] == "failed"


def test_retry_then_success(client):
    from fcc.simplant import SimulatedPlant

    plant = SimulatedPlant(tau=2.0, dead_time=0.0, noise_sigma=0.0, x0=0.0)
    data = cfg()
    data["actuator"].update({"adapter": "flaky", "max_retries": 2})
    loop = new_loop(client, data)
    client.register_actuator("flaky", plant)
    observe(client, loop, "temp", 10.0)
    plant.inject_failures(2)              # 前两次抛异常，第三次成功
    res = run(client, loop)
    action = client.store.list_actions(loop)[0]
    assert action["status"] == "pending_verification"
    assert (action["result"] or {}).get("attempts") == 3
    assert res.executed_actions[0]["executed"] is True


def test_idempotency_key_dedupes_action(client):
    """A8：同一 idempotency_key 两次只产生一个 action。"""
    loop = new_loop(client)
    observe(client, loop, "temp", 10.0)
    first = client.submit_human_feedback(loop, {
        "value": 30.0, "goal_id": "g1", "idempotency_key": "evt-2026-001"})
    second = client.submit_human_feedback(loop, {
        "value": 30.0, "goal_id": "g1", "idempotency_key": "evt-2026-001"})
    assert first["ok"] is True
    assert second["ok"] is False and "幂等" in second["error"]
    assert len(client.store.list_actions(loop)) == 1


def test_duplicate_event_does_not_create_second_cycle(client):
    loop = new_loop(client)
    observe(client, loop, "temp", 10.0)
    r1 = client.run_cycle(loop, event_key="evt-A")
    r2 = client.run_cycle(loop, event_key="evt-A")
    assert r1.cycle_id == r2.cycle_id
    assert client.store.cycle_count(loop) == 1


def test_rollback_restores_previous_value(client):
    from fcc.simplant import SimulatedPlant

    plant = SimulatedPlant(tau=2.0, dead_time=0.0, noise_sigma=0.0, x0=0.0, u0=5.0)
    data = cfg()
    data["actuator"]["adapter"] = "rb_plant"
    loop = new_loop(client, data)
    client.register_actuator("rb_plant", plant)
    observe(client, loop, "temp", 10.0)
    run(client, loop)
    action = client.store.list_actions(loop)[0]
    assert plant.u != 5.0
    res = client.rollback_action(action["id"])
    assert res["ok"] is True
    assert plant.u == pytest.approx(5.0)
    assert client.store.get_action(action["id"])["status"] == "rolled_back"


def test_rollback_refused_without_method(client):
    loop = new_loop(client)
    observe(client, loop, "temp", 10.0)
    res = client.submit_human_feedback(loop, {
        "value": 30.0, "goal_id": "g1", "idempotency_key": "k-norb"})
    aid = res["action"]["id"]
    client.store.update_action(aid, rollback_method=None)
    out = client.rollback_action(aid)
    assert out["ok"] is False and "未登记回滚方法" in out["error"]


def test_decide_downgrades_ai_in_auto(client):
    act = Actuator(client.store, "L", ActuatorConfig(permission_mode="auto"))
    d = act.decide(_proposal(1.0, source_type="ai_inference", risk="medium"))
    assert d.mode == "require_approval"
    assert d.required_approval is True
    assert any("ai_action_downgraded" in g for g in d.guard_events)


def test_decide_ai_exception_needs_low_risk_and_rollback(client):
    act = Actuator(client.store, "L",
                   ActuatorConfig(permission_mode="auto", allow_ai_auto=True))
    ok = act.decide(_proposal(1.0, source_type="ai_inference", risk="low",
                              rollback="restore_previous_value"))
    assert ok.mode == "auto"
    high = act.decide(_proposal(1.0, source_type="ai_inference", risk="high",
                                rollback="restore_previous_value"))
    assert high.mode == "require_approval"


def test_manual_adapter_tracks_state():
    adapter = ManualAdapter()
    assert adapter.state()["current_value"] is None
    adapter.apply({"params": {"value": 12.0}})
    assert adapter.state()["current_value"] == 12.0
    adapter.rollback({}, {"previous_value": None})
    assert adapter.state()["current_value"] is None


def test_adapter_timeout_is_reported(client):
    import time

    class SlowAdapter:
        def apply(self, action):
            time.sleep(2.0)
            return {"ok": True}

        def state(self):
            return {}

        def rollback(self, action, result):
            return {"ok": True}

    data = cfg()
    data["actuator"].update({"adapter": "slow", "timeout_seconds": 0.2, "max_retries": 0})
    loop = new_loop(client, data)
    client.register_actuator("slow", SlowAdapter())
    observe(client, loop, "temp", 10.0)
    run(client, loop)
    action = client.store.list_actions(loop)[0]
    assert action["status"] == "outcome_unknown"
    assert "超时" in (action["result"] or {}).get("error", "")


def test_approval_flow_executes_only_after_approve(client):
    data = cfg()
    data["actuator"]["permission_mode"] = "require_approval"
    loop = new_loop(client, data)
    observe(client, loop, "temp", 10.0)
    run(client, loop)
    action = client.store.list_actions(loop)[0]
    assert action["status"] == "awaiting_approval"
    out = client.approve_action(action["id"], "tester")
    assert out["ok"] is True
    assert out["action"]["status"] == "pending_verification"
    assert out["action"]["approval_by"] == "tester"


def test_reject_flow(client):
    data = cfg()
    data["actuator"]["permission_mode"] = "require_approval"
    loop = new_loop(client, data)
    observe(client, loop, "temp", 10.0)
    run(client, loop)
    action = client.store.list_actions(loop)[0]
    out = client.reject_action(action["id"], "tester", "不需要调")
    assert out["ok"] is True
    assert out["action"]["status"] == "rejected"
    kinds = [e["kind"] for e in client.store.list_audit(loop, 100)]
    assert "action_rejected" in kinds
