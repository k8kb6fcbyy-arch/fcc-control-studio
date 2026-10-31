"""Controller 层：基础规则、PID、约束 MPC 与自适应 ARX。

设计约束（派单包 §2.2 规则 A / §3.2）：
- 控制器只产出 `ActionProposal`，不执行、不审批、不判成功；
- `ai_assisted` 产出的提案，`source_type` 由代码强制写成 `ai_inference`，
  **不接受 params 覆盖**，防止用配置把 AI 动作伪装成 measured。
"""

from __future__ import annotations

import uuid
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from . import util
from .theory import DigitalPID, StateSpace
from .advanced import ConstrainedMPC, DiscreteKalmanFilter, RecursiveLeastSquares, SelfTuningARX
import numpy as np
from .models import ActionProposal, ErrorRecord, GoalSpec


class Controller(Protocol):
    STATUS: str
    KIND: str

    def propose(self, ctx: "ControllerContext") -> "ControllerOutput": ...


@dataclass
class ControllerOutput:
    proposals: list[ActionProposal] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    awaiting_human: bool = False
    next_state: dict[str, Any] | None = None
    estimate: dict[str, Any] | None = None


@dataclass
class ControllerContext:
    """控制器能看到的全部信息。刻意不传 store —— 控制器不该自己去查库。"""

    loop_id: str
    cycle_seq: int
    goals: list[GoalSpec]
    errors: list[ErrorRecord]
    measurements: dict[str, float]
    measurement_trust: dict[str, bool] = field(default_factory=dict)
    current_control: float | None = None  # 被控对象当前的执行量 u
    last_action_cycle: dict[str, int] = field(default_factory=dict)  # goal_id -> cycle_seq
    prev_in_band: dict[str, bool] = field(default_factory=dict)
    oscillation: bool = False
    action_range: tuple[float, float] | None = None
    max_step: float | None = None
    dt_seconds: float = 1.0
    pending_control: bool = False


def _mk_proposal(
    goal: GoalSpec,
    proposed_value: float,
    current: float | None,
    *,
    source_type: str,
    origin_detail: str,
    reason: str,
    risk_level: str = "medium",
    rollback_method: str | None = None,
) -> ActionProposal:
    return ActionProposal(
        action_id=f"{goal.goal_id}:{uuid.uuid4().hex[:8]}",
        action_type="set_value",
        target="control_input",
        params={"value": proposed_value, "previous": current},
        expected_effect=f"把 {goal.target_variable} 推向 {goal.setpoint.nominal()}",
        risk_level=risk_level,  # type: ignore[arg-type]
        source_type=source_type,  # type: ignore[arg-type]
        origin_detail=origin_detail,
        rollback_method=rollback_method,
        reason=reason,
        goal_id=goal.goal_id,
        proposed_value=proposed_value,
        previous_value=current,
    )


# --------------------------------------------------------------------------
# 1. Monitor Only
# --------------------------------------------------------------------------


class MonitorController:
    """只观察，不动作。用于灰度接入、观察期。"""

    STATUS = "implemented"
    KIND = "monitor"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params = params or {}

    def propose(self, ctx: ControllerContext) -> ControllerOutput:
        out = ControllerOutput()
        for err in ctx.errors:
            if err.out_of_tolerance:
                out.notes.append(f"{err.goal_id}: 超出容差 {err.error:+.4g}，monitor 模式不动作")
        return out


# --------------------------------------------------------------------------
# 2. Threshold（死区 + 迟滞 + 冷却 + 比例步长）
# --------------------------------------------------------------------------


