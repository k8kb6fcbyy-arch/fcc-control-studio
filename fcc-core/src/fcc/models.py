"""FCC 数据模型（pydantic v2）。

这里承载派单包 §2 的**代码级强制**：三源分类的字段级校验、目标冲突检测。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SourceType = Literal["measured", "ai_inference", "human_input"]
GoalDirection = Literal["increase", "decrease", "approach", "maintain"]
PermissionMode = Literal["auto", "require_approval", "suggest_only"]
ControllerKind = Literal[
    "monitor", "threshold", "rule", "ai_assisted", "human_in_loop", "pid", "mpc"
]

AI_SOURCE: SourceType = "ai_inference"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False, allow_inf_nan=False)


# --------------------------------------------------------------------------
# 目标
# --------------------------------------------------------------------------


class Setpoint(_Base):
    """目标值。kind=value 单点，kind=range 区间（区间目标不要求落在某一点）。"""

    kind: Literal["value", "range"] = "value"
    value: float | None = None
    low: float | None = None
    high: float | None = None

    @model_validator(mode="after")
    def _check(self) -> "Setpoint":
        if self.kind == "value":
            if self.value is None:
                raise ValueError("kind=value 时必须提供 value")
        else:
            if self.low is None or self.high is None:
                raise ValueError("kind=range 时必须同时提供 low 和 high")
            if self.low > self.high:
                raise ValueError("range 目标 low 不能大于 high")
        return self

    def nominal(self) -> float:
        if self.kind == "value":
            return float(self.value)  # type: ignore[arg-type]
        return (float(self.low) + float(self.high)) / 2.0  # type: ignore[arg-type]


class GoalSpec(_Base):
    goal_id: str
    target_variable: str
    direction: GoalDirection = "approach"
    setpoint: Setpoint
    tolerance: float = 0.0
    priority: int = Field(default=5, ge=1, le=9)
    constraints: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tolerance")
    @classmethod
    def _tol(cls, v: float) -> float:
        if v < 0:
            raise ValueError("tolerance 不能为负")
        return v


# --------------------------------------------------------------------------
# 观测
# --------------------------------------------------------------------------


class ObservationSpec(_Base):
    metric_id: str
    source: str = "manual"  # 观测适配器名，manual 表示由 API 提交
    value_type: Literal["float", "int", "bool"] = "float"
    unit: str = ""
    sampling_interval: float = Field(default=5.0, gt=0)
    valid_range: tuple[float, float] | None = None
    freshness_limit: float = Field(default=120.0, gt=0)
    missing_policy: Literal["hold_last", "skip", "stale"] = "hold_last"
    outlier_policy: Literal["flag", "drop", "clamp"] = "flag"
    outlier_method: Literal["sigma3", "iqr"] = "sigma3"
    outlier_window: int = Field(default=12, ge=4)
    importance: Literal["high", "normal", "low"] = "normal"

    @model_validator(mode="after")
    def _range(self) -> "ObservationSpec":
        if self.valid_range is not None and self.valid_range[0] > self.valid_range[1]:
            raise ValueError("valid_range 下界不能大于上界")
        return self


class ObservationIn(_Base):
    """外部提交的观测。ai_inference 的字段校验在这里硬拦。"""

    metric_id: str
    value: float
    source_type: SourceType
    ts: str | None = None
    confidence: float | None = None
    evidence: list[str] | None = None
    is_ground_truth: bool = False
    model: str | None = None
    prompt_version: str | None = None
    note: str | None = None

    @field_validator("value")
    @classmethod
    def _finite(cls, v: float) -> float:
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("value 必须是有限数值")
        return v

    @model_validator(mode="after")
    def _ai_contract(self) -> "ObservationIn":
        if self.source_type != AI_SOURCE:
            # 非 AI 源：可信地来自测量/人工，标记为 ground truth
            self.is_ground_truth = True
            self.confidence = 1.0 if self.confidence is None else self.confidence
            return self
        if self.confidence is None:
            raise ValueError("[FCC_GUARD] ai_inference 观测必须携带 confidence")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("[FCC_GUARD] ai_inference.confidence 必须落在 0~1")
        if not self.evidence or not [e for e in self.evidence if e and e.strip()]:
            raise ValueError("[FCC_GUARD] ai_inference 观测必须携带至少 1 条 evidence")
        if self.is_ground_truth:
            raise ValueError("[FCC_GUARD] ai_inference 的 is_ground_truth 恒为 false")
        return self


class ObservationRecord(BaseModel):
    """入库后的观测（含质量标记）。"""

    model_config = ConfigDict(extra="ignore")

    id: int | None = None
    loop_id: str
    metric_id: str
    value: float
    source_type: SourceType
    confidence: float
    evidence: list[str] = Field(default_factory=list)
    is_ground_truth: bool
    ts: str
    quality_flags: list[str] = Field(default_factory=list)
    stale: bool = False
    model: str | None = None
    prompt_version: str | None = None

    @property
    def is_ai(self) -> bool:
        return self.source_type == AI_SOURCE

    @property
    def usable_as_ground_truth(self) -> bool:
        """只有非 AI 源、且没被判为坏数据，才算硬证据。"""
        if self.source_type == AI_SOURCE:
            return False
        return not ({"outlier", "rejected"} & set(self.quality_flags))


# --------------------------------------------------------------------------
# 控制器 / 调度 / 执行 / 验证 配置
# --------------------------------------------------------------------------


class ControllerConfig(_Base):
    # 用 str 而不是 Literal：允许外部通过 register_controller 注册自定义控制器，
    # 合法性在 Engine 建 loop 时用 BUILTIN ∪ 已注册集合校验（见 engine._check_controller_kind）。
    kind: str = "threshold"
    params: dict[str, Any] = Field(default_factory=dict)
    strategy_version: int = 1


class SchedulerConfig(_Base):
    mode: Literal["manual", "fixed", "event", "threshold", "adaptive", "cron"] = "manual"
    interval_seconds: float = Field(default=30.0, gt=0)
    interval_unit: Literal["second", "minute", "hour"] = "second"
    min_interval_seconds: float = Field(default=5., gt=0)
    max_interval_seconds: float = Field(default=600., gt=0)
    severity_gain: float = Field(default=2., ge=0)
    uncertainty_gain: float = Field(default=1., ge=0)
    cron_expression: str = "0 * * * *"
    cron_timezone: str = "UTC"

    @model_validator(mode="after")
    def _sched_bounds(self):
        if self.min_interval_seconds > self.max_interval_seconds:
            raise ValueError("min_interval_seconds > max_interval_seconds")
        if self.mode == "cron":
            from .scheduler import CronTrigger
            CronTrigger(self.cron_expression, timezone=self.cron_timezone)
        return self

    def interval_in_seconds(self) -> float:
        mult = {"second": 1.0, "minute": 60.0, "hour": 3600.0}[self.interval_unit]
        return self.interval_seconds * mult


class ActuatorConfig(_Base):
    permission_mode: PermissionMode = "require_approval"
    allow_ai_auto: bool = False
    adapter: str = "manual"
    timeout_seconds: float = Field(default=10.0, gt=0)
    max_retries: int = Field(default=1, ge=0, le=5)
    retry_backoff_seconds: float = Field(default=0.0, ge=0)
    action_range: tuple[float, float] | None = None
    max_step: float | None = Field(default=None, gt=0)  # 单次最大调整幅度
    # 同一目标上允许同时挂起（未结案）的同类动作数。默认 1 = 前一个动作没看到效果
    # 就不许再叠一个（需求 §7）。调高只在确有并行执行需求时才合理。
    max_pending_actions: int = Field(default=1, ge=1, le=100)

    @model_validator(mode="after")
    def _valid_limits(self) -> "ActuatorConfig":
        if self.action_range is not None and self.action_range[0] > self.action_range[1]:
            raise ValueError("action_range 下界不能超过上界")
        return self


class VerificationConfig(_Base):
    delay_cycles: int = Field(default=1, ge=0, le=50)
    delay_seconds: float = Field(default=0.0, ge=0)
    noise_k: float = Field(default=2.0, gt=0)  # |delta| < noise_k*noise 视为 unchanged


class LoopConfig(_Base):
    name: str
    goals: list[GoalSpec]
    observations: list[ObservationSpec]
    controller: ControllerConfig = Field(default_factory=ControllerConfig)
    scheduler: SchedulerConfig = Field(default_factory=SchedulerConfig)
    actuator: ActuatorConfig = Field(default_factory=ActuatorConfig)
    verification: VerificationConfig = Field(default_factory=VerificationConfig)
    enabled: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _integrity(self) -> "LoopConfig":
        if not self.goals:
            raise ValueError("至少需要一个目标")
        if self.controller.kind in ("pid", "mpc", "adaptive_arx") and len(self.goals) != 1:
            raise ValueError("当前引擎的 PID/MPC/Adaptive ARX 适配器仅支持单目标 SISO；数学库另支持 MIMO")
        if not self.observations:
            raise ValueError("至少需要一个观测变量")

        ids: set[str] = set()
        for g in self.goals:
            if g.goal_id in ids:
                raise ValueError(f"goal_id 重复: {g.goal_id}")
            ids.add(g.goal_id)

        metrics = {o.metric_id for o in self.observations}
        for g in self.goals:
            if g.target_variable not in metrics:
                raise ValueError(
                    f"目标 {g.goal_id} 的目标变量 {g.target_variable} 未注册为观测变量"
                )

        # 目标冲突检测：同一目标变量上方向矛盾
        by_var: dict[str, set[str]] = {}
        for g in self.goals:
            by_var.setdefault(g.target_variable, set()).add(g.direction)
        for var, dirs in by_var.items():
            if "increase" in dirs and "decrease" in dirs:
                raise ValueError(
                    f"目标冲突：目标变量 {var} 同时要求 increase 与 decrease"
                )

        metric_ids = [o.metric_id for o in self.observations]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("观测变量 metric_id 重复")
        return self


# --------------------------------------------------------------------------
# 运行时结构
# --------------------------------------------------------------------------


class ErrorRecord(BaseModel):
    goal_id: str
    target_variable: str
    direction: GoalDirection
    value: float | None = None
    setpoint: dict[str, Any] = Field(default_factory=dict)
    error: float | None = None  # 正=需要增大
    tolerance: float = 0.0
    out_of_tolerance: bool = False
    uncertainty: float = 0.0
    uncertainty_reason: list[str] = Field(default_factory=list)
    evidence_sources: list[str] = Field(default_factory=list)  # 支持该偏差的源
    ai_only: bool = False  # 只有 ai_inference 支持该偏差
    rate_of_change: float | None = None
    cumulative_error: float = 0.0
    oscillation_detected: bool = False
    over_correction: bool = False
    stale: bool = False
    missing: bool = False
    notes: list[str] = Field(default_factory=list)


class ActionProposal(BaseModel):
    action_id: str  # 决策内局部 id（动作类型+目标）
    action_type: str = "set_value"
    target: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    expected_effect: str = ""
    risk_level: Literal["low", "medium", "high"] = "medium"
    source_type: SourceType = "measured"
    origin_detail: str = ""
    rollback_method: str | None = None
    reason: str = ""
    goal_id: str | None = None
    proposed_value: float | None = None
    previous_value: float | None = None
    idempotency_key: str | None = None


class VerificationRecord(BaseModel):
    action_id: str = ""
    verdict: Literal["improved", "unchanged", "worsened", "failed", "inconclusive", "pending"]
    baseline: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    delta: dict[str, Any] = Field(default_factory=dict)
    noise_estimate: float | None = None
    notes: list[str] = Field(default_factory=list)


class CycleResult(BaseModel):
    cycle_id: int
    loop_id: str
    started_at: str
    finished_at: str | None = None
    target: dict[str, Any] = Field(default_factory=dict)
    observations: dict[str, Any] = Field(default_factory=dict)
    estimated_state: dict[str, Any] = Field(default_factory=dict)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    uncertainty: float = 0.0
    anomalies: list[str] = Field(default_factory=list)
    proposed_actions: list[dict[str, Any]] = Field(default_factory=list)
    executed_actions: list[dict[str, Any]] = Field(default_factory=list)
    expected_outcome: dict[str, Any] = Field(default_factory=dict)
    verification_status: str = "pending_verification"
    # 本 loop 迄今所有动作的结论分布（verification_status 只描述"本轮"，
    # 单看它永远看不到 improved/worsened，这里补一份累计视角）
    verification_summary: dict[str, int] = Field(default_factory=dict)
    next_evaluation_at: str | None = None
    human_review_required: bool = False
    should_intervene: bool = False
    verifications: list[dict[str, Any]] = Field(default_factory=list)
    audit_ref: list[int] = Field(default_factory=list)
    guard_events: list[str] = Field(default_factory=list)
