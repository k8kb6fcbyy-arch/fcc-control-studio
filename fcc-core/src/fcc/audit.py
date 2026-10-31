"""审计。所有环节都往这里写一条，kind 固定枚举，便于机器消费。"""

from __future__ import annotations

from typing import Any

from .store import Store

KINDS = (
    "loop_created",
    "loop_updated",
    "loop_state",
    "observation_received",
    "observation_rejected",
    "cycle_started",
    "cycle_finished",
    "error_computed",
    "decision",
    "action_proposed",
    "action_blocked",
    "action_awaiting_approval",
    "action_approved",
    "action_rejected",
    "action_executed",
    "action_failed",
    "verification",
    "rollback",
    "guard",
    "human_feedback",
    "loop_error",
)


class AuditLog:
    def __init__(self, store: Store) -> None:
        self.store = store

    def write(self, loop_id: str, kind: str, actor: str,
              payload: dict[str, Any] | None = None, **extra: Any) -> int:
        if kind not in KINDS:
            raise ValueError(f"未知审计类型: {kind}")
        body = dict(payload or {})
        body.update(extra)
        return self.store.add_audit(loop_id, kind, actor, body)

    def history(self, loop_id: str, limit: int = 200, kind: str | None = None):
        return self.store.list_audit(loop_id, limit=limit, kind=kind)

    def chain(self, cycle_id: int):
        return self.store.audit_chain(cycle_id)