class ThresholdController:
    """带死区/迟滞/冷却的阈值控制器。

    动作律是纯比例 P：u_new = u + gain * error。
    注意：这是**比例步长**，不是 PID —— 积分/微分、抗积分饱和在 PIDController 里
    在 PIDController 中实现，不能混为一谈。
    """

    STATUS = "implemented"
    KIND = "threshold"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        p = params or {}
        self.gain = float(p.get("proportional_gain", 0.5))
        self.deadband = float(p.get("deadband", 0.0))
        self.hysteresis = float(p.get("hysteresis", 0.0))
        self.cooldown_cycles = int(p.get("cooldown_cycles", 0))
        self.min_delta = float(p.get("min_delta", 0.0))
        # law:
        #   tracking    u = sp + K*e   （P + 设定值前馈，假设执行量与目标量纲一致）
        #   incremental u = u + K*e    （只知道该往哪边推、不知道增益时用）
        self.law = str(p.get("law", "tracking"))

    def propose(self, ctx: ControllerContext) -> ControllerOutput:
        out = ControllerOutput()
        if ctx.oscillation:
            out.notes.append("检测到振荡，本轮抑制动作，转人工复核")
            return out

        for err in ctx.errors:
            if err.missing or err.stale:
                out.notes.append(f"{err.goal_id}: 观测缺失/stale，跳过")
                continue
            if err.ai_only:
                out.notes.append(
                    f"{err.goal_id}: [FCC_GUARD] 偏差仅由 ai_inference 支持，不产生动作"
                )
                continue
            if err.error is None:
                continue

            # 迟滞：上一轮还在带内，需要误差突破 容差+hysteresis 才重新介入
            if ctx.prev_in_band.get(err.goal_id):
                threshold = err.tolerance + self.hysteresis
                if abs(err.error) <= threshold:
                    out.notes.append(f"{err.goal_id}: 迟滞带内（|e|={abs(err.error):.4g}），保持")
                    continue

            if abs(err.error) <= err.tolerance + self.deadband:
                out.notes.append(f"{err.goal_id}: 死区内（|e|={abs(err.error):.4g}），不动作")
                continue

            last = ctx.last_action_cycle.get(err.goal_id)
            if last is not None and ctx.cycle_seq - last <= self.cooldown_cycles:
                out.notes.append(f"{err.goal_id}: 冷却中（{ctx.cycle_seq - last} 轮）")
                continue

            delta = self.gain * err.error
            if abs(delta) < self.min_delta:
                out.notes.append(f"{err.goal_id}: 调整量 {delta:.4g} 小于 min_delta，丢弃")
                continue
            if ctx.max_step is not None:
                delta = util.clamp(delta, -ctx.max_step, ctx.max_step)

            current = ctx.current_control
            base = _setpoint_nominal(err.setpoint)
            if self.law == "tracking" and base is not None:
                proposed = float(base) + delta
                note = f"u {base:.4g}{delta:+.4g} = {proposed:.4g}（tracking，P+前馈）"
            elif current is None:
                proposed = delta
                note = "无当前执行量读数，按绝对量下发"
            else:
                proposed = current + delta
                note = f"u {current:.4g} -> {proposed:.4g}（Δ={delta:+.4g}）"
            if ctx.action_range is not None:
                proposed = util.clamp(proposed, *ctx.action_range)

            goal = _goal(ctx.goals, err.goal_id)
            if goal is None:
                continue
            out.proposals.append(
                _mk_proposal(
                    goal,
                    proposed,
                    current,
                    source_type="measured",
                    origin_detail="threshold",
                    reason=f"|e|={abs(err.error):.4g} > 容差 {err.tolerance:.4g}；{note}",
                    risk_level="medium",
                    rollback_method="restore_previous_value",
                )
            )
            out.notes.append(f"{err.goal_id}: 提出调整 {note}")
        return out


# --------------------------------------------------------------------------
# 3. Rule-Based
# --------------------------------------------------------------------------


