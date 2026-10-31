"""Actuator：权限裁决（规则 B）+ 幂等 + 超时 + 重试 + 范围限制 + 回滚登记。

规则 B 落点：来源为 `ai_inference` 的提案，即使是 `auto` 模式也强制降级为
`require_approval`，并写 `[FCC_GUARD] ai_action_downgraded`。
唯一例外必须**三个条件同时成立**：allow_ai_auto、risk_level=low、登记了回滚方法。
"""

from __future__ import annotations

import logging
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from typing import Any, Protocol, runtime_checkable

from . import util
from .models import ActionProposal, ActuatorConfig
from .store import Store

log = logging.getLogger("fcc.actuator")

GUARD_PREFIX = "[FCC_GUARD]"
_SHARED_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="fcc-act")


@runtime_checkable
class ActuatorAdapter(Protocol):
    def apply(self, action: dict[str, Any]) -> dict[str, Any]: ...
    def state(self) -> dict[str, Any]: ...
    def rollback(self, action: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]: ...


class ManualAdapter:
    """默认适配器：不驱动任何真实对象，只把动作记下来。"""

    name = "manual"

    def __init__(self) -> None:
        self.applied: list[dict[str, Any]] = []
        self._value: float | None = None

    def apply(self, action: dict[str, Any]) -> dict[str, Any]:
        value = action.get("params", {}).get("value")
        self.applied.append({"value": value, "at": util.utcnow_iso()})
        previous = self._value
        self._value = float(value) if value is not None else previous
        return {"ok": True, "applied_value": self._value, "previous_value": previous,
                "detail": "manual adapter: 仅记录"}

    def state(self) -> dict[str, Any]:
        return {"current_value": self._value}

    def rollback(self, action: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
        prev = result.get("previous_value")
        self._value = float(prev) if prev is not None else None
        return {"ok": True, "detail": f"manual adapter 回滚到 {prev}"}


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, ActuatorAdapter] = {"manual": ManualAdapter()}
        self._lock = threading.RLock()

    def register(self, name: str, adapter: ActuatorAdapter) -> None:
        with self._lock:
            self._adapters[name] = adapter

    def get(self, name: str) -> ActuatorAdapter | None:
        with self._lock:
            return self._adapters.get(name)

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._adapters)

    def snapshot_states(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in self.names():
            adapter = self.get(name)
            if adapter is None:
                continue
            try:
                out[name] = adapter.state()
            except Exception as exc:  # noqa: BLE001 - 适配器实现不可信
                out[name] = {"error": str(exc)}
        return out


REGISTRY = AdapterRegistry()


def _reason(base: str, guard: list[str]) -> str:
    """把 guard 事件写进决策原因，保证数据库里也能查到降级理由。"""
    if not guard:
        return base
    return base + " | " + " ; ".join(guard)


class Decision:
    __slots__ = ("mode", "required_approval", "reason", "guard_events")

    def __init__(self, mode: str, required_approval: bool, reason: str,
                 guard_events: list[str]) -> None:
        self.mode = mode
        self.required_approval = required_approval
        self.reason = reason
        self.guard_events = guard_events


class Actuator:
    def __init__(self, store: Store, loop_id: str, config: ActuatorConfig,
                 registry: AdapterRegistry | None = None) -> None:
        self.store = store
        self.loop_id = loop_id
        self.config = config
        self.registry = registry or REGISTRY
        self._pool = _SHARED_POOL  # bounded shared threads; timeout does not cancel a running task

    # -- 权限裁决 ---------------------------------------------------------

    def decide(self, proposal: ActionProposal) -> Decision:
        guard: list[str] = []
        mode = self.config.permission_mode

        # High-risk changes are never unreviewed, even if evidence is measured.
        # This includes online adaptive-model changes whose dynamics can drift.
        if proposal.risk_level == "high" and mode == "auto":
            mode = "require_approval"
            guard.append(f"{GUARD_PREFIX} high_risk_action_requires_human")

        if proposal.source_type == "ai_inference":
            exception_ok = (
                self.config.allow_ai_auto
                and proposal.risk_level == "low"
                and bool(proposal.rollback_method)
            )
            if mode == "auto" and not exception_ok:
                mode = "require_approval"
                guard.append(
                    f"{GUARD_PREFIX} ai_action_downgraded: auto -> require_approval "
                    f"(risk={proposal.risk_level}, rollback={proposal.rollback_method})"
                )
                log.warning(
                    "%s loop=%s action=%s 已从 auto 降级为 require_approval",
                    GUARD_PREFIX, self.loop_id, proposal.action_id,
                )
            elif mode == "auto" and exception_ok:
                guard.append(
                    f"{GUARD_PREFIX} ai_action_auto_exception: allow_ai_auto+low+rollback"
                )
            if mode in ("require_approval", "suggest_only"):
                guard.append(f"{GUARD_PREFIX} ai_action_requires_human")

        if mode == "suggest_only":
            return Decision("suggest_only", False,
                            _reason("权限模式 suggest_only：只出建议，不执行", guard), guard)
        if mode == "require_approval":
            return Decision("require_approval", True,
                            _reason("需要人工审批后执行", guard), guard)
        return Decision("auto", False, _reason("auto：自动执行", guard), guard)

    # -- 执行 -------------------------------------------------------------

    def status_for_mode(self, decision: Decision) -> str:
        if decision.mode == "auto":
            return "approved"
        if decision.mode == "require_approval":
            return "awaiting_approval"
        return "proposed"

    def execute(self, action_id: int, actor: str = "engine") -> dict[str, Any]:
        action = self.store.get_action(action_id)
        if action is None:
            return {"ok": False, "error": "action not found"}

        if action["status"] != "approved":
            return {"ok": False, "error": f"状态 {action['status']} 不可执行"}

        value = action["params"].get("value")
        if value is not None:
            try:
                finite_value = math.isfinite(float(value))
            except (TypeError, ValueError, OverflowError):
                finite_value = False
            if not finite_value:
                msg = "执行量不是有限数字，拒绝执行"
                self._fail(action_id, msg)
                return {"ok": False, "error": msg}
        if value is not None and self.config.action_range is not None:
            low, high = self.config.action_range
            if not (low <= float(value) <= high):
                msg = f"动作值 {value} 超出允许范围 [{low}, {high}]，拒绝执行"
                self._fail(action_id, msg)
                self.store.add_audit(self.loop_id, "action_failed", actor,
                                     {"action_id": action_id, "reason": msg})
                return {"ok": False, "error": msg}

        adapter_name = self.config.adapter
        adapter = self.registry.get(adapter_name)
        if adapter is None:
            msg = f"未注册执行适配器 {adapter_name}"
            self._fail(action_id, msg)
            return {"ok": False, "error": msg}

        if value is not None and self.config.max_step is not None:
            try:
                current_value = adapter.state().get("current_value")
            except Exception:
                current_value = None
            if current_value is not None and abs(float(value) - float(current_value)) > self.config.max_step + 1e-12:
                msg = f"动作幅度超出 max_step={self.config.max_step}，拒绝执行"
                self._fail(action_id, msg)
                return {"ok": False, "error": msg}

        self.store.update_action(action_id, status="executing")
        attempts = 0
        last_error = ""
        while attempts <= self.config.max_retries:
            attempts += 1
            try:
                result = self._call_with_timeout(adapter.apply, action)
                if result.get("ok", False):
                    self.store.update_action(
                        action_id,
                        status="pending_verification",
                        verification_status="pending_verification",
                        executed_at=util.utcnow_iso(),
                        result_json=util.dumps({**(action.get("result") or {}), **result, "attempts": attempts}),
                    )
                    self.store.add_audit(
                        self.loop_id, "action_executed", actor,
                        {"action_id": action_id, "attempts": attempts, "before": action["params"],
                         "after": result, "verification_status": "pending_verification"},
                    )
                    return {"ok": True, "status": "pending_verification", "result": result,
                            "attempts": attempts}
                last_error = str(result.get("error") or "adapter returned ok=false")
            except FutureTimeout:
                # Threads cannot be forcibly stopped in Python. The adapter may still
                # apply the action AFTER timeout. Do not retry or report definite failure.
                last_error = f"适配器超时 {self.config.timeout_seconds}s；执行结果未知，禁止自动重试"
                self.store.update_action(action_id, status="outcome_unknown",
                    verification_status="inconclusive",
                    result_json=util.dumps({**(action.get("result") or {}), "ok": None, "error": last_error,
                                            "attempts": attempts, "outcome_unknown": True}))
                self.store.add_audit(self.loop_id, "action_outcome_unknown", actor,
                                     {"action_id": action_id, "attempts": attempts,
                                      "reason": last_error})
                return {"ok": False, "unknown": True, "status": "outcome_unknown",
                        "error": last_error, "attempts": attempts}
            except Exception as exc:  # noqa: BLE001 - 外部适配器不可信
                last_error = f"{type(exc).__name__}: {exc}"
            log.warning("actuator 第 %d 次执行失败 loop=%s action=%s: %s",
                        attempts, self.loop_id, action_id, last_error)
            if self.config.retry_backoff_seconds:
                time.sleep(self.config.retry_backoff_seconds)

        self._fail(action_id, last_error, attempts=attempts)
        self.store.add_audit(self.loop_id, "action_failed", actor,
                             {"action_id": action_id, "attempts": attempts,
                              "error": last_error})
        return {"ok": False, "error": last_error, "attempts": attempts}

    def _call_with_timeout(self, fn, action: dict[str, Any]) -> dict[str, Any]:
        future = self._pool.submit(fn, action)
        try:
            return future.result(timeout=self.config.timeout_seconds)
        except FutureTimeout:
            future.cancel()
            raise

    def _fail(self, action_id: int, reason: str, attempts: int = 0) -> None:
        self.store.update_action(
            action_id,
            status="failed",
            verification_status="failed",
            result_json=util.dumps({**((self.store.get_action(action_id) or {}).get("result") or {}),
                                    "ok": False, "error": reason, "attempts": attempts}),
        )

    # -- 回滚 -------------------------------------------------------------

    def rollback(self, action_id: int, actor: str = "api") -> dict[str, Any]:
        action = self.store.get_action(action_id)
        if action is None:
            return {"ok": False, "error": "action not found"}
        if not action.get("rollback_method"):
            return {"ok": False, "error": "该动作未登记回滚方法，禁止伪造回滚"}
        if action["status"] not in ("pending_verification", "verified", "failed"):
            return {"ok": False, "error": f"状态 {action['status']} 不可回滚"}
        adapter = self.registry.get(self.config.adapter)
        if adapter is None:
            return {"ok": False, "error": f"未注册执行适配器 {self.config.adapter}"}
        try:
            result = adapter.rollback(action, action.get("result") or {})
        except Exception as exc:
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        ok = bool(result.get("ok"))
        self.store.add_audit(self.loop_id, "rollback" if ok else "rollback_failed", actor,
                             {"action_id": action_id, "method": action["rollback_method"],
                              "result": result})
        # Failed rollback must NOT be recorded as rolled_back.
        if ok:
            self.store.update_action(
                action_id, status="rolled_back", verification_status="rolled_back",
                result_json=util.dumps({**(action.get("result") or {}), "rollback": result}),
            )
        return {"ok": ok, "result": result}

    def current_control(self) -> float | None:
        adapter = self.registry.get(self.config.adapter)
        if adapter is None:
            return None
        try:
            state = adapter.state()
        except Exception:  # noqa: BLE001
            return None
        value = state.get("current_value")
        return float(value) if value is not None else None
