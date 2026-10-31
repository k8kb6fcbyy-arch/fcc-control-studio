"""Comparator：偏差计算 + 规则 A（AI 不能当裁判）。

规则 A 的落点在这里：当某个偏差**只有 ai_inference 支持**时，
`should_intervene` 强制为 False，并置 `human_review_required=True`。
此判断不依赖任何"配置开关"，无法通过改配置绕过。
"""

from __future__ import annotations

from typing import Any, Iterable

from . import util
from .goals import GoalManager, setpoint_error, within_tolerance
from .models import ErrorRecord, GoalSpec, ObservationRecord
from .observations import Snapshot
from .store import Store

FLIP_THRESHOLD = 3  # 符号翻转次数达到该值判为振荡
HISTORY_WINDOW = 8  # 速率/累计误差用的历史周期数
OSC_WINDOW = 12     # 振荡判定要看得更长：8 轮窗口会把早期的翻转滑出去，导致漏判


class Comparator:
    def __init__(self, store: Store, loop_id: str, goals: GoalManager) -> None:
        self.store = store
        self.loop_id = loop_id
        self.goals = goals

    # ------------------------------------------------------------------

    def evaluate(
        self, snapshots: dict[str, Snapshot], now: str | None = None
    ) -> tuple[list[ErrorRecord], dict[str, Any]]:
        now = now or util.utcnow_iso()
        records: list[ErrorRecord] = []
        for goal in self.goals.sorted_by_priority():
            records.append(self._evaluate_goal(goal, snapshots, now))

        summary = {
            "should_intervene": any(
                r.out_of_tolerance and not r.ai_only and not r.missing and not r.stale
                for r in records
            ),
            "human_review_required": any(r.ai_only for r in records),
            "uncertainty": max((r.uncertainty for r in records), default=0.0),
            "ai_only_goals": [r.goal_id for r in records if r.ai_only],
            "oscillation_goals": [r.goal_id for r in records if r.oscillation_detected],
            "missing_goals": [r.goal_id for r in records if r.missing],
        }
        return records, summary

    # ------------------------------------------------------------------

    def _evaluate_goal(
        self, goal: GoalSpec, snapshots: dict[str, Snapshot], now: str
    ) -> ErrorRecord:
        rec = ErrorRecord(
            goal_id=goal.goal_id,
            target_variable=goal.target_variable,
            direction=goal.direction,
            setpoint=goal.setpoint.model_dump(),
            tolerance=goal.tolerance,
        )
        snap = snapshots.get(goal.target_variable)
        if snap is None or snap.value is None:
            rec.missing = True
            rec.stale = bool(snap.stale) if snap else True
            rec.notes.append(
                snap.note if snap and snap.note else "目标变量本轮无可用观测"
            )
            rec.uncertainty = 1.0
            rec.uncertainty_reason.append("missing_observation")
            return rec

        rec.value = snap.value
        rec.stale = snap.stale
        rec.error = setpoint_error(goal.setpoint, snap.value)
        rec.out_of_tolerance = not within_tolerance(goal.setpoint, snap.value, goal.tolerance)
        if goal.setpoint.kind == "value" and goal.direction == "increase":
            rec.error = max(0.0, rec.error)
            rec.out_of_tolerance = snap.value < goal.setpoint.nominal() - goal.tolerance
        elif goal.setpoint.kind == "value" and goal.direction == "decrease":
            rec.error = min(0.0, rec.error)
            rec.out_of_tolerance = snap.value > goal.setpoint.nominal() + goal.tolerance

        # ---- 证据来源（规则 A 的判据基础）----
        recent = self._recent_observations(goal.target_variable, now, snap.spec.freshness_limit)
        ground = [o for o in recent if o.usable_as_ground_truth]
        ai_obs = [o for o in recent if o.is_ai]
        sources = sorted({o.source_type for o in ground})
        if not ground and snap.source_type not in ("none",):
            sources = sorted({o.source_type for o in recent}) or [snap.source_type]
        rec.evidence_sources = sources

        ai_only = (not ground) and bool(ai_obs)
        rec.ai_only = ai_only

        # ---- 不确定性 ----
        if ai_obs:
            worst = max((1.0 - float(o.confidence) for o in ai_obs), default=0.0)
            rec.uncertainty = util.clamp(worst, 0.0, 1.0)
            rec.uncertainty_reason.append(
                f"ai_inference 参与（最低置信度 {min(o.confidence for o in ai_obs):.2f}）"
            )
        if snap.stale:
            rec.uncertainty = util.clamp(rec.uncertainty + 0.4, 0.0, 1.0)
            rec.uncertainty_reason.append("观测 stale")
        if ai_only:
            rec.uncertainty = util.clamp(max(rec.uncertainty, 0.5), 0.0, 1.0)
            rec.uncertainty_reason.append(
                "[FCC_GUARD] 该偏差只有 ai_inference 支持，AI 是证人不是裁判"
            )
            rec.notes.append("ai_only_deviation: 不得据此单独触发干预")

        # ---- 速率 / 累计 / 振荡 ----
        history = self.store.observation_history(
            self.loop_id, goal.target_variable, HISTORY_WINDOW
        )
        rec.rate_of_change = self._rate(history)
        past_errors = self._past_errors(goal.goal_id, OSC_WINDOW)
        rec.cumulative_error = sum(past_errors) / max(len(past_errors), 1) if past_errors else (
            rec.error or 0.0
        )
        flips = count_sign_flips(past_errors + [rec.error or 0.0])
        rec.oscillation_detected = flips >= FLIP_THRESHOLD
        if rec.oscillation_detected:
            rec.notes.append(f"oscillation_detected: 连续 {flips} 次误差符号翻转")
            rec.over_correction = self._amplitude_growing(past_errors + [rec.error or 0.0])
        return rec

    # ------------------------------------------------------------------

    def _recent_observations(
        self, metric_id: str, now: str, freshness: float
    ) -> list[ObservationRecord]:
        import datetime as _dt

        start = util.parse_iso(now) - _dt.timedelta(seconds=freshness)
        return self.store.observations_since(self.loop_id, metric_id, util.to_iso(start))

    def _rate(self, history: list[ObservationRecord]) -> float | None:
        usable = [h for h in history if "rejected" not in h.quality_flags]
        if len(usable) < 2:
            return None
        last, prev = usable[-1], usable[-2]
        dt = util.seconds_between(prev.ts, last.ts)
        if dt <= 0:
            return None
        return (last.value - prev.value) / dt

    def _past_errors(self, goal_id: str, limit: int = HISTORY_WINDOW) -> list[float]:
        cycles = self.store.list_cycles(self.loop_id, limit)
        errors: list[float] = []
        for cyc in reversed(cycles):
            for err in cyc.get("errors", []):
                if err.get("goal_id") == goal_id and err.get("error") is not None:
                    errors.append(float(err["error"]))
        return errors

    @staticmethod
    def _amplitude_growing(errors: list[float]) -> bool:
        if len(errors) < 4:
            return False
        amps = [abs(e) for e in errors[-4:]]
        return amps[3] > amps[2] > amps[1] > amps[0]


def count_sign_flips(series: Iterable[float], eps: float = 1e-9) -> int:
    flips = 0
    prev = 0
    for value in series:
        sign = 1 if value > eps else (-1 if value < -eps else 0)
        if sign == 0:
            continue
        if prev != 0 and sign != prev:
            flips += 1
        prev = sign
    return flips