class RuleController:
    """声明式规则表。规则来自配置，不是硬编码。

    params["rules"] = [
      {"id": "r1", "if": {"metric": "pressure", "op": "gt", "value": 90},
       "action": {"type": "set_value", "value": 60, "risk": "high"},
       "reason": "..."} ,
      {"id": "r2", "if": {"goal": "g1", "op": "error_abs_gt", "value": 5}, ...}
    ]
    首条命中即执行（按配置顺序），避免互相打架。
    """

    STATUS = "implemented"
    KIND = "rule"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.rules: list[dict[str, Any]] = list((params or {}).get("rules", []))

    def propose(self, ctx: ControllerContext) -> ControllerOutput:
        out = ControllerOutput()
        for rule in self.rules:
            if not self._matches(rule.get("if", {}), ctx):
                continue
            act = rule.get("action", {})
            goal_id = act.get("goal_id") or rule.get("goal_id")
            goal = _goal(ctx.goals, goal_id) if goal_id else (ctx.goals[0] if ctx.goals else None)
            if goal is None:
                continue
            value = act.get("value")
            if value is None:
                out.notes.append(f"规则 {rule.get('id')} 命中但缺少 action.value，忽略")
                continue
            try:
                finite_value = math.isfinite(float(value))
            except (TypeError, ValueError, OverflowError):
                finite_value = False
            if not finite_value:
                out.notes.append(f"规则 {rule.get('id')} 动作值无效")
                continue
            out.proposals.append(
                _mk_proposal(
                    goal,
                    float(value),
                    ctx.current_control,
                    source_type="measured",
                    origin_detail=f"rule:{rule.get('id', '?')}",
                    reason=rule.get("reason", f"规则 {rule.get('id')} 命中"),
                    risk_level=str(act.get("risk", "medium")),
                    rollback_method=act.get("rollback", "restore_previous_value"),
                )
            )
            out.notes.append(f"规则 {rule.get('id')} 命中 -> 目标值 {value}")
            return out  # 首条命中即停
        out.notes.append("无规则命中")
        return out

    @staticmethod
    def _matches(cond: dict[str, Any], ctx: ControllerContext) -> bool:
        if not cond:
            return False
        op = cond.get("op", "gt")
        if "metric" in cond:
            metric = str(cond["metric"])
            if ctx.measurement_trust.get(metric) is False:
                return False  # AI/stale/outlier readings cannot authorize a measured rule
            actual = ctx.measurements.get(metric)
        elif "goal" in cond:
            err = next((e for e in ctx.errors if e.goal_id == cond["goal"]), None)
            if err is None or err.error is None or err.ai_only or err.stale or err.missing:
                return False
            actual = abs(err.error) if op.startswith("error_") else err.error
            op = op.replace("error_", "") if op.startswith("error_") else op
        else:
            return False
        if actual is None or not math.isfinite(actual):
            return False
        target = cond.get("value", 0)
        try:
            target_f = float(target)
        except (TypeError, ValueError):
            return False
        return {
            "gt": actual > target_f,
            "ge": actual >= target_f,
            "lt": actual < target_f,
            "le": actual <= target_f,
            "eq": abs(actual - target_f) < 1e-9,
            "ne": abs(actual - target_f) >= 1e-9,
        }.get(op, False)


# --------------------------------------------------------------------------
# 4. AI-Assisted（只是通道，不是裁判）
# --------------------------------------------------------------------------


AdvisorFn = Callable[[ControllerContext], list[dict[str, Any]]]


