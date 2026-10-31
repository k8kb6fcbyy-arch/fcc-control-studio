"""五种控制器真实现 + PID/MPC 仅接口。"""

from __future__ import annotations

import pytest

from conftest import cfg, new_loop, observe, run
from fcc.controllers import (
    AIAssistedController,
    BUILTIN,
    ControllerContext,
    HumanInLoopController,
    MPCController,
    MonitorController,
    PIDController,
    RuleController,
    StateSpaceEstimator,
    ThresholdController,
)
from fcc.models import ErrorRecord, GoalSpec, Setpoint


def _ctx(errors=None, measurements=None, current=None, goals=None, prev_in_band=None):
    goal = (goals or [GoalSpec(goal_id="g1", target_variable="temp",
                               setpoint=Setpoint(value=50.0), tolerance=1.0)])[0]
    return ControllerContext(
        loop_id="L", cycle_seq=3, goals=[goal],
        errors=errors or [ErrorRecord(goal_id="g1", target_variable="temp",
                                      direction="approach", error=10.0,
                                      tolerance=1.0, out_of_tolerance=True, value=40.0,
                                      setpoint={"kind": "value", "value": 50.0})],
        measurements=measurements or {"temp": 40.0},
        current_control=current, prev_in_band=prev_in_band or {},
    )


def test_monitor_never_proposes():
    out = MonitorController().propose(_ctx())
    assert out.proposals == []


def test_threshold_proposes_proportional_step():
    out = ThresholdController({"proportional_gain": 0.5, "law": "tracking"}).propose(
        _ctx(current=0.0))
    assert len(out.proposals) == 1
    # tracking: u = sp + K*e = 50 + 0.5*10 = 55
    assert out.proposals[0].proposed_value == pytest.approx(55.0)


def test_threshold_incremental_law():
    out = ThresholdController({"proportional_gain": 0.2, "law": "incremental"}).propose(
        _ctx(current=30.0))
    assert out.proposals[0].proposed_value == pytest.approx(32.0)


def test_threshold_deadband_suppresses():
    err = ErrorRecord(goal_id="g1", target_variable="temp", direction="approach",
                      error=0.4, tolerance=0.1, out_of_tolerance=True, value=49.6)
    out = ThresholdController({"proportional_gain": 0.5, "deadband": 1.0}).propose(
        _ctx(errors=[err]))
    assert out.proposals == []


def test_threshold_hysteresis_holds_when_prev_in_band():
    err = ErrorRecord(goal_id="g1", target_variable="temp", direction="approach",
                      error=1.2, tolerance=1.0, out_of_tolerance=True, value=48.8)
    out = ThresholdController({"proportional_gain": 0.5, "hysteresis": 1.0}).propose(
        _ctx(errors=[err], prev_in_band={"g1": True}))
    assert out.proposals == []


def test_threshold_cooldown():
    err = ErrorRecord(goal_id="g1", target_variable="temp", direction="approach",
                      error=10.0, tolerance=1.0, out_of_tolerance=True)
    ctx = _ctx(errors=[err])
    ctx.last_action_cycle = {"g1": 2}
    ctx.cycle_seq = 3
    out = ThresholdController({"proportional_gain": 0.5, "cooldown_cycles": 2}).propose(ctx)
    assert out.proposals == []


def test_threshold_suppressed_on_oscillation():
    out = ThresholdController({"proportional_gain": 0.5}).propose(_ctx())
    ctx = _ctx()
    ctx.oscillation = True
    assert ThresholdController({"proportional_gain": 0.5}).propose(ctx).proposals == []
    assert out.proposals  # 无振荡时正常出提案


def test_threshold_respects_max_step():
    out = ThresholdController({"proportional_gain": 0.5, "law": "incremental"}).propose(
        _ctx(current=0.0)).proposals[0]
    ctx = _ctx(current=0.0)
    ctx.max_step = 2.0
    out2 = ThresholdController({"proportional_gain": 0.5, "law": "incremental"}).propose(ctx)
    assert out2.proposals[0].proposed_value == pytest.approx(2.0)


