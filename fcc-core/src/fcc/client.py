"""Python SDK。与 HTTP 通道语义一致（同一份 Engine 实现）。"""

from __future__ import annotations

from typing import Any

from .actuator import AdapterRegistry
from .capability import capabilities_payload
from .engine import Engine
from .models import LoopConfig
from .store import Store


class Client:
    """轻量门面。可以多实例指向不同 db 文件，互不影响（A10）。"""

    def __init__(self, db_path: str = "fcc.db", registry: AdapterRegistry | None = None,
                 engine: Engine | None = None) -> None:
        if engine is not None:
            self.engine = engine
            self.store = engine.store
        else:
            self.store = Store(db_path)
            self.engine = Engine(self.store, registry)

    # -- 生命周期 ---------------------------------------------------------

    def create_loop(self, config: LoopConfig | dict[str, Any]) -> str:
        return self.engine.create_loop(config)

    def start_loop(self, loop_id: str) -> None:
        self.engine.set_enabled(loop_id, True, "client")

    def pause_loop(self, loop_id: str) -> None:
        self.engine.set_enabled(loop_id, False, "client")

    def resume_loop(self, loop_id: str) -> None:
        self.engine.set_enabled(loop_id, True, "client")

    def stop_loop(self, loop_id: str) -> None:
        self.engine.set_enabled(loop_id, False, "client")

    def delete_loop(self, loop_id: str) -> None:
        self.engine.delete_loop(loop_id)

    # -- 运行 -------------------------------------------------------------

    def run_cycle(self, loop_id: str, event_key: str | None = None,
                  now: str | None = None):
        """now 仅用于模拟时钟（演示"时间流逝"），生产环境不要传。"""
        return self.engine.run_cycle(loop_id, now=now, event_key=event_key)

    def submit_observation(self, loop_id: str, data: dict[str, Any]) -> dict[str, Any]:
        return self.engine.submit_observation(loop_id, data)

    def submit_human_feedback(self, loop_id: str, feedback: dict[str, Any]) -> dict[str, Any]:
        return self.engine.submit_human_feedback(loop_id, feedback)

    def approve_action(self, action_id: int, by: str = "human") -> dict[str, Any]:
        return self.engine.approve_action(action_id, by)

    def reject_action(self, action_id: int, by: str = "human",
                      reason: str = "") -> dict[str, Any]:
        return self.engine.reject_action(action_id, by, reason)

    # -- 注册 -------------------------------------------------------------

    def register_observation_adapter(self, name: str, fn) -> None:
        self.engine.register_observation_adapter(name, fn)

    def register_controller(self, kind: str, cls) -> None:
        self.engine.register_controller(kind, cls)

    def register_actuator(self, name: str, adapter) -> None:
        self.engine.register_actuator(name, adapter)

    def register_ai_advisor(self, name: str, fn) -> None:
        self.engine.register_ai_advisor(name, fn)

    # -- 查询 -------------------------------------------------------------

    def list_loops(self) -> list[dict[str, Any]]:
        return self.store.list_loops()

    def get_state(self, loop_id: str) -> dict[str, Any]:
        return self.engine.get_state(loop_id)

    def get_history(self, loop_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.engine.get_history(loop_id, limit)

    def get_verification_report(self, loop_id: str) -> dict[str, Any]:
        return self.engine.get_verification_report(loop_id)

    def get_capabilities(self) -> dict[str, Any]:
        return capabilities_payload()

    # -- 变更 -------------------------------------------------------------

    def update_config(self, loop_id: str, config: dict[str, Any]) -> LoopConfig:
        return self.engine.update_config(loop_id, config)

    def rollback_strategy(self, loop_id: str, version: int) -> dict[str, Any]:
        return self.engine.rollback_strategy(loop_id, version)

    def rollback_action(self, action_id: int, by: str = "client") -> dict[str, Any]:
        return self.engine.rollback_action(action_id, by)

    def close(self) -> None:
        self.store.close()
