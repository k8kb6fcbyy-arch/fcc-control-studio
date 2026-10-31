"""Comparator：误差、区间目标、速率、累计、振荡检测。"""

from __future__ import annotations

import pytest

from conftest import cfg, new_loop, observe, patch, run
from fcc.comparator import count_sign_flips
from fcc.goals import GoalManager, setpoint_error, within_tolerance
from fcc.models import GoalSpec, Setpoint


def test_sign_flip_counter():
    assert count_sign_flips([1, -1, 1, -1]) == 3
    assert count_sign_flips([1, 2, 3]) == 0
    assert count_sign_flips([0, 1, 0, -1]) == 1


def test_setpoint_error_sign_convention():
    sp = Setpoint(value=50.0)
    assert setpoint_error(sp, 40.0) == pytest.approx(10.0)    # 正 = 需要往上推
    assert setpoint_error(sp, 60.0) == pytest.approx(-10.0)


def test_range_setpoint_error():
    sp = Setpoint(kind="range", low=48, high=52)
    assert setpoint_error(sp, 50) == 0
    assert setpoint_error(sp, 45) == pytest.approx(3.0)     # 低于下界，需要上推
    assert setpoint_error(sp, 55) == pytest.approx(-3.0)    # 高于上界，需要下推
    assert within_tolerance(sp, 50, 0.0) is True
    assert within_tolerance(sp, 53, 0.0) is False


def test_value_setpoint_tolerance():
    sp = Setpoint(value=50.0)
    assert within_tolerance(sp, 50.9, 1.0) is True
    assert within_tolerance(sp, 51.1, 1.0) is False


def test_error_record_has_uncertainty_field(client):
    loop = new_loop(client)
    observe(client, loop, "temp", 10.0)
    res = run(client, loop)
    assert "uncertainty" in res.errors[0]
    assert res.errors[0]["uncertainty"] == 0.0     # 纯 measured：无 AI 不确定性


def test_rate_of_change_and_cumulative(client):
    loop = new_loop(client)
    for v in (10.0, 20.0, 30.0):
        observe(client, loop, "temp", v)
        run(client, loop)
    cycle = client.get_history(loop, 5)[-1]
    err = cycle["errors"][0]
    assert err["rate_of_change"] is not None
    assert err["cumulative_error"] != 0.0


def test_oscillation_detected_after_sign_flips(client):
    """持续来回摆动 -> oscillation_detected。"""
    loop = new_loop(client)
    seq = [10, 90, 10, 90, 10, 90, 10, 90]
    for v in seq:
        observe(client, loop, "temp", v)
        res = run(client, loop)
    err = res.errors[0]
    assert err["oscillation_detected"] is True
    assert "oscillation_detected" in " ".join(err["notes"])
    assert any(a.startswith("oscillation:") for a in res.anomalies)


def test_stable_series_is_not_oscillation(client):
    loop = new_loop(client)
    for v in (10, 20, 30, 40, 45, 48, 49, 50):
        observe(client, loop, "temp", v)
        res = run(client, loop)
    assert res.errors[0]["oscillation_detected"] is False


def test_over_correction_detected_when_amplitude_grows(client):
    from fcc.comparator import Comparator

    errs = [1.0, 3.0, 7.0, 15.0]
    assert Comparator._amplitude_growing(errs) is True
    assert Comparator._amplitude_growing([15.0, 7.0, 3.0, 1.0]) is False


def test_multi_goal_independent_errors(client):
    data = cfg()
    data["observations"].append({"metric_id": "humid", "source": "manual",
                                 "freshness_limit": 600})
    data["goals"].append({"goal_id": "g2", "target_variable": "humid",
                          "direction": "decrease",
                          "setpoint": {"kind": "value", "value": 40.0},
                          "tolerance": 5.0, "priority": 2})
    loop = new_loop(client, data)
    observe(client, loop, "temp", 10.0)
    observe(client, loop, "humid", 60.0)
    res = run(client, loop)
    assert {e["goal_id"] for e in res.errors} == {"g1", "g2"}
    temp_err = next(e for e in res.errors if e["goal_id"] == "g1")
    humid_err = next(e for e in res.errors if e["goal_id"] == "g2")
    assert temp_err["error"] == pytest.approx(40.0)
    assert humid_err["error"] == pytest.approx(-20.0)   # 需要下推
    assert res.target["g2"]["priority"] == 2


def test_priority_order_respected(client):
    data = cfg()
    data["observations"].append({"metric_id": "humid", "source": "manual",
                                 "freshness_limit": 600})
    data["goals"][0]["priority"] = 7
    data["goals"].append({"goal_id": "g2", "target_variable": "humid",
                          "direction": "decrease",
                          "setpoint": {"kind": "value", "value": 40.0},
                          "tolerance": 5.0, "priority": 2})
    gm = GoalManager([GoalSpec.model_validate(g) for g in data["goals"]])
    assert gm.sorted_by_priority()[0].goal_id == "g2"


def test_missing_observation_makes_goal_missing(client):
    loop = new_loop(client)
    res = run(client, loop)
    err = res.errors[0]
    assert err["missing"] is True
    assert err["uncertainty"] == 1.0
    assert res.should_intervene is False


def test_hold_last_in_band_no_intervention(client):
    import datetime as dt

    from fcc import util

    data = patch(cfg(), "observations.0.freshness_limit", 1.0)
    loop = new_loop(client, data)
    old = util.to_iso(util.utcnow() - dt.timedelta(seconds=30))
    observe(client, loop, "temp", 50.2, ts=old)   # 在容差带内且已 stale
    res = run(client, loop)
    assert res.errors[0]["stale"] is True
    assert res.should_intervene is False