class AIAssistedController:
    """把外部 AI 的建议转成提案。

    硬约束：
    - `source_type` **恒为 ai_inference**，忽略 params 里的任何覆盖；
    - 建议必须带 confidence 与 evidence，否则整条丢弃并记录；
    - 提案只会被送去审批（规则 B），本控制器无权签发放行。
    """

    STATUS = "implemented"
    KIND = "ai_assisted"

    def __init__(self, params: dict[str, Any] | None = None,
                 advisor: AdvisorFn | None = None) -> None:
        p = params or {}
        self.advisor = advisor
        self.static_suggestions = list(p.get("static_suggestions", []))
        self.min_confidence = float(p.get("min_confidence", 0.6))

    def propose(self, ctx: ControllerContext) -> ControllerOutput:
        out = ControllerOutput()
        raw: list[dict[str, Any]] = []
        if self.advisor is not None:
            raw = self.advisor(ctx) or []
        elif self.static_suggestions:
            raw = self.static_suggestions  # 测试替身，origin 会标 test_stub
        else:
            out.notes.append("未注册 AI advisor，且未配置 static_suggestions，AI 控制器无输出")
            return out

        stub = self.advisor is None
        for item in raw:
            conf = item.get("confidence")
            ev = item.get("evidence")
            try:
                conf_value = float(conf)
                valid_confidence = math.isfinite(conf_value) and 0 <= conf_value <= 1
                valid_evidence = isinstance(ev, list) and any(
                    isinstance(e, str) and e.strip() for e in ev)
            except (TypeError, ValueError, OverflowError):
                valid_confidence, valid_evidence = False, False
            if not valid_confidence or not valid_evidence:
                out.notes.append("[FCC_GUARD] AI 建议 confidence/evidence 无效，已拒绝")
                continue
            if conf_value < self.min_confidence:
                out.notes.append(f"AI 建议置信度 {conf_value} 低于 {self.min_confidence}，丢弃")
                continue
            value = item.get("value")
            try:
                valid_value = math.isfinite(float(value))
            except (TypeError, ValueError, OverflowError):
                valid_value = False
            if not valid_value:
                out.notes.append("[FCC_GUARD] AI 建议动作值不是有限数值，已拒绝")
                continue
            goal = _goal(ctx.goals, item.get("goal_id")) or (
                ctx.goals[0] if ctx.goals else None
            )
            if value is None or goal is None:
                out.notes.append("AI 建议缺少 value/goal，丢弃")
                continue
            out.proposals.append(
                _mk_proposal(
                    goal,
                    float(value),
                    ctx.current_control,
                    source_type="ai_inference",  # 强制，不允许被配置改写
                    origin_detail=(
                        "ai_assisted:" + ("test_stub" if stub else str(item.get("model", "external")))
                    ),
                    reason=str(item.get("reason", "AI 建议")),
                    risk_level=str(item.get("risk_level", "medium")),
                    rollback_method=item.get("rollback_method"),
                )
            )
        return out


# --------------------------------------------------------------------------
# 5. Human-in-the-Loop
# --------------------------------------------------------------------------


class HumanInLoopController:
    """算得出「该调多少」，但不自己调 —— 挂起等人。

    它不伪造 human_input：提案要等人真的通过
    `submit_human_feedback` 提交后才由 Actuator 建单，source_type=human_input。
    """

    STATUS = "implemented"
    KIND = "human_in_loop"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.gain = float((params or {}).get("proportional_gain", 0.5))

    def propose(self, ctx: ControllerContext) -> ControllerOutput:
        out = ControllerOutput()
        need = [
            e for e in ctx.errors
            if e.out_of_tolerance and not e.missing and not e.stale
        ]
        if not need:
            return out
        out.awaiting_human = True
        for err in need:
            suggestion = None
            if ctx.current_control is not None and err.error is not None:
                suggestion = ctx.current_control + self.gain * err.error
            out.notes.append(
                f"{err.goal_id}: 待人工决策（误差 {err.error:+.4g}，建议值 {suggestion}）"
            )
        return out


# --------------------------------------------------------------------------
# 增强控制器与状态估计
# --------------------------------------------------------------------------


