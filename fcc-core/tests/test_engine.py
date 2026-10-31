"""Engine：完整周期、四项配置生效、审计可还原、学习统计、生命周期。"""

from __future__ import annotations

import pytest

from conftest import cfg, new_loop, observe, patch, run


def test_A2_full_cycle_record_has_all_fields(client):
    loop = new_loop(client)
    observe(client, loop, "temp", 10.0)
    res = run(client, loop)

    cycle = client.store.get_cycle(res.cycle_id)
    required = [
        "id", "loop_id", "started_at", "finished_at", "target", "observations",
        "estimated_state", "errors", "uncertainty", "anomalies", "proposed_actions",
        "executed_actions", "expected_outcome", "verification_status",
        "next_evaluation_at", "human_review_required", "should_intervene", "audit_ref",
    ]
    for key in required:
        assert key in cycle, f"ctrl_cycle 缺字段 {key}"
    assert cycle["finished_at"] is not None
    assert cycle["target"]["g1"]["nominal"] == 50.0
    assert cycle["observations"]["temp"]["value"] == 10.0
    assert cycle["errors"][0]["error"] == pytest.approx(40.0)
    assert cycle["proposed_actions"]
    assert cycle["audit_ref"]


def test_A3_setpoint_change_takes_effect(client):
    loop = new_loop(client)
    observe(client, loop, "temp", 40.0)
    assert run(client, loop).errors[0]["error"] == pytest.approx(10.0)

    client.update_config(loop, {"goals": [{
        "goal_id": "g1", "target_variable": "temp", "direction": "approach",
        "setpoint": {"kind": "value", "value": 60.0}, "tolerance": 1.0}]})
    assert run(client, loop).errors[0]["error"] == pytest.approx(20.0)


def test_A3_controller_change_takes_effect(client):
    loop = new_loop(client)
    observe(client, loop, "temp", 10.0)
    assert run(client, loop).proposed_actions[0]["origin_detail"] == "threshold"

    client.update_config(loop, {"controller": {"kind": "monitor", "params": {},
                                               "strategy_version": 2}})
    res = run(client, loop)
    assert res.proposed_actions == []


def test_A3_frequency_change_takes_effect(client):
    loop = new_loop(client)
    assert run(client, loop).next_evaluation_at is None     # manual 模式

    client.update_config(loop, {"scheduler": {"mode": "fixed", "interval_seconds": 45}})
    nxt = run(client, loop).next_evaluation_at
    assert nxt is not None


def test_A3_sampling_interval_and_freshness_change(client):
    loop = new_loop(client)
    spec = dict(cfg()["observations"][0])
    spec["freshness_limit"] = 0.001
    spec["missing_policy"] = "skip"
    client.update_config(loop, {"observations": [spec]})
    res = run(client, loop)
    assert res.errors[0]["missing"] is True


def test_A7_audit_chain_is_reconstructable(client):
    loop = new_loop(client)
    observe(client, loop, "temp", 10.0)
    res = run(client, loop)
    observe(client, loop, "temp", 45.0)
    run(client, loop)

    data = client.engine.audit_chain(loop, res.cycle_id)
    kinds = [e["kind"] for e in data["chain"]]
    for expected in ("cycle_started", "error_computed", "action_proposed",
                     "action_executed", "cycle_finished"):
        assert expected in kinds, f"审计链缺 {expected}"
    finished = next(e for e in data["chain"] if e["kind"] == "cycle_finished")
    assert finished["payload"]["observations"]["temp"]["value"] == 10.0
    assert finished["payload"]["errors"][0]["error"] == pytest.approx(40.0)

    executed = next(e for e in data["chain"] if e["kind"] == "action_executed")
    assert executed["payload"]["before"]["value"] == 70.0
    assert executed["payload"]["after"]["applied_value"] == 70.0
    assert data["actions"][0]["status"] in ("pending_verification", "verified")


def test_learning_stats_counts_verdicts(client):
    loop = new_loop(client)
    observe(client, loop, "temp", 10.0)
    run(client, loop)
    observe(client, loop, "temp", 45.0)
    run(client, loop)
    observe(client, loop, "temp", 20.0)
    run(client, loop)

    stats = client.engine.get_state(loop)["strategy_stats"]
    versions = stats["strategy_versions"]
    assert versions and versions[0]["strategy_version"] == 1
    assert versions[0]["interventions"] >= 2
    assert versions[0]["improved"] + versions[0]["worsened"] + \
        versions[0]["unchanged"] + versions[0]["inconclusive"] + \
        versions[0]["failed"] + versions[0]["pending"] == versions[0]["interventions"]


def test_learning_auto_upgrade_is_not_implemented(client):
    loop = new_loop(client)
    from fcc.learning import LearningManager

    lm = LearningManager(client.store, loop)
    assert lm.propose_upgrade()["status"] == "not_implemented"
    assert lm.system_identification()["status"] == "not_implemented"
    assert lm.daily_digest()["status"] == "not_implemented"


def test_loop_lifecycle(client):
    loop = new_loop(client)
    assert client.store.get_loop(loop)["enabled"] == 0
    client.start_loop(loop)
    assert client.store.get_loop(loop)["enabled"] == 1
    client.pause_loop(loop)
    assert client.store.get_loop(loop)["enabled"] == 0
    client.resume_loop(loop)
    assert client.store.get_loop(loop)["enabled"] == 1
    client.stop_loop(loop)
    assert client.store.get_loop(loop)["enabled"] == 0

    kinds = [e["kind"] for e in client.store.list_audit(loop, 50)]
    assert "loop_state" in kinds


def test_unknown_loop_raises(client):
    from fcc.engine import LoopNotFound

    with pytest.raises(LoopNotFound):
        client.get_state("loop_not_exist")


def test_unknown_controller_rejected(client):
    data = cfg()
    data["controller"]["kind"] = "nonsense"
    with pytest.raises(ValueError):
        new_loop(client, data)


def test_custom_controller_can_be_registered(client):
    from fcc.controllers import ControllerOutput

    class Always:
        STATUS = "implemented"
        KIND = "custom"

        def __init__(self, params=None):
            self.params = params or {}

        def propose(self, ctx):
            return ControllerOutput(notes=["custom controller ran"])

    client.engine.register_controller("always", Always)
    data = cfg()
    data["controller"]["kind"] = "always"
    loop = new_loop(client, data)
    observe(client, loop, "temp", 10.0)
    res = run(client, loop)
    assert res.executed_actions == []


def test_range_goal_cycle(client):
    data = patch(cfg(), "goals.0.setpoint", {"kind": "range", "low": 48.0, "high": 52.0})
    data["goals"][0]["tolerance"] = 0.0
    loop = new_loop(client, data)
    observe(client, loop, "temp", 50.0)
    res = run(client, loop)
    assert res.errors[0]["error"] == 0.0
    assert res.should_intervene is False


def test_loop_concurrency_guard(client):
    loop = new_loop(client)
    lock = client.engine._loop_lock(loop)
    lock.acquire()
    try:
        with pytest.raises(RuntimeError):
            client.run_cycle(loop)
    finally:
        lock.release()


def test_delete_loop_cleans_rows(client):
    loop = new_loop(client)
    observe(client, loop, "temp", 10.0)
    run(client, loop)
    client.delete_loop(loop)
    assert client.store.get_loop(loop) is None
    assert client.store.list_cycles(loop) == []
    assert client.store.list_actions(loop) == []
