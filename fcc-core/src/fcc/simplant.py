"""模拟被控对象：一阶惯性 + 时滞 + 噪声（派单包 §6）。

    dx/dt = (gain*u + disturbance - x) / tau
    y     = x(t - dead_time) + N(0, sigma)

刻意做成"真对象"：它有自己的状态、会超时、会失败、会抖动。
框架不允许偷偷读它的内部量 —— 只能通过 `apply()` 下发、通过 `read()` 观测。
"""

from __future__ import annotations

import random
from typing import Any

from . import util


class PlantError(RuntimeError):
    pass


class SimulatedPlant:
    def __init__(
        self,
        *,
        tau: float = 8.0,
        gain: float = 1.0,
        dead_time: float = 2.0,
        noise_sigma: float = 0.2,
        dt: float = 1.0,
        x0: float = 0.0,
        u0: float = 0.0,
        seed: int = 20261030,
    ) -> None:
        self.tau = float(tau)
        self.gain = float(gain)
        self.dead_time = float(dead_time)
        self.noise_sigma = float(noise_sigma)
        self.dt = float(dt)
        self.x = float(x0)
        self.u = float(u0)
        self.disturbance = 0.0
        self.fail_next = 0
        self.fail_mode = "raise"
        self._rng = random.Random(seed)
        delay_steps = max(1, int(round(self.dead_time / self.dt)) + 1)
        self._buffer: list[float] = [self.x] * delay_steps
        self._delay_steps = delay_steps
        self.step_count = 0

    # -- 对象动力学 -------------------------------------------------------

    def advance(self, dt: float | None = None) -> float:
        dt = self.dt if dt is None else float(dt)
        dx = (self.gain * self.u + self.disturbance - self.x) / self.tau
        self.x += dx * dt
        self._buffer.append(self.x)
        if len(self._buffer) > self._delay_steps:
            self._buffer.pop(0)
        self.step_count += 1
        return self.x

    def true_value(self) -> float:
        """内部真值（只给测试/场景脚本对照用，框架代码不许调用）。"""
        return self._buffer[0]

    def read(self) -> dict[str, Any]:
        noise = self._rng.gauss(0.0, self.noise_sigma) if self.noise_sigma > 0 else 0.0
        return {
            "value": self.true_value() + noise,
            "true_value": self.true_value(),
            "u": self.u,
            "step": self.step_count,
        }

    # -- 适配器协议 -------------------------------------------------------

    def apply(self, action: dict[str, Any]) -> dict[str, Any]:
        if self.fail_next > 0:
            self.fail_next -= 1
            if self.fail_mode == "raise":
                raise PlantError("模拟对象拒绝执行（注入故障）")
            return {"ok": False, "error": "模拟对象返回 ok=false（注入故障）"}
        value = action.get("params", {}).get("value")
        if value is None:
            return {"ok": False, "error": "缺少 value"}
        previous = self.u
        self.u = float(value)
        return {
            "ok": True,
            "applied_value": self.u,
            "previous_value": previous,
            "detail": f"plant u: {previous:.4g} -> {self.u:.4g}",
        }

    def state(self) -> dict[str, Any]:
        return {"current_value": self.u, "x": util.round_sig(self.x), "step": self.step_count}

    def rollback(self, action: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        prev = result.get("previous_value")
        if prev is None:
            return {"ok": False, "error": "没有 previous_value 可回滚"}
        self.u = float(prev)
        return {"ok": True, "detail": f"plant u 回滚到 {self.u:.4g}"}

    # -- 场景辅助 ---------------------------------------------------------

    def inject_disturbance(self, amount: float) -> None:
        self.disturbance = float(amount)

    def set_noise(self, sigma: float) -> None:
        self.noise_sigma = float(sigma)

    def inject_failures(self, times: int = 1, mode: str = "raise") -> None:
        self.fail_next = int(times)
        self.fail_mode = mode
