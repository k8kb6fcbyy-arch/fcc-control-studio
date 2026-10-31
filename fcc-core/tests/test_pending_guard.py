"""需求 §7「不得在尚未观察到前次动作效果时无限叠加新动作」+ 验证分布对外可见。

对应审核缺陷 1（verdict 算出来但对外永远 pending）与缺陷 2（每轮无条件叠动作）。
"""

from __future__ import annotations

import sqlite3

import pytest

from conftest import cfg, new_loop, observe, run
from fcc import Client
from fcc.engine import Engine
from fcc.store import SCHEMA, Store
from fcc.verification import SUMMARY_KEYS

GUARD = "[FCC_GUARD]"
SUMMARY_SHAPE = {"improved", "unchanged", "worsened", "inconclusive", "failed", "pending"}


def _loop(client):
    data = cfg()
    data["goals"][0]["setpoint"] = {"kind": "value", "value": 80.0}
    data["goals"][0]["tolerance"] = 0.5
    return new_loop(client, data)


# ---------------------------------------------------------------- 缺陷 2


def test_five_cycles_without_observation_keep_one_action(client):
    """没有新观测 = 看不到效果 = 只允许一个动作挂着，后面几轮全被拦。"""
    loop = _loop(client)
    observe(client, loop, "temp", 20.0)

    results = [run(client, loop) for _ in range(5)]

    actions = client.store.list_actions(loop)
    assert len(actions) == 1, [(a["id"], a["status"]) for a in actions]
    assert actions[0]["status"] == "pending_verification"
    assert len(client.store.actions_pending_verification(loop)) == 1

    blocked = [r for r in results if any("pending_action_blocked" in g for g in r.guard_events)]
    assert len(blocked) == 4
    assert any(
        f"{GUARD} pending_action_blocked goal=g1 action={actions[0]['id']}" in g
        for r in blocked for g in r.guard_events
    ), [r.guard_events for r in blocked]
    # 被拦的轮次不许报成"什么都没发生"
    assert all(r.verification_status.startswith(("blocked", "verified")) for r in blocked)


def test_blocked_cycle_still_observes_and_computes_error(client):
    """拦截只针对动作；观测、偏差、审计照常走完。"""
    loop = _loop(client)
    observe(client, loop, "temp", 20.0)
    run(client, loop)
    res = run(client, loop)

    assert res.observations["temp"]["value"] == 20.0
    assert res.errors[0]["error"] == pytest.approx(60.0)
    assert res.should_intervene is True
    kinds = [e["kind"] for e in client.store.list_audit(loop, 200)]
    assert "action_blocked" in kinds
    chain = client.store.audit_chain(res.cycle_id)
    assert any(e["payload"].get("event") == "pending_action_blocked" for e in chain)


def test_new_action_allowed_again_after_verdict(client):
    loop = _loop(client)
    observe(client, loop, "temp", 20.0)
    run(client, loop)                      # a1 pending
    observe(client, loop, "temp", 79.0)
    res = run(client, loop)

    assert [v["verdict"] for v in res.verifications] == ["improved"]
    actions = client.store.list_actions(loop)
    assert len(actions) == 2, [(a["id"], a["status"]) for a in actions]
    newest = max(actions, key=lambda a: a["id"])
    assert newest["status"] == "pending_verification"
    assert res.guard_events == []


def test_max_pending_actions_can_be_raised(client):
    """默认严格，但配置明确调高后允许多个同类动作同时挂起。"""
    data = cfg()
    data["goals"][0]["setpoint"] = {"kind": "value", "value": 80.0}
    data["goals"][0]["tolerance"] = 0.5
    data["actuator"]["max_pending_actions"] = 2
    loop = new_loop(client, data)
    observe(client, loop, "temp", 20.0)

    run(client, loop)
    run(client, loop)

    assert len(client.store.actions_pending_verification(loop)) == 2


def test_max_pending_actions_default_is_one(client):
    data = cfg()
    from fcc.models import LoopConfig

    assert LoopConfig.model_validate(data).actuator.max_pending_actions == 1


# ---------------------------------------------------------------- 缺陷 1


def test_cycle_carries_verification_summary(client):
    loop = _loop(client)
    observe(client, loop, "temp", 20.0)
    res = run(client, loop)

    assert set(res.verification_summary) == SUMMARY_SHAPE == set(SUMMARY_KEYS)
    assert res.verification_summary["pending"] == 1
    stored = client.store.get_cycle(res.cycle_id)
    assert stored["verification_summary"] == res.verification_summary


def test_verdict_becomes_visible_after_new_observation(client):
    """审核缺陷 1 的复现路径：verdict 算出来了就必须在对外字段里看得到。"""
    loop = _loop(client)
    observe(client, loop, "temp", 20.0)
    run(client, loop)

    observe(client, loop, "temp", 79.0)
    up = run(client, loop)
    assert up.verification_summary["improved"] >= 1
    assert up.verification_summary["worsened"] == 0

    observe(client, loop, "temp", 10.0)
    down = run(client, loop)
    assert down.verification_summary["worsened"] >= 1
    assert down.verification_summary["improved"] >= 1

    report = client.get_verification_report(loop)
    assert report["verdict_counts"]["improved"] >= 1
    assert report["verdict_counts"]["worsened"] >= 1
    assert set(report["verdict_counts"]) == SUMMARY_SHAPE
    assert report["pending_actions"] == report["verdict_counts"]["pending"]


def test_verdict_requires_observation_after_action(client):
    """动作执行前的旧观测不能当证据：判不出结论时动作必须继续挂起。"""
    loop = _loop(client)
    observe(client, loop, "temp", 20.0)
    run(client, loop)
    res = run(client, loop)                # 没有新观测

    assert [v["verdict"] for v in res.verifications] == ["inconclusive"]
    assert "新观测" in " ".join(res.verifications[0]["notes"])
    action = client.store.list_actions(loop)[0]
    assert action["status"] == "pending_verification"
    assert res.verification_summary["pending"] == 1


def test_panel_renders_verdict_distribution(client):
    from fcc.server import WEB_DIR
    import os

    html = open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8").read()
    assert "verification_summary" in html
    assert "verdictPills" in html
    for word in ("improved", "worsened", "unchanged", "pending"):
        assert word in html


# ---------------------------------------------------------------- 老库升级


def test_old_database_gets_verification_summary_column(tmp_path):
    """老库（没有 verification_summary_json 列）启动时自动补列，且能继续读写。"""
    path = str(tmp_path / "legacy.db")
    old_schema = SCHEMA.replace(
        "    verification_summary_json TEXT NOT NULL DEFAULT '{}',\n", ""
    )
    assert "verification_summary_json" not in old_schema
    conn = sqlite3.connect(path)
    conn.executescript(old_schema)
    conn.commit()
    conn.close()

    store = Store(path)
    try:
        columns = {r["name"] for r in store._exec("PRAGMA table_info(ctrl_cycle)").fetchall()}
        assert "verification_summary_json" in columns

        client = Client(engine=Engine(store))
        loop = new_loop(client)
        observe(client, loop, "temp", 20.0)
        res = run(client, loop)
        assert res.verification_summary["pending"] == 1
        assert store.get_cycle(res.cycle_id)["verification_summary"]["pending"] == 1
    finally:
        store.close()
