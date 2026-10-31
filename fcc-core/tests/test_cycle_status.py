"""FCC-P1d：`verification_status` 不许吞掉本轮已得的 verdict。

第 6 步（验证上一批动作）发生在新动作产生之前，所以同一轮里
"给旧动作结案"和"提了新动作"可以同时成立。顶层单值字段必须两个都表达：
`verified:<verdicts>+action_pending[+exec_failed]`。
"""

from __future__ import annotations

from conftest import cfg, new_loop, observe, run
from fcc.engine import Engine

PEN = {"action_id": 1, "status": "pending_verification"}
FAIL = {"action_id": 2, "status": "failed"}
APPROVAL = {"action_id": 3, "status": "awaiting_approval"}


def _verdicts(*values: str) -> list[dict]:
    return [{"verdict": v} for v in values]


def _mixed_cycle(client):
    """跑出一轮"既结旧动作(worsened)又提新动作(pending)"的混合态。"""
    loop = new_loop(client)
    observe(client, loop, "temp", 10.0)      # |e|=40 -> 提案并执行
    run(client, loop)
    observe(client, loop, "temp", 5.0)       # 跳到更远处：旧动作 worsening，|e|=45 仍带外
    return loop, run(client, loop)


# ---------------------------------------------------------------- 单元级


def test_status_table_nine_cases():
    """派单包 §1 的 9 行对照表，逐行核对。"""
    cases = [
        (([PEN | {"status": "verified"}], _verdicts("improved"), False),
         "verified:improved"),
        (([PEN], _verdicts("worsened"), False), "verified:worsened+action_pending"),
        (([FAIL], _verdicts("improved"), False), "verified:improved+exec_failed"),
        (([PEN, FAIL], _verdicts("improved"), False),
         "verified:improved+action_pending+exec_failed"),
        (([FAIL], [], False), "failed"),
        (([PEN], [], False), "pending_verification"),
        (([APPROVAL], [], False), "awaiting_human"),
        (([], [], True), "blocked_pending_action"),
        (([], [], False), "no_action"),
    ]
    for (executed, verifications, blocked), expected in cases:
        got = Engine._cycle_verification_status(executed, verifications, blocked=blocked)
        assert got == expected, (executed, verifications, blocked, got)


def test_verified_prefix_always_first_and_multi_verdict_sorted():
    out = Engine._cycle_verification_status([PEN], _verdicts("worsened", "improved"))
    assert out == "verified:improved,worsened+action_pending"
    assert out.startswith("verified:")


# ---------------------------------------------------------------- 端到端


def test_mixed_cycle_keeps_verdict_string_exact(client):
    _, res = _mixed_cycle(client)
    assert [v["verdict"] for v in res.verifications] == ["worsened"]
    assert len(res.executed_actions) == 1
    assert res.executed_actions[0]["status"] == "pending_verification"
    assert res.verification_status == "verified:worsened+action_pending"


def test_mixed_cycle_startswith_verified(client):
    _, res = _mixed_cycle(client)
    assert res.verification_status.startswith("verified:") is True


def test_mixed_cycle_summary_counts_untouched(client):
    _, res = _mixed_cycle(client)
    summary = res.verification_summary
    assert set(summary) == {"improved", "unchanged", "worsened", "inconclusive",
                            "failed", "pending"}
    assert summary["worsened"] == 1
    assert summary["pending"] == 1
    assert summary["improved"] == 0
    # 状态里的 +action_pending 不许渗进 verdict 计数
    assert sum(summary.values()) == 2


def test_single_verdict_string_unchanged(client):
    """单结论场景逐字不变：动作结案 improved 且本轮不再提案。"""
    loop = new_loop(client)
    observe(client, loop, "temp", 10.0)
    run(client, loop)
    observe(client, loop, "temp", 50.0)      # 到点，|e|=0 在容差内 -> 不再提案
    res = run(client, loop)
    assert [v["verdict"] for v in res.verifications] == ["improved"]
    assert res.executed_actions == []
    assert res.verification_status == "verified:improved"


def test_first_cycle_pending_status_unchanged(client):
    """没有 verdict 的老行为一律照旧。"""
    loop = new_loop(client)
    observe(client, loop, "temp", 10.0)
    res = run(client, loop)
    assert res.verification_status == "pending_verification"
