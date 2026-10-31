"""Verification：五态判定，必须基于**重新观测**，不接受 AI 自评（派单包 §10.2）。

判定顺序（写死，不通过配置放宽）：
1. 动作执行失败          -> failed
2. 没有新的观测          -> inconclusive
3. 观测 stale / 仅 AI 源  -> inconclusive
4. |Δ| <= noise_k*σ      -> unchanged
5. |error_after| < |error_before| -> improved
6. |error_after| > |error_before| -> worsened
"""

from __future__ import annotations

from typing import Any

from . import util
from .goals import setpoint_error
from .models import ErrorRecord, GoalSpec, VerificationRecord
from .observations import ObservationManager, Snapshot
from .store import Store

VERDICTS = ("improved", "unchanged", "worsened", "failed", "inconclusive")
CONCLUSIVE = ("improved", "unchanged", "worsened")
SUMMARY_KEYS = ("improved", "unchanged", "worsened", "inconclusive", "failed", "pending")
# 这两个状态不参与验证闭环（人驳回、已回滚），不计入分布
_EXCLUDED_STATUSES = {"rejected", "rolled_back"}


def summarize_verdicts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """把 `Store.verification_breakdown` 的聚合行翻成固定六键分布。

    口径：本 loop **迄今所有**动作各算一票。已结案的动作按 verdict 落到
    improved/unchanged/worsened/inconclusive/failed；还没结案的一律落 pending
    （哪怕中间试判过 inconclusive —— 它仍然"没有结论"，面板上的 pending 就该是它）。
    被驳回、被回滚的动作不进入分布。
    """
    summary = {key: 0 for key in SUMMARY_KEYS}
    for row in rows:
        verdict = str(row.get("verdict") or "pending")
        n = int(row.get("n") or 0)
        if verdict in _EXCLUDED_STATUSES:
            continue
        summary[verdict if verdict in summary else "pending"] += n
    return summary


def _observed_after(snap: Any, action: dict[str, Any]) -> bool:
    """动作执行之后是否拿到了新的观测（没有基线时间就无法判断，按"是"处理）。"""
    executed_at = action.get("executed_at")
    record = getattr(snap, "record", None)
    ts = getattr(record, "ts", None) if record is not None else None
    if not executed_at or not ts:
        return True
    try:
        return util.parse_iso(str(ts)) > util.parse_iso(str(executed_at))
    except (TypeError, ValueError):
        return True




