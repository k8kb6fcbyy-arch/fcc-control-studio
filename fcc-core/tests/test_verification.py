"""Verification：五态判定（A11）+ 验证延迟 + 噪声带。"""

from __future__ import annotations

import pytest

from conftest import cfg, new_loop, observe, run
from fcc import util
from fcc.models import GoalSpec, ObservationRecord, ObservationSpec, Setpoint
from fcc.observations import Snapshot
from fcc.verification import Verifier

GOAL = GoalSpec(goal_id="g1", target_variable="temp",
                setpoint=Setpoint(value=50.0), tolerance=1.0)
SPEC = ObservationSpec(metric_id="temp", freshness_limit=600)


def _snap(value: float | None, source: str = "measured", stale: bool = False) -> Snapshot:
    if value is None:
        return Snapshot(metric_id="temp", spec=SPEC, record=None, value=None,
                        source_type="none", missing=True)
    rec = ObservationRecord(loop_id="L", metric_id="temp", value=value,
                            source_type=source,  # type: ignore[arg-type]
                            confidence=1.0, evidence=[], is_ground_truth=(source != "ai_inference"),
                            ts=util.utcnow_iso())
    return Snapshot(metric_id="temp", spec=SPEC, record=rec, value=value,
                    source_type=source, stale=stale)


def _action(status: str = "pending_verification", baseline: float | None = 20.0) -> dict:
    return {
        "id": 1, "status": status, "goal_id": "g1",
        "verification_status": None,
        "result": {"baseline": {"value": baseline, "error": 50 - baseline}} if baseline is not None else {},
    }


def _verifier(client, noise: float | None = None) -> Verifier:
    v = Verifier(client.store, "L", None)  # type: ignore[arg-type]
    if noise is not None:
        v._noise_estimate = lambda metric: noise  # type: ignore[assignment]
    return v


def test_verdict_improved(client):
    rec = _verifier(client).judge(_action(baseline=20.0), GOAL, _snap(40.0))
    assert rec.verdict == "improved"
    assert rec.delta["abs_error_after"] < rec.delta["abs_error_before"]


def test_verdict_worsened(client):
    rec = _verifier(client).judge(_action(baseline=45.0), GOAL, _snap(20.0))
    assert rec.verdict == "worsened"


def test_verdict_unchanged_by_noise_band(client):
    rec = _verifier(client, noise=1.0).judge(_action(baseline=40.0), GOAL, _snap(40.5))
    assert rec.verdict == "unchanged"
    assert "噪声带" in " ".join(rec.notes)


def test_verdict_failed_when_action_failed(client):
    rec = _verifier(client).judge(_action(status="failed"), GOAL, _snap(40.0))
    assert rec.verdict == "failed"


def test_verdict_inconclusive_when_no_observation(client):
    rec = _verifier(client).judge(_action(), GOAL, _snap(None))
    assert rec.verdict == "inconclusive"


def test_verdict_inconclusive_when_stale(client):
    rec = _verifier(client).judge(_action(), GOAL, _snap(40.0, stale=True))
    assert rec.verdict == "inconclusive"


def test_verdict_inconclusive_when_only_ai(client):
    rec = _verifier(client).judge(_action(), GOAL, _snap(40.0, source="ai_inference"))
    assert rec.verdict == "inconclusive"
    assert "AI 不能给动作作证" in " ".join(rec.notes)


def test_verdict_inconclusive_without_baseline(client):
    rec = _verifier(client).judge(_action(baseline=None), GOAL, _snap(40.0))
    assert rec.verdict == "inconclusive"


def test_all_five_verdicts_reachable():
    """A11：五个 verdict 都能被构造出来（上面每条一个），这里做一次集合核对。"""
    from fcc.verification import VERDICTS

    assert set(VERDICTS) == {"improved", "unchanged", "worsened", "failed",
                             "inconclusive"}


# ------------------------------------------------------------ 端到端验证


def test_verification_delay_of_one_cycle(client):
    loop = new_loop(client)
    observe(client, loop, "temp", 10.0)
    r1 = run(client, loop)
    assert r1.verifications == []          # 第一轮不判：避免同轮假因果
    client.submit_observation(loop, {"metric_id": "temp", "value": 40.0,
                                     "source_type": "measured"})
    r2 = run(client, loop)
    assert len(r2.verifications) == 1
    assert r2.verifications[0]["verdict"] in ("improved", "unchanged", "worsened")


def test_verification_never_judges_inside_same_cycle(client):
    """delay=0 也至少等到下一轮：同一轮里"执行后的新观测"根本不存在。"""
    data = cfg()
    data["verification"]["delay_cycles"] = 0
    loop = new_loop(client, data)
    observe(client, loop, "temp", 10.0)
    r1 = run(client, loop)
    assert r1.verifications == []
    observe(client, loop, "temp", 40.0)
    r2 = run(client, loop)
    assert len(r2.verifications) == 1


def test_inconclusive_keeps_action_pending(client):
    """执行完却拿不到新观测 -> inconclusive，动作必须继续挂在 pending，不许当成功。"""
    from fcc.simplant import SimulatedPlant

    plant = SimulatedPlant(tau=2.0, dead_time=0.0, noise_sigma=0.0, x0=0.0)
    data = cfg()
    data["actuator"]["adapter"] = "blind"
    loop = new_loop(client, data)
    client.register_actuator("blind", plant)
    observe(client, loop, "temp", 10.0)
    run(client, loop)

    # 第二轮前把有效期改到 1 毫秒且策略改成 skip -> 拿不到可用观测
    blind_spec = dict(data["observations"][0])
    blind_spec.update({"freshness_limit": 0.001, "missing_policy": "skip"})
    client.update_config(loop, {"observations": [blind_spec]})
    r2 = run(client, loop)

    assert [v["verdict"] for v in r2.verifications] == ["inconclusive"]
    action = client.store.list_actions(loop)[0]
    assert action["status"] == "pending_verification"
    assert action["verification_status"] == "inconclusive"


def test_verification_report_counts(client):
    loop = new_loop(client)
    observe(client, loop, "temp", 10.0)
    run(client, loop)
    observe(client, loop, "temp", 40.0)
    run(client, loop)
    report = client.get_verification_report(loop)
    assert report["total"] == 1
    assert sum(report["counts"].values()) == 1
    assert report["improved_rate"] >= 0.0
