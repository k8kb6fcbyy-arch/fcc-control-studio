"""Observation Manager：注册、有效期、范围、异常值、缺失策略。"""

from __future__ import annotations

import datetime as dt

import pytest

from conftest import cfg, new_loop, observe, patch
from fcc import util
from fcc.models import ObservationSpec
from fcc.observations import ObservationManager, ObservationRejected
from fcc.store import Store


def test_ingest_and_flags(client):
    loop = new_loop(client)
    r = observe(client, loop, "temp", 41.5)
    assert r["ok"] is True
    snap = ObservationManager(client.store, loop, [
        ObservationSpec(metric_id="temp")]).snapshot("temp")
    assert snap.value == 41.5
    assert snap.source_type == "measured"
    assert snap.stale is False


def test_unregistered_metric_rejected(client):
    loop = new_loop(client)
    with pytest.raises(ObservationRejected):
        observe(client, loop, "humidity", 1.0)


def test_out_of_valid_range_flagged(client):
    data = patch(cfg(), "observations.0.valid_range", [-50, 150])
    loop = new_loop(client, data)
    r = observe(client, loop, "temp", 900.0)
    assert "out_of_range" in r["quality_flags"]


def test_out_of_range_drop_policy_makes_missing(client):
    data = patch(cfg(), "observations.0.valid_range", [0, 100])
    data["observations"][0]["outlier_policy"] = "drop"
    loop = new_loop(client, data)
    observe(client, loop, "temp", 500.0)
    snap = ObservationManager(client.store, loop, [
        ObservationSpec(metric_id="temp", valid_range=(0, 100),
                        outlier_policy="drop")]).snapshot("temp")
    assert snap.missing is True
    assert snap.value is None


def test_three_sigma_outlier_detected(client):
    data = cfg()
    data["observations"][0]["outlier_policy"] = "flag"
    data["observations"][0]["outlier_method"] = "sigma3"
    loop = new_loop(client, data)
    for v in (10.0, 10.2, 9.8, 10.1, 9.9, 10.05):
        observe(client, loop, "temp", v)
    r = observe(client, loop, "temp", 40.0)  # 明显离群
    assert "outlier" in r["quality_flags"]


def test_iqr_outlier_detected(client):
    data = cfg()
    data["observations"][0]["outlier_method"] = "iqr"
    loop = new_loop(client, data)
    for v in (10.0, 10.5, 11.0, 10.2, 9.9, 10.7, 10.1):
        observe(client, loop, "temp", v)
    r = observe(client, loop, "temp", 99.0)
    assert "outlier" in r["quality_flags"]


def test_missing_policy_hold_last(client):
    data = patch(cfg(), "observations.0.freshness_limit", 1.0)
    loop = new_loop(client, data)
    old = util.to_iso(util.utcnow() - dt.timedelta(seconds=30))
    observe(client, loop, "temp", 30.0, ts=old)   # 观测时间戳明确写成 30 秒前
    snap = ObservationManager(client.store, loop, [
        ObservationSpec(metric_id="temp", freshness_limit=1.0,
                        missing_policy="hold_last")]).snapshot("temp")
    assert snap.stale is True
    assert snap.value == 30.0          # 沿用旧值
    assert "held_last" in snap.flags   # 但明确标记


def test_missing_policy_skip_returns_none(client):
    data = patch(cfg(), "observations.0.freshness_limit", 1.0)
    data["observations"][0]["missing_policy"] = "skip"
    loop = new_loop(client, data)
    old = util.to_iso(util.utcnow() - dt.timedelta(seconds=30))
    observe(client, loop, "temp", 30.0, ts=old)
    snap = ObservationManager(client.store, loop, [
        ObservationSpec(metric_id="temp", freshness_limit=1.0,
                        missing_policy="skip")]).snapshot("temp")
    assert snap.missing is True
    assert snap.value is None


def test_no_observation_at_all(client):
    loop = new_loop(client)
    snap = ObservationManager(client.store, loop, [
        ObservationSpec(metric_id="temp")]).snapshot("temp")
    assert snap.missing is True
    assert snap.source_type == "none"


def test_history_is_ordered(client):
    loop = new_loop(client)
    for v in (1.0, 2.0, 3.0):
        observe(client, loop, "temp", v)
    hist = client.store.observation_history(loop, "temp", 10)
    assert [h.value for h in hist] == [1.0, 2.0, 3.0]


def test_rejected_observation_still_audited(client):
    data = patch(cfg(), "observations.0.valid_range", [0, 10])
    data["observations"][0]["outlier_policy"] = "drop"
    loop = new_loop(client, data)
    observe(client, loop, "temp", 99.0)
    kinds = [e["kind"] for e in client.store.list_audit(loop, 50)]
    assert "observation_rejected" in kinds


def test_store_reopen_keeps_observations(tmp_path):
    from fcc import Client

    path = str(tmp_path / "persist.db")
    c1 = Client(path)
    loop = new_loop(c1)
    observe(c1, loop, "temp", 12.0)
    c1.close()

    c2 = Client(path)
    hist = c2.store.observation_history(loop, "temp", 10)
    assert len(hist) == 1 and hist[0].value == 12.0
    c2.close()


def test_observation_in_store_is_finite(client):
    loop = new_loop(client)
    with pytest.raises(Exception):
        observe(client, loop, "temp", float("nan"))