class PIDController:
    """Operational SISO discrete PID (classical discrete-time control).

    The controlled variable must be numerical and the plant's signed response
    to u must be known. Config gains DO NOT imply a stability guarantee.
    State is injected/persisted by Engine with each strategy version.
    """
    STATUS = "implemented"
    KIND = "pid"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params = dict(params or {})
        state = self.params.pop("_runtime_state", {})
        self.pid = DigitalPID(
            kp=float(self.params.get("kp", 0.0)),
            ki=float(self.params.get("ki", 0.0)),
            kd=float(self.params.get("kd", 0.0)),
            derivative_tau=float(self.params.get("derivative_tau", 0.0)),
            integral_min=float(self.params.get("integral_min", -float("inf"))),
            integral_max=float(self.params.get("integral_max", float("inf"))),
            output_min=float(self.params.get("output_min", -float("inf"))),
            output_max=float(self.params.get("output_max", float("inf"))),
            bias=float(self.params.get("bias", 0.0)),
            integral=float(state.get("integral", 0.0)),
            prev_measurement=state.get("prev_measurement"),
            filtered_derivative=float(state.get("filtered_derivative", 0.0)),
        )
        self.kp, self.ki, self.kd = self.pid.kp, self.pid.ki, self.pid.kd
        self.integral_limit = self.params.get("integral_limit")

    def compute(self, error: float, dt: float) -> float:
        """For compatibility: error input assumes r=error and y=0.

        Prefer step(setpoint, measurement, dt) for correct derivative-on-measurement.
        """
        return self.pid.compute(error, 0.0, dt)["output"]

    def step(self, setpoint: float, measurement: float, dt: float) -> dict:
        return self.pid.compute(setpoint, measurement, dt)

    def propose(self, ctx: ControllerContext) -> ControllerOutput:
        out = ControllerOutput()
        if len(ctx.goals) != 1:
            raise ValueError("PID requires exactly one SISO goal; use separate loops for other goals")
        err = next((e for e in ctx.errors if e.goal_id == ctx.goals[0].goal_id), None)
        if err is None or err.missing or err.stale or err.ai_only or err.error is None:
            out.notes.append("PID requires fresh non-AI numeric observation")
            return out
        if ctx.pending_control:
            out.notes.append("PID paused: previous action pending or outcome unknown")
            return out
        if ctx.oscillation:
            out.notes.append("PID paused: possible oscillation; review tuning")
            return out
        if not err.out_of_tolerance:
            # Maintain derivative state without integral winding under target tolerance.
            self.pid.prev_measurement = err.value
            out.next_state = self.pid.snapshot()
            return out
        if err.value is None:
            return out
        sp = _setpoint_nominal(err.setpoint)
        if sp is None:
            return out
        if ctx.action_range is not None:
            self.pid.output_min = max(self.pid.output_min, ctx.action_range[0])
            self.pid.output_max = min(self.pid.output_max, ctx.action_range[1])
            if self.pid.output_min > self.pid.output_max:
                raise ValueError("PID output limits conflict with actuator limits")
        # Clamp adjustment size relative to actual actuator value, if known.
        if ctx.max_step is not None and ctx.current_control is not None:
            self.pid.output_min = max(self.pid.output_min, ctx.current_control - ctx.max_step)
            self.pid.output_max = min(self.pid.output_max, ctx.current_control + ctx.max_step)
            if self.pid.output_min > self.pid.output_max:
                raise ValueError("PID step limits conflict with output bounds")
        result = self.step(sp, err.value, ctx.dt_seconds)
        out.next_state = self.pid.snapshot()
        proposed = result["output"]
        out.proposals.append(_mk_proposal(
            ctx.goals[0], proposed, ctx.current_control, source_type="measured",
            origin_detail="pid", risk_level="medium",
            rollback_method="restore_previous_value",
            reason=f"PID: e={result['error']:.5g}, dt={ctx.dt_seconds:.5g}, "
                   f"saturated={result['saturated']}",
        ))
        return out


def _single_goal_observation(ctx: ControllerContext):
    if len(ctx.goals) != 1:
        raise ValueError("FCC engine scalar actuator requires exactly one goal for advanced modes")
    goal = ctx.goals[0]
    if not ctx.measurement_trust.get(goal.target_variable, False):
        return goal, None
    value = ctx.measurements.get(goal.target_variable)
    if value is None or not math.isfinite(value):
        return goal, None
    return goal, float(value)