class Verifier:
    def __init__(self, store: Store, loop_id: str, obs: ObservationManager,
                 noise_k: float = 2.0, delay_cycles: int = 1,
                 delay_seconds: float = 0.0) -> None:
        self.store = store
        self.loop_id = loop_id
        self.obs = obs
        self.noise_k = noise_k
        self.delay_cycles = max(delay_cycles, 0)
        self.delay_seconds = max(delay_seconds, 0.0)

    # ------------------------------------------------------------------

    MAX_ATTEMPTS = 3  # 连续 inconclusive 超过这个次数就停手，等新观测，别刷记录

    def verify_pending(
        self, goals: list[GoalSpec], cycle_seq: int, now: str
    ) -> list[dict[str, Any]]:
        """对本 loop 所有待验证动作做判定，返回已判定的摘要列表。"""
        pending = self.store.actions_pending_verification(self.loop_id)
        done: list[dict[str, Any]] = []
        for action in pending:
            goal = next((g for g in goals if g.goal_id == action.get("goal_id")), None)
            if goal is None:
                continue
            result = action.get("result") or {}
            baseline_cycle = result.get("baseline_cycle")
            if baseline_cycle is not None and cycle_seq - int(baseline_cycle) < self.delay_cycles:
                continue  # 验证延迟未到，避免"动作和观测同时发生"的假因果
            executed_at = action.get("executed_at")
            if executed_at and self.delay_seconds > 0:
                if util.seconds_between(executed_at, now) < self.delay_seconds:
                    continue
            attempt = int(result.get("verify_attempts", 0))
            if attempt >= self.MAX_ATTEMPTS:
                continue  # 已经试够了，状态仍是 pending_verification，等新观测再判
            snap = self.obs.snapshot(goal.target_variable, now)
            record = self.judge(action, goal, snap)
            # 判"改善/不变/恶化"必须有动作之后的新观测。观测比动作还旧时
            # （hold_last 拿到的是执行前那条），只是"还没看到效果"，不是结论。
            deferred = record.verdict in CONCLUSIVE and not _observed_after(snap, action)
            if deferred:
                if result.get("deferred_at") == action.get("executed_at"):
                    continue  # 同一条旧观测已经判过"等新观测"，不重复刷记录
                record = VerificationRecord(
                    action_id=str(action["id"]), verdict="inconclusive",
                    baseline=record.baseline, after=record.after, delta={},
                    noise_estimate=record.noise_estimate,
                    notes=["动作执行后还没有新观测（最新观测早于执行时间），"
                           "不能拿执行前的数据当证据；动作继续挂起，等新观测再判"],
                )
            vid = self.store.insert_verification(self.loop_id, action["id"],
                                                 cycle_seq, record)
            self.store.update_action(
                action["id"],
                status="verified" if record.verdict != "inconclusive" else "pending_verification",
                verification_status=record.verdict,
                # 等观测不算一次判定，避免把 MAX_ATTEMPTS 耗在"没数据"上
                result_json=util.dumps({
                    **result,
                    "verify_attempts": attempt + (0 if deferred else 1),
                    "deferred_at": action.get("executed_at") if deferred else None,
                }),
            )
            self.store.add_audit(
                self.loop_id, "verification", "verifier",
                {"action_id": action["id"], "verification_id": vid,
                 "verdict": record.verdict, "baseline": record.baseline,
                 "after": record.after, "delta": record.delta,
                 "cycle_id": cycle_seq},
            )
            done.append({
                "action_id": action["id"], "goal_id": action.get("goal_id"),
                "verdict": record.verdict,
                "baseline": record.baseline, "after": record.after,
                "notes": record.notes,
            })
        return done

    # ------------------------------------------------------------------

    def judge(self, action: dict[str, Any], goal: GoalSpec, snap: Snapshot) -> VerificationRecord:
        result = action.get("result") or {}
        baseline = result.get("baseline") or {}
        after = {
            "value": util.round_sig(snap.value),
            "ts": snap.record.ts if snap.record else None,
            "source_type": snap.source_type,
            "stale": snap.stale,
            "flags": snap.flags,
        }
        notes: list[str] = []

        if action.get("status") == "failed" or action.get("verification_status") == "failed":
            return VerificationRecord(
                action_id=str(action["id"]), verdict="failed",
                baseline=baseline, after=after, delta={},
                notes=["动作执行失败，无有效效果可验证"],
            )

        if snap.value is None:
            return VerificationRecord(
                action_id=str(action["id"]), verdict="inconclusive",
                baseline=baseline, after=after, delta={},
                notes=["执行后没有新的可用观测，无法判定（不能声称成功）"],
            )

        if snap.stale:
            return VerificationRecord(
                action_id=str(action["id"]), verdict="inconclusive",
                baseline=baseline, after=after, delta={},
                notes=[f"执行后的观测 stale（{snap.note}），无法判定"],
            )

        if snap.source_type == "ai_inference":
            return VerificationRecord(
                action_id=str(action["id"]), verdict="inconclusive",
                baseline=baseline, after=after, delta={},
                notes=["[FCC_GUARD] 执行后只有 ai_inference 观测，AI 不能给动作作证"],
            )

        if snap.record is not None and not snap.record.usable_as_ground_truth:
            return VerificationRecord(
                action_id=str(action["id"]), verdict="inconclusive",
                baseline=baseline, after=after, delta={},
                notes=["观测存在异常值或不可信来源，不能用于验证动作效果"],
            )

        base_value = baseline.get("value")
        if base_value is None:
            return VerificationRecord(
                action_id=str(action["id"]), verdict="inconclusive",
                baseline=baseline, after=after, delta={},
                notes=["缺少基线观测（动作提出时没有可用观测），无法对比"],
            )

        err_before = float(baseline.get("error", setpoint_error(goal.setpoint, base_value)))
        err_after = setpoint_error(goal.setpoint, snap.value)
        delta_value = snap.value - float(base_value)
        delta_error = err_after - err_before
        noise = self._noise_estimate(goal.target_variable)

        record = VerificationRecord(
            action_id=str(action["id"]),
            verdict="inconclusive",
            baseline={**baseline, "value": base_value, "error": util.round_sig(err_before)},
            after={**after, "error": util.round_sig(err_after)},
            delta={
                "value": util.round_sig(delta_value),
                "error": util.round_sig(delta_error),
                "abs_error_before": util.round_sig(abs(err_before)),
                "abs_error_after": util.round_sig(abs(err_after)),
            },
            noise_estimate=util.round_sig(noise),
        )

        band = self._band(goal, noise)
        if band is not None and abs(delta_value) <= band:
            record.verdict = "unchanged"
            raw = (self.noise_k * noise) if noise is not None else 0.0
            record.notes.append(f"变化量 {delta_value:+.4g} 落在噪声带 ±{band:.4g} 内")
            if noise is not None and raw > band + 1e-12:
                record.notes.append(
                    f"合理噪声带为 ±{raw:.4g}，已被目标尺度 ±{band:.4g} 截断；"
                    "说明当前观测的波动已经和容差同量级，验证结论可信度低"
                )
            return record

        if abs(err_after) < abs(err_before) - 1e-12:
            record.verdict = "improved"
            record.notes.append(f"|e| {abs(err_before):.4g} -> {abs(err_after):.4g}")
        elif abs(err_after) > abs(err_before) + 1e-12:
            record.verdict = "worsened"
            record.notes.append(f"|e| {abs(err_before):.4g} -> {abs(err_after):.4g}，方向错了")
        else:
            record.verdict = "unchanged"
            record.notes.append("误差未变化")
        return record

    def _band(self, goal: GoalSpec, noise: float | None) -> float | None:
        """有效噪声带 = min(noise_k*sigma, 目标尺度)。

        目标尺度：单点目标取 tolerance，区间目标取区间半宽。
        不截断的话，被控量自身剧烈振荡时噪声带会涨到几十，
        任何动作都会被判成 unchanged —— 那等于放弃验证。
        """
        if noise is None:
            return None
        sp = goal.setpoint
        if sp.kind == "range" and sp.low is not None and sp.high is not None:
            scale = abs(float(sp.high) - float(sp.low)) / 2.0
        else:
            scale = goal.tolerance
        band = self.noise_k * noise
        if scale > 0:
            band = min(band, scale)
        return band

    def _noise_estimate(self, metric_id: str) -> float | None:
        """噪声估计用**二阶差分**而不是原始标准差。

        原始标准差会把"正在爬升"的趋势当成噪声，导致什么都判成 unchanged
        （冒烟时实测到过这个问题）。二阶差分对恒定斜率不敏感：
            d2[i] = v[i] - 2v[i-1] + v[i-2]
        对白噪声，stdev(d2) = sqrt(6)*sigma，故 sigma ≈ stdev(d2)/sqrt(6)。
        """
        hist = self.store.observation_history(self.loop_id, metric_id, 10)
        values = [h.value for h in hist if "rejected" not in h.quality_flags]
        if len(values) < 4:
            return None
        d2 = [
            values[i] - 2 * values[i - 1] + values[i - 2]
            for i in range(2, len(values))
        ]
        # 用 MAD（中位数绝对偏差）而不是标准差：正弦/斜坡类确定性波动会把标准差撑大，
        # 结果什么都判成 unchanged。MAD 对这类离群点更稳。
        med = util.quantile(sorted(abs(v) for v in d2), 0.5)
        sigma = 1.4826 * med / (6 ** 0.5)
        return sigma

    # ------------------------------------------------------------------

    def report(self, limit: int = 200) -> dict[str, Any]:
        rows = self.store.list_verifications(self.loop_id, limit)
        counts = {v: 0 for v in VERDICTS}
        for row in rows:
            counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
        total = sum(counts.values())
        verdict_counts = summarize_verdicts(self.store.verification_breakdown(self.loop_id))
        return {
            "loop_id": self.loop_id,
            "total": total,
            "counts": counts,
            # 按动作算的分布（一个动作一票），含尚未结案的 pending；
            # 上面的 counts 是按判定记录算的（同一动作重试会重复计数）。
            "verdict_counts": verdict_counts,
            "pending_actions": verdict_counts["pending"],
            "improved_rate": (counts["improved"] / total) if total else 0.0,
            "records": rows,
        }


def summarize_errors(errors: list[ErrorRecord]) -> dict[str, Any]:
    return {
        "max_abs_error": max((abs(e.error) for e in errors if e.error is not None), default=None),
        "out_of_tolerance": [e.goal_id for e in errors if e.out_of_tolerance],
    }