def test_threshold_skips_ai_only_error():
    err = ErrorRecord(goal_id="g1", target_variable="temp", direction="approach",
                      error=10.0, tolerance=1.0, out_of_tolerance=True, ai_only=True)
    out = ThresholdController({"proportional_gain": 0.5}).propose(_ctx(errors=[err]))
    assert out.proposals == []
    assert any("FCC_GUARD" in n for n in out.notes)


def test_rule_controller_matches_metric_condition():
    ctrl = RuleController({"rules": [{
        "id": "r1", "if": {"metric": "temp", "op": "gt", "value": 38},
        "action": {"value": 42.0, "risk": "high", "goal_id": "g1"}, "reason": "过热"}]})
    out = ctrl.propose(_ctx())
    assert out.proposals and out.proposals[0].proposed_value == 42.0
    assert out.proposals[0].risk_level == "high"


def test_rule_controller_no_match():
    ctrl = RuleController({"rules": [{
        "id": "r1", "if": {"metric": "temp", "op": "gt", "value": 100},
        "action": {"value": 42.0}}]})
    assert ctrl.propose(_ctx()).proposals == []


def test_human_in_loop_waits_for_human(client):
    data = cfg()
    data["controller"]["kind"] = "human_in_loop"
    loop = new_loop(client, data)
    observe(client, loop, "temp", 10.0)
    res = run(client, loop)
    assert res.executed_actions == []
    assert res.human_review_required is True
    assert client.store.list_actions(loop) == []


def test_human_feedback_creates_human_input_action(client):
    data = cfg()
    data["controller"]["kind"] = "human_in_loop"
    loop = new_loop(client, data)
    observe(client, loop, "temp", 10.0)
    run(client, loop)
    res = client.submit_human_feedback(loop, {"value": 48.0, "goal_id": "g1",
                                              "note": "人工给定"})
    assert res["ok"] is True
    action = client.store.list_actions(loop)[0]
    assert action["source_type"] == "human_input"
    assert action["status"] == "pending_verification"


def test_pid_is_operational():
    pid = PIDController({"kp": 1.0, "ki": 0.1, "kd": 0.01})
    assert pid.STATUS == "implemented"
    assert pid.kp == 1.0 and pid.ki == 0.1 and pid.kd == 0.01
    assert pid.compute(1.0, 1.0) > 0
    out = pid.propose(_ctx())
    assert out.proposals and out.proposals[0].origin_detail == "pid"
    assert out.next_state is not None


def test_mpc_and_statespace_are_operational():
    assert MPCController.STATUS == 'implemented'
    assert StateSpaceEstimator.STATUS == 'implemented'
    with pytest.raises(ValueError, match='model'):
        MPCController().propose(_ctx())
    est = StateSpaceEstimator([[1.0]], [[1.0]], [[1.0]], Q=[[0.1]],
                              R=[[1.0]], x0=[0.0], P0=[[1.0]])
    assert 0 < est.update([1.0], [0.0])[0] < 1.0


def test_builtin_registry_marks_pid_as_implemented():
    assert BUILTIN["pid"].STATUS == "implemented"
    assert BUILTIN["mpc"].STATUS == "implemented"
    assert BUILTIN["threshold"].STATUS == "implemented"


def test_pid_loop_acts_with_verified_measurement(client):
    data = cfg()
    data["controller"] = {"kind": "pid", "params": {"kp": 0.5, "ki": 0.1,
                          "sample_time_seconds": 1.0}, "strategy_version": 1}
    loop = new_loop(client, data)
    observe(client, loop, "temp", 10.0)
    result = run(client, loop)
    assert result.executed_actions
    assert result.verification_status == "pending_verification"
    state = client.store.get_controller_state(loop, "pid", 1)
    assert state["integral"] > 0


def test_ai_controller_without_advisor_is_honest():
    out = AIAssistedController({}).propose(_ctx())
    assert out.proposals == []
    assert any("未注册 AI advisor" in n for n in out.notes)


def test_human_in_loop_notes_suggestion():
    ctrl = HumanInLoopController({"proportional_gain": 0.5})
    out = ctrl.propose(_ctx(current=10.0))
    assert out.awaiting_human is True
    assert out.proposals == []
    assert any("待人工决策" in n for n in out.notes)