class MPCController:
    """Operational linear constrained MPC with an optional Kalman state estimator.

    Public Python `ConstrainedMPC` supports MIMO. The legacy FCC engine actuator
    has one scalar `set_value`, so this adapter accepts only SISO model/goal.
    Model parameter sample_time must match real observation/action intervals.
    """
    STATUS = "implemented"
    KIND = "mpc"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params = dict(params or {})
        model_cfg = self.params.get("model")
        if not isinstance(model_cfg, dict):
            raise ValueError('MPC requires "model": {A,B,C,D,sample_time}')
        self.model = StateSpace(A=model_cfg["A"], B=model_cfg["B"], C=model_cfg["C"],
                                D=model_cfg.get("D"), domain="discrete",
                                sample_time=float(model_cfg["sample_time"]))
        if self.model.m != 1 or self.model.p != 1:
            raise ValueError("The FCC scalar actuator MPC adapter is SISO; use ConstrainedMPC directly for MIMO")
        if "u_min" not in self.params or "u_max" not in self.params:
            raise ValueError("MPC requires explicit u_min and u_max")
        self.optimizer = ConstrainedMPC(
            model=self.model, horizon=int(self.params.get("horizon", 8)),
            Q=self.params.get("Q", [[1.0]]), R=self.params.get("R", [[0.2]]),
            S=self.params.get("S"), Qf=self.params.get("Qf"),
            u_min=self.params["u_min"], u_max=self.params["u_max"],
            du_min=self.params.get("du_min"), du_max=self.params.get("du_max"),
            y_min=self.params.get("y_min"), y_max=self.params.get("y_max"))
        self.state = self.params.get("_runtime_state", {})
        self.kf_cfg = self.params.get("kalman")
        if self.kf_cfg is not None and not isinstance(self.kf_cfg, dict):
            raise ValueError("kalman must be a config dictionary")
        if self.kf_cfg is None and not (self.model.n == 1 and
                                          np.allclose(self.model.C, [[1.]]) and
                                          np.allclose(self.model.D, [[0.]])):
            raise ValueError("Provide Kalman filter for unobserved states; single-state direct reading only without KF")

    def propose(self, ctx: ControllerContext) -> ControllerOutput:
        import numpy as np
        out = ControllerOutput()
        goal, measurement = _single_goal_observation(ctx)
        if measurement is None:
            out.notes.append('MPC requires fresh independent measurement')
            return out
        if ctx.current_control is None or not math.isfinite(ctx.current_control):
            out.notes.append('MPC requires verified current actuator value; cannot assume zero')
            return out
        if self.state and abs(ctx.dt_seconds - self.model.sample_time) > self.model.sample_time * float(self.params.get('max_dt_error_ratio', 0.25)):
            out.notes.append('MPC timestamp does not match identified model sample period')
            return out
        if self.kf_cfg is not None:
            state = self.state.get('kalman') or {}
            kf = DiscreteKalmanFilter(
                model=self.model, Q=self.kf_cfg['Q'], R=self.kf_cfg['R'],
                x=state.get('x', self.kf_cfg.get('x0', [0.0]*self.model.n)),
                P=state.get('P', self.kf_cfg.get('P0', np.eye(self.model.n).tolist())))
            if state:
                kf.predict([ctx.current_control])
            updated = kf.update([measurement], [ctx.current_control],
                                gate_nis=self.kf_cfg.get('gate_nis'))
            out.next_state = {'kalman':kf.snapshot()}
            out.estimate = {'method':'kalman', **updated}
            if not updated['accepted']:
                out.notes.append('MPC Kalman observation was gated or missing; no new control')
                return out
            x0 = kf.x
        else:
            x0 = np.array([measurement])
            out.estimate = {'method':'direct_state_measurement','x':x0.tolist()}
            out.next_state = {}
        if ctx.pending_control or ctx.oscillation:
            out.notes.append('MPC held due to pending action or observed oscillation')
            return out
        if abs(goal.setpoint.nominal()-measurement) <= goal.tolerance:
            return out
        try:
            solution = self.optimizer.solve(x0, [goal.setpoint.nominal()],
                                            [ctx.current_control],
                                            u_reference=[float(self.params.get('u_reference', 0.0))])
        except (ValueError, RuntimeError, np.linalg.LinAlgError) as exc:
            out.notes.append(f'MPC optimizer held: {exc}')
            out.awaiting_human = True
            return out
        proposed = float(solution['first_control'][0])
        if ctx.action_range:
            if not ctx.action_range[0]-1e-9 <= proposed <= ctx.action_range[1]+1e-9:
                out.notes.append('MPC computed control outside actuator bound; rejected')
                out.awaiting_human = True
                return out
        if ctx.max_step is not None and abs(proposed-ctx.current_control) > ctx.max_step+1e-9:
            out.notes.append('MPC control violates max_step; reject instead of silently clipping plan')
            out.awaiting_human = True
            return out
        if np.isclose(proposed,ctx.current_control,atol=1e-9):
            return out
        out.proposals.append(_mk_proposal(goal,proposed,ctx.current_control,
            source_type='measured',origin_detail='mpc:constrained_slsqp',
            reason=f'MPC first action (horizon={self.optimizer.horizon}); model forecast only',
            risk_level='medium',rollback_method='restore_previous_value'))
        out.estimate['predicted_outputs'] = solution['predicted_outputs'].tolist()
        out.estimate['predicted_controls'] = solution['plan'].tolist()
        return out


