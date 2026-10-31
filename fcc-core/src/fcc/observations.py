"""Observation Manager：注册、校验、异常值检测、有效期、缺失策略、历史。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import util
from .models import ObservationIn, ObservationRecord, ObservationSpec
from .store import Store


class ObservationRejected(ValueError):
    """观测不合法（字段缺失 / 越过 valid_range 且策略为 drop / 未知变量）。"""


@dataclass
class Snapshot:
    metric_id: str
    spec: ObservationSpec
    record: ObservationRecord | None
    value: float | None
    source_type: str
    stale: bool = False
    missing: bool = False
    age_seconds: float | None = None
    flags: list[str] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "value": util.round_sig(self.value),
            "source_type": self.source_type,
            "unit": self.spec.unit,
            "stale": self.stale,
            "missing": self.missing,
            "age_seconds": util.round_sig(self.age_seconds, 2),
            "flags": list(self.flags),
            "note": self.note,
            "ts": self.record.ts if self.record else None,
            "is_ground_truth": bool(self.record.is_ground_truth) if self.record else False,
            "confidence": util.round_sig(self.record.confidence, 3) if self.record else None,
        }


class ObservationManager:
    """一个 loop 一个实例；本身无状态，状态全在 Store。"""

    def __init__(self, store: Store, loop_id: str, specs: list[ObservationSpec]) -> None:
        self.store = store
        self.loop_id = loop_id
        self._specs = {s.metric_id: s for s in specs}

    @property
    def specs(self) -> dict[str, ObservationSpec]:
        return dict(self._specs)

    def spec(self, metric_id: str) -> ObservationSpec:
        if metric_id not in self._specs:
            raise ObservationRejected(f"未注册的观测变量: {metric_id}")
        return self._specs[metric_id]

    # -- 入库 -------------------------------------------------------------

    def ingest(self, data: ObservationIn | dict[str, Any], actor: str = "api") -> ObservationRecord:
        obs_in = data if isinstance(data, ObservationIn) else ObservationIn.model_validate(data)
        spec = self.spec(obs_in.metric_id)

        flags: list[str] = []
        value = float(obs_in.value)
        reject = False

        # 1) 硬范围校验
        if spec.valid_range is not None:
            low, high = spec.valid_range
            if value < low or value > high:
                flags.append("out_of_range")
                if spec.outlier_policy == "drop":
                    reject = True
                elif spec.outlier_policy == "clamp":
                    value = util.clamp(value, low, high)
                    flags.append("clamped")

        # 2) 统计异常值（3σ 或 IQR，二选一并写清）
        if not reject:
            is_outlier, z = self._outlier_check(spec, value)
            if is_outlier:
                flags.append("outlier")
                flags.append(f"outlier_z={z:.2f}" if z is not None else "outlier_iqr")
                if spec.outlier_policy == "drop":
                    reject = True
                elif spec.outlier_policy == "clamp":
                    value = self._clamp_to_window(spec, value)
                    flags.append("clamped")

        if reject:
            flags.append("rejected")
            # 仍然入库（留痕），但不可用作 ground truth
            record = self._persist(obs_in, value, flags)
            self.store.add_audit(
                self.loop_id,
                "observation_rejected",
                actor,
                {
                    "metric_id": obs_in.metric_id,
                    "value": value,
                    "source_type": obs_in.source_type,
                    "flags": flags,
                    "policy": spec.outlier_policy,
                },
            )
            return record

        record = self._persist(obs_in, value, flags)
        return record

    def _persist(
        self, obs_in: ObservationIn, value: float, flags: list[str]
    ) -> ObservationRecord:
        ts = obs_in.ts or util.utcnow_iso()
        record = ObservationRecord(
            loop_id=self.loop_id,
            metric_id=obs_in.metric_id,
            value=value,
            source_type=obs_in.source_type,
            confidence=float(obs_in.confidence if obs_in.confidence is not None else 1.0),
            evidence=list(obs_in.evidence or []),
            is_ground_truth=bool(obs_in.is_ground_truth),
            ts=ts,
            quality_flags=flags,
            model=obs_in.model,
            prompt_version=obs_in.prompt_version,
        )
        record.id = self.store.insert_observation(record)
        return record

    def _recent_values(self, spec: ObservationSpec) -> list[float]:
        hist = self.store.observation_history(self.loop_id, spec.metric_id, spec.outlier_window)
        return [h.value for h in hist if "rejected" not in h.quality_flags]

    def _outlier_check(self, spec: ObservationSpec, value: float) -> tuple[bool, float | None]:
        values = self._recent_values(spec)
        if len(values) < 4:
            return False, None
        if spec.outlier_method == "sigma3":
            mu = util.mean(values) or 0.0
            sd = util.stdev(values)
            if sd <= 1e-12:
                return abs(value - mu) > 1e-9, 0.0
            z = (value - mu) / sd
            return abs(z) > 3.0, z
        # IQR
        ordered = sorted(values)
        q1 = util.quantile(ordered, 0.25)
        q3 = util.quantile(ordered, 0.75)
        iqr = q3 - q1
        if iqr <= 1e-12:
            return False, None
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        return value < lo or value > hi, None

    def _clamp_to_window(self, spec: ObservationSpec, value: float) -> float:
        values = self._recent_values(spec)
        if len(values) < 4:
            return value
        ordered = sorted(values)
        if spec.outlier_method == "sigma3":
            mu = util.mean(values) or 0.0
            sd = util.stdev(values)
            return util.clamp(value, mu - 3 * sd, mu + 3 * sd)
        return util.clamp(
            value, util.quantile(ordered, 0.25), util.quantile(ordered, 0.75)
        )

    # -- 取样 -------------------------------------------------------------

    def snapshot(self, metric_id: str, now: str | None = None) -> Snapshot:
        spec = self.spec(metric_id)
        now = now or util.utcnow_iso()
        latest = self.store.latest_observation(self.loop_id, metric_id)

        if latest is None:
            return Snapshot(
                metric_id=metric_id,
                spec=spec,
                record=None,
                value=None,
                source_type="none",
                missing=True,
                flags=["no_data"],
                note="该变量从未上报过观测",
            )

        age = util.seconds_between(latest.ts, now)
        stale = age > spec.freshness_limit
        flags = list(latest.quality_flags)

        if "rejected" in flags:
            # 被丢弃的观测不能参与计算，按缺失处理
            return Snapshot(
                metric_id=metric_id,
                spec=spec,
                record=latest,
                value=None,
                source_type=latest.source_type,
                stale=True,
                missing=True,
                age_seconds=age,
                flags=flags + ["missing"],
                note="最新观测被判为异常且策略为 drop，按缺失处理",
            )

        if stale:
            flags.append("stale")
            if spec.missing_policy == "skip":
                return Snapshot(
                    metric_id=metric_id,
                    spec=spec,
                    record=latest,
                    value=None,
                    source_type=latest.source_type,
                    stale=True,
                    missing=True,
                    age_seconds=age,
                    flags=flags + ["missing"],
                    note=f"观测已过期 {age:.1f}s > {spec.freshness_limit}s，策略 skip",
                )
            if spec.missing_policy == "hold_last":
                flags.append("held_last")
                return Snapshot(
                    metric_id=metric_id,
                    spec=spec,
                    record=latest,
                    value=latest.value,
                    source_type=latest.source_type,
                    stale=True,
                    missing=False,
                    age_seconds=age,
                    flags=flags,
                    note=f"观测已过期 {age:.1f}s，策略 hold_last：沿用旧值但标记 stale",
                )
            # stale 策略：值仍返回，但下游必须视为不可信
            return Snapshot(
                metric_id=metric_id,
                spec=spec,
                record=latest,
                value=latest.value,
                source_type=latest.source_type,
                stale=True,
                missing=False,
                age_seconds=age,
                flags=flags,
                note=f"观测已过期 {age:.1f}s，策略 stale：值保留但不计入可信证据",
            )

        return Snapshot(
            metric_id=metric_id,
            spec=spec,
            record=latest,
            value=latest.value,
            source_type=latest.source_type,
            stale=False,
            missing=False,
            age_seconds=age,
            flags=flags,
        )

    def snapshot_all(self, now: str | None = None) -> dict[str, Snapshot]:
        return {m: self.snapshot(m, now) for m in self._specs}

    def history(self, metric_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return [
            {
                "ts": r.ts,
                "value": r.value,
                "source_type": r.source_type,
                "confidence": r.confidence,
                "quality_flags": r.quality_flags,
            }
            for r in self.store.observation_history(self.loop_id, metric_id, limit)
        ]
