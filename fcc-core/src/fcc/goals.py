"""Goal Manager：多目标登记、区间目标、方向、容差、优先级、冲突检测。"""

from __future__ import annotations

from typing import Any, Iterable

from .models import GoalSpec, Setpoint


class GoalConflictError(ValueError):
    """同一目标变量上出现方向矛盾的目标。"""


class GoalManager:
    def __init__(self, goals: Iterable[GoalSpec]) -> None:
        self._goals: list[GoalSpec] = list(goals)
        if not self._goals:
            raise ValueError("至少需要一个目标")
        self.check_conflicts(self._goals)

    # -- 校验 -------------------------------------------------------------

    @staticmethod
    def check_conflicts(goals: Iterable[GoalSpec]) -> None:
        seen: set[str] = set()
        by_var: dict[str, set[str]] = {}
        for g in goals:
            if g.goal_id in seen:
                raise GoalConflictError(f"goal_id 重复: {g.goal_id}")
            seen.add(g.goal_id)
            by_var.setdefault(g.target_variable, set()).add(g.direction)
        for var, dirs in by_var.items():
            if "increase" in dirs and "decrease" in dirs:
                raise GoalConflictError(
                    f"目标冲突：目标变量 {var} 同时要求 increase 与 decrease，"
                    "无法同时满足，请在创建 loop 前裁决"
                )

    # -- 查询 -------------------------------------------------------------

    @property
    def goals(self) -> list[GoalSpec]:
        return list(self._goals)

    def by_id(self, goal_id: str) -> GoalSpec | None:
        return next((g for g in self._goals if g.goal_id == goal_id), None)

    def for_variable(self, target_variable: str) -> list[GoalSpec]:
        return [g for g in self._goals if g.target_variable == target_variable]

    def sorted_by_priority(self) -> list[GoalSpec]:
        # priority 数字越小越优先
        return sorted(self._goals, key=lambda g: (g.priority, g.goal_id))

    def primary(self) -> GoalSpec:
        return self.sorted_by_priority()[0]

    # -- 快照 -------------------------------------------------------------

    def target_snapshot(self) -> dict[str, Any]:
        return {
            g.goal_id: {
                "target_variable": g.target_variable,
                "direction": g.direction,
                "setpoint": g.setpoint.model_dump(),
                "nominal": g.setpoint.nominal(),
                "tolerance": g.tolerance,
                "priority": g.priority,
                "band": [
                    None if g.direction == "decrease" and g.setpoint.kind == "value" else
                    (g.setpoint.low if g.setpoint.kind == "range" else g.setpoint.nominal()) - g.tolerance,
                    None if g.direction == "increase" and g.setpoint.kind == "value" else
                    (g.setpoint.high if g.setpoint.kind == "range" else g.setpoint.nominal()) + g.tolerance,
                ],
            }
            for g in self.sorted_by_priority()
        }


def setpoint_error(setpoint: Setpoint, value: float) -> float:
    """误差符号约定：**正数一律表示「需要把被控量往上推」**。

    单点目标：error = sp - v
    区间目标：落在区间内 error = 0；低于下界 error = low - v（正）；高于上界 error = high - v（负）
    """
    if setpoint.kind == "value":
        return float(setpoint.value) - value  # type: ignore[arg-type]
    low = float(setpoint.low)  # type: ignore[arg-type]
    high = float(setpoint.high)  # type: ignore[arg-type]
    if value < low:
        return low - value
    if value > high:
        return high - value
    return 0.0


def within_tolerance(setpoint: Setpoint, value: float, tolerance: float) -> bool:
    if setpoint.kind == "range":
        return float(setpoint.low) - tolerance <= value <= float(setpoint.high) + tolerance  # type: ignore[arg-type]
    return abs(float(setpoint.value) - value) <= tolerance  # type: ignore[arg-type]