class StateSpaceEstimator:
    """Compatibility wrapper: operational Kalman state estimator, not a stub.

    Input u to `update(y,u)` is applied during the preceding sample interval;
    first update assimilates initial y without predicting. Set real Q,R noise
    covariance parameters, do not rely on arbitrary defaults.
    """
    STATUS = 'implemented'

    def __init__(self, a, b, c, *, Q, R, x0, P0, sample_time=1.0, d=None):
        model = StateSpace(a,b,c,d,domain='discrete',sample_time=sample_time)
        self.filter = DiscreteKalmanFilter(model,Q,R,x0,P0)
        self.initial = True

    def update(self, y, u):
        if not self.initial:
            self.filter.predict(np.atleast_1d(u))
        self.initial = False
        return self.filter.update(np.atleast_1d(y), np.atleast_1d(u))['x']

    def snapshot(self):
        return self.filter.snapshot()


class AdaptiveARXController:
    """Operational supervised self-tuning SISO ARX; requires verified input path.

    This approach may be unstable when model shifts; never infer actuator input
    from a proposed (unexecuted) action. The 'current_control' must be a
    measured/current adapter value reflecting the preceding plant interval.
    """
    STATUS = 'implemented'
    KIND = 'adaptive_arx'

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        import numpy as np
        self.params = dict(params or {})
        state = self.params.get('_runtime_state', {})
        theta = state.get('rls',{}).get('theta',self.params.get('theta0',[0.7,0.3,0.0]))
        P = state.get('rls',{}).get('P',self.params.get('P0', (np.eye(3)*100).tolist()))
        rls = RecursiveLeastSquares(theta, P, float(self.params.get('forgetting_factor',0.99)))
        self.adaptive = SelfTuningARX(rls, expected_gain_sign=int(self.params['expected_gain_sign']),
                                      min_abs_gain=float(self.params.get('min_abs_gain',0.03)),
                                      max_abs_a=float(self.params.get('max_abs_a',2.0)),
                                      max_abs_gain=float(self.params.get('max_abs_gain',100.0)),
                                      min_samples=int(self.params.get('min_samples',12)),
                                      samples=int(state.get('samples',0)))
        self.prev_y = state.get('prev_y')
        self.inputs = list(state.get('recent_inputs', []))
        self.input_span = float(self.params.get('min_input_span',1e-3))
        # The adapter must guarantee actual applied u over the preceding
        # interval (not the suggested u). Require explicit acknowledgment.
        self.trusted_applied_input = self.params.get('trusted_applied_input') is True

    def propose(self, ctx: ControllerContext) -> ControllerOutput:
        out = ControllerOutput()
        goal, y = _single_goal_observation(ctx)
        if y is None or ctx.current_control is None or not math.isfinite(ctx.current_control):
            out.notes.append('Adaptive ARX requires verified observation and actual current input')
            return out
        if self.prev_y is not None and self.trusted_applied_input:
            self.adaptive.observe_transition(float(self.prev_y),float(ctx.current_control),y)
        self.inputs = (self.inputs+[float(ctx.current_control)])[-50:]
        out.next_state = {**self.adaptive.snapshot(),'prev_y':y,'recent_inputs':self.inputs}
        out.estimate = {'method':'rls_arx','theta':self.adaptive.rls.theta.tolist(),
                        'samples':self.adaptive.samples}
        if not self.trusted_applied_input:
            out.notes.append('Adaptive ARX is monitor-only: trusted_applied_input not explicitly verified')
            return out
        if ctx.pending_control or ctx.oscillation:
            out.notes.append('Adaptive control paused: action pending or oscillation')
            return out
        if abs(goal.setpoint.nominal()-y) <= goal.tolerance:
            return out
        if (len(self.inputs) < self.adaptive.min_samples or
            max(self.inputs)-min(self.inputs) < self.input_span):
            out.notes.append('Adaptive control stays monitor-only: insufficient input excitation')
            return out
        if ctx.action_range is None or ctx.max_step is None:
            out.notes.append('Adaptive ARX needs both actuator action_range and max_step')
            return out
        try:
            plan = self.adaptive.propose(y,goal.setpoint.nominal(),float(ctx.current_control),
                    u_min=ctx.action_range[0], u_max=ctx.action_range[1], max_step=ctx.max_step)
        except (ValueError,RuntimeError) as exc:
            out.notes.append(f'Adaptive model guard: {exc}')
            out.awaiting_human=True
            return out
        if abs(plan['control']-ctx.current_control) < 1e-9:
            return out
        out.proposals.append(_mk_proposal(goal,plan['control'],ctx.current_control,
                source_type='measured',origin_detail='adaptive_arx:rls',
                reason=f'RLS self-tuning proposal, n={self.adaptive.samples}; verify next plant response',
                risk_level='high',rollback_method='restore_previous_value'))
        out.estimate['predicted_output']=plan['predicted_output']
        return out


def _setpoint_nominal(setpoint: dict[str, Any]) -> float | None:
    """从 ErrorRecord.setpoint 里取出设定值名义点；取不到返回 None。"""
    if not setpoint:
        return None
    if setpoint.get("kind") == "value" and setpoint.get("value") is not None:
        return float(setpoint["value"])
    if setpoint.get("low") is not None and setpoint.get("high") is not None:
        return (float(setpoint["low"]) + float(setpoint["high"])) / 2.0
    return None


def _goal(goals: list[GoalSpec], goal_id: str | None) -> GoalSpec | None:
    if goal_id is None:
        return goals[0] if goals else None
    return next((g for g in goals if g.goal_id == goal_id), None)


BUILTIN: dict[str, type] = {
    "monitor": MonitorController,
    "threshold": ThresholdController,
    "rule": RuleController,
    "ai_assisted": AIAssistedController,
    "human_in_loop": HumanInLoopController,
    "pid": PIDController,
    "mpc": MPCController,
    "adaptive_arx": AdaptiveARXController,
}


def build_controller(kind: str, params: dict[str, Any] | None = None,
                     advisor: AdvisorFn | None = None) -> Controller:
    cls = BUILTIN.get(kind)
    if cls is None:
        raise ValueError(f"未知控制器类型: {kind}")
    if cls is AIAssistedController:
        return AIAssistedController(params, advisor=advisor)  # type: ignore[return-value]
    return cls(params)  # type: ignore[return-value]
