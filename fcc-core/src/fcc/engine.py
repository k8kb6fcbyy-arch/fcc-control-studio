"""Engine：把一个控制周期的八步串起来。

建目标 → 观测 → 偏差 → 建议 → 审批 → 执行 → 验证 → 记录

这里只做编排，业务判断分散在 goals / observations / comparator / controllers /
actuator / verification / learning 里，任何一层都能单独 import 测试。
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any, Callable

from . import util
from .actuator import REGISTRY, Actuator, AdapterRegistry
from .audit import AuditLog
from .capability import capabilities_payload
from .comparator import Comparator
from .controllers import (
    BUILTIN as BUILTIN_KINDS,
)
from .controllers import (
    AdvisorFn,
    ControllerContext,
    build_controller,
)
from .goals import GoalManager, setpoint_error
from .learning import LearningManager
from .models import (
    ActionProposal,
    CycleResult,
    GoalSpec,
    LoopConfig,
    ObservationIn,
    ObservationSpec,
    VerificationRecord,
)
from .observations import ObservationManager
from .scheduler import Scheduler
from .store import Store
from .verification import Verifier, summarize_verdicts

log = logging.getLogger("fcc.engine")

GUARD = "[FCC_GUARD]"

ObservationFn = Callable[[str, ObservationSpec], dict[str, Any]]


class LoopNotFound(KeyError):
    pass


class Engine:
    def __init__(self, store: Store, registry: AdapterRegistry | None = None) -> None:
        self.store = store
        self.registry = registry if registry is not None else AdapterRegistry()
        self.audit = AuditLog(store)
        self.obs_adapters: dict[str, ObservationFn] = {}
        self.ai_advisors: dict[str, AdvisorFn] = {}
        self.custom_controllers: dict[str, Any] = {}
        self._lock = threading.RLock()
        self._loop_locks: dict[str, threading.Lock] = {}

    # -- 注册 -------------------------------------------------------------

    def register_observation_adapter(self, name: str, fn: ObservationFn) -> None:
        self.obs_adapters[name] = fn

    def register_actuator(self, name: str, adapter: Any) -> None:
        self.registry.register(name, adapter)

    def register_controller(self, kind: str, cls: Any) -> None:
        self.custom_controllers[kind] = cls

    def register_ai_advisor(self, name: str, fn: AdvisorFn) -> None:
        self.ai_advisors[name] = fn

    # -- loop 生命周期 ----------------------------------------------------

    def create_loop(self, config: LoopConfig | dict[str, Any]) -> str:
        cfg = config if isinstance(config, LoopConfig) else LoopConfig.model_validate(config)
        GoalManager.check_conflicts(cfg.goals)  # 冲突检测前置，失败即拒绝建 loop
        self._check_controller_kind(cfg.controller.kind)
        loop_id = f"loop_{uuid.uuid4().hex[:12]}"
        self.store.save_loop(loop_id, cfg, cfg.enabled)
        self.audit.write(loop_id, "loop_created", "api",
                         {"name": cfg.name, "goals": [g.goal_id for g in cfg.goals],
                          "controller": cfg.controller.kind,
                          "permission_mode": cfg.actuator.permission_mode})
        return loop_id

    def update_config(self, loop_id: str, patch: dict[str, Any]) -> LoopConfig:
        cfg, enabled, _ = self._require(loop_id)
        merged = cfg.model_dump()
        _deep_merge(merged, patch)
        new_cfg = LoopConfig.model_validate(merged)
        GoalManager.check_conflicts(new_cfg.goals)
        self._check_controller_kind(new_cfg.controller.kind)
        self.store.save_loop(loop_id, new_cfg, enabled)
        self.audit.write(loop_id, "loop_updated", "api", {"patch": patch})
        return new_cfg

    def set_enabled(self, loop_id: str, enabled: bool, actor: str = "api") -> None:
        self._require(loop_id)
        self.store.set_enabled(loop_id, enabled)
        self.audit.write(loop_id, "loop_state", actor,
                         {"enabled": enabled, "cycle": "start/pause/stop"})

    def delete_loop(self, loop_id: str) -> None:
        self._require(loop_id)
        self.store.delete_loop(loop_id)

    def _require(self, loop_id: str) -> tuple[LoopConfig, bool, str]:
        loaded = self.store.get_loop_config(loop_id)
        if loaded is None:
            raise LoopNotFound(loop_id)
        return loaded

    def _loop_lock(self, loop_id: str) -> threading.Lock:
        with self._lock:
            return self._loop_locks.setdefault(loop_id, threading.Lock())

    # -- 观测 -------------------------------------------------------------

    def submit_observation(self, loop_id: str, data: ObservationIn | dict[str, Any],
                           actor: str = "api") -> dict[str, Any]:
        cfg, _, _ = self._require(loop_id)
        mgr = ObservationManager(self.store, loop_id, cfg.observations)
        record = mgr.ingest(data, actor)
        self.audit.write(
            loop_id, "observation_received", actor,
            {"metric_id": record.metric_id, "value": record.value,
             "source_type": record.source_type, "confidence": record.confidence,
             "evidence": record.evidence, "quality_flags": record.quality_flags,
             "is_ground_truth": record.is_ground_truth},
        )
        return {"ok": True, "observation_id": record.id, "quality_flags": record.quality_flags}

    def _pull_adapters(self, loop_id: str, cfg: LoopConfig, actor: str) -> list[str]:
        errors: list[str] = []
        for spec in cfg.observations:
            if spec.source == "manual":
                continue
            fn = self.obs_adapters.get(spec.source)
            if fn is None:
                errors.append(f"{spec.metric_id}: 未注册观测适配器 {spec.source}")
                continue
            try:
                raw = fn(loop_id, spec)
            except Exception as exc:  # noqa: BLE001 - 适配器不可信
                errors.append(f"{spec.metric_id}: 适配器异常 {type(exc).__name__}: {exc}")
                continue
            if raw is None:
                continue
            raw.setdefault("metric_id", spec.metric_id)
            try:
                self.submit_observation(loop_id, raw, actor=f"adapter:{spec.source}")
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{spec.metric_id}: 被拒绝 {exc}")
        return errors

    # -- 一周期的核心 -----------------------------------------------------

    def run_cycle(self, loop_id: str, now: str | None = None,
                  event_key: str | None = None) -> CycleResult:
        """手动/事件触发跑一轮。event_key 相同的重复事件不会产生第二个周期。"""
        if event_key:
            existing = self.store.find_cycle_by_event(loop_id, event_key)
            if existing is not None:
                existing["already_ran"] = True
                return CycleResult.model_validate(_cycle_result_payload(existing))
        lock = self._loop_lock(loop_id)
        if not lock.acquire(blocking=False):
            raise RuntimeError(f"loop {loop_id} 正在运行，拒绝重入")
        try:
            return self._run_cycle_locked(loop_id, now, event_key)
        finally:
            lock.release()

    def _run_cycle_locked(self, loop_id: str, now: str | None,
                          event_key: str | None = None) -> CycleResult:
        cfg, _, _ = self._require(loop_id)
        now = now or util.utcnow_iso()
        cycle_seq = self.store.cycle_count(loop_id) + 1
        audit_refs: list[int] = []

        cycle_id = self.store.insert_cycle(loop_id, {
            "started_at": now, "verification_status": "running",
            "audit_ref": [], "target": {}, "observations": {},
            "event_key": event_key,
        })
        audit_refs.append(self.audit.write(loop_id, "cycle_started", "engine",
                                           {"cycle_id": cycle_id, "seq": cycle_seq}))

        goals = GoalManager(cfg.goals)
        mgr = ObservationManager(self.store, loop_id, cfg.observations)
        anomalies: list[str] = []

        try:
            anomalies.extend(self._pull_adapters(loop_id, cfg, "adapter"))
            snapshots = mgr.snapshot_all(now)
            obs_dict = {k: v.as_dict() for k, v in snapshots.items()}
            for snap in snapshots.values():
                if snap.stale:
                    anomalies.append(f"stale:{snap.metric_id}")
                if snap.missing:
                    anomalies.append(f"missing:{snap.metric_id}")

            # ---- 第 6 步：验证上一批动作（必须在产生新动作之前）----
            verifier = Verifier(self.store, loop_id, mgr,
                                noise_k=cfg.verification.noise_k,
                                delay_cycles=cfg.verification.delay_cycles,
                                delay_seconds=cfg.verification.delay_seconds)
            verifications = verifier.verify_pending(cfg.goals, cycle_seq, now)

            # ---- 第 3 步：偏差 ----
            comparator = Comparator(self.store, loop_id, goals)
            errors, summary = comparator.evaluate(snapshots, now)
            error_dicts = [e.model_dump() for e in errors]
            for err in errors:
                audit_refs.append(self.audit.write(
                    loop_id, "error_computed", "comparator",
                    {"cycle_id": cycle_id, "goal_id": err.goal_id,
                     "error": err.error, "tolerance": err.tolerance,
                     "out_of_tolerance": err.out_of_tolerance,
                     "ai_only": err.ai_only, "uncertainty": err.uncertainty},
                ))
                if err.oscillation_detected:
                    anomalies.append(f"oscillation:{err.goal_id}")

            # ---- 第 4 步：建议 ----
            controller = self._build_controller(loop_id, cfg)
            ctx = ControllerContext(
                loop_id=loop_id,
                cycle_seq=cycle_seq,
                goals=cfg.goals,
                errors=errors,
                measurements={k: (v.value if v.value is not None else float("nan"))
                              for k, v in snapshots.items()},
                measurement_trust={k: bool(v.record and v.record.usable_as_ground_truth
                                           and not v.stale and not v.missing)
                                   for k, v in snapshots.items()},
                current_control=self._current_control(loop_id, cfg),
                last_action_cycle=self._last_action_cycles(loop_id),
                prev_in_band=self._prev_in_band(loop_id, goals),
                oscillation=bool(summary["oscillation_goals"]),
                action_range=cfg.actuator.action_range,
                max_step=cfg.actuator.max_step,
                dt_seconds=self._pid_dt(loop_id, cfg, now),
                pending_control=bool(self.store.actions_pending_verification(loop_id) or
                    self.store.actions_awaiting_approval(loop_id) or
                    any(a.get("status") == "outcome_unknown"
                        for a in self.store.list_actions(loop_id, limit=1000))),
            )
            output = controller.propose(ctx)
            proposal_dicts = [p.model_dump() for p in output.proposals]

            # ---- 第 5 步：审批裁决（规则 B）----
            actuator = Actuator(self.store, loop_id, cfg.actuator, self.registry)
            guard_events: list[str] = []
            executed: list[dict[str, Any]] = []
            ai_only_goals = set(summary["ai_only_goals"])
            pending_blocks: list[dict[str, Any]] = []

            for proposal in output.proposals:
                ref, record = self._handle_proposal(
                    loop_id, cycle_id, cycle_seq, cfg, proposal, actuator,
                    ai_only_goals, snapshots, errors,
                )
                audit_refs.append(ref)
                guard_events.extend(record.pop("guard_events", []))
                if record.get("status") == "blocked_pending":
                    pending_blocks.append(record)
                if record.get("action_id") is not None:
                    # 只要真的落了 action 行就记进本轮清单（含待审批/失败），
                    # 否则人工复核标记会漏掉"等审批"的动作。
                    executed.append(record)

            # Persist state only if the controller had the opportunity to act, or
            # if it merely refreshed derivative state while already in tolerance.
            # When a proposed PID action is blocked, discard this cycle's integral.
            if cfg.controller.kind in ("pid", "mpc", "adaptive_arx") and output.next_state is not None:
                if cfg.controller.kind != "pid" or not output.proposals or any(r.get("action_id") is not None for r in executed):
                    self.store.set_controller_state(loop_id, cfg.controller.kind,
                        cfg.controller.strategy_version, output.next_state)

            # ---- 第 8 步：记录 ----
            should_intervene = bool(summary["should_intervene"])
            human_review = bool(
                summary["human_review_required"] or output.awaiting_human
                or any(r.get("status") == "awaiting_approval" for r in executed)
            )
            verification_status = self._cycle_verification_status(
                executed, verifications, blocked=bool(pending_blocks)
            )
            verification_summary = self._verification_summary(loop_id)
            next_at = Scheduler(self.store).next_evaluation(loop_id, cfg.scheduler, now)

            result = CycleResult(
                cycle_id=cycle_id,
                loop_id=loop_id,
                started_at=now,
                finished_at=util.utcnow_iso(),
                target=goals.target_snapshot(),
                observations=obs_dict,
                estimated_state=({"current_control": ctx.current_control,
                                  **(output.estimate or {"method":"direct_measurement"})}),
                errors=error_dicts,
                uncertainty=float(summary["uncertainty"]),
                anomalies=anomalies,
                proposed_actions=proposal_dicts,
                executed_actions=executed,
                expected_outcome=self._expected_outcome(cfg, errors),
                verification_status=verification_status,
                verification_summary=verification_summary,
                next_evaluation_at=next_at,
                human_review_required=human_review,
                should_intervene=should_intervene,
                verifications=verifications,
                audit_ref=audit_refs,
                guard_events=guard_events,
            )

            audit_refs.append(self.audit.write(
                loop_id, "cycle_finished", "engine",
                {"cycle_id": cycle_id, "should_intervene": should_intervene,
                 "verification_status": verification_status,
                 "verification_summary": verification_summary,
                 "executed": [e["action_id"] for e in executed],
                 "guard_events": guard_events, "notes": output.notes,
                 # 观测快照写进审计链，保证 A7 能从审计还原"观测"这一环
                 "observations": obs_dict,
                 "errors": [{"goal_id": e.goal_id, "error": e.error} for e in errors]},
            ))
            self.store.update_cycle(cycle_id, result.model_dump())
            return result

        except Exception as exc:  # noqa: BLE001 - 单 loop 失败不许静默
            self.store.update_cycle(cycle_id, {
                "finished_at": util.utcnow_iso(),
                "verification_status": "error",
                "human_review_required": True,
            })
            self.audit.write(loop_id, "loop_error", "engine",
                             {"cycle_id": cycle_id, "error": f"{type(exc).__name__}: {exc}"})
            log.exception("loop %s 周期失败", loop_id)
            raise

    # -- 单个提案的完整处置 ------------------------------------------------

    def _handle_proposal(
        self,
        loop_id: str,
        cycle_id: int,
        cycle_seq: int,
        cfg: LoopConfig,
        proposal: ActionProposal,
        actuator: Actuator,
        ai_only_goals: set[str],
        snapshots: dict[str, Any],
        errors: list[Any],
    ) -> tuple[int, dict[str, Any]]:
        record: dict[str, Any] = {
            "action_id": None, "goal_id": proposal.goal_id,
            "status": "skipped", "source_type": proposal.source_type,
            "proposed_value": proposal.proposed_value, "executed": False,
            "guard_events": [],
        }

        # 规则 A 的第二道闸：AI 独占证据的目标，任何控制器都不许出动作
        if proposal.goal_id in ai_only_goals:
            msg = f"{GUARD} ai_only_deviation_blocked: goal={proposal.goal_id}"
            record["status"] = "blocked"
            record["guard_events"].append(msg)
            return self.audit.write(loop_id, "action_blocked", "engine",
                                    {"cycle_id": cycle_id, "reason": msg,
                                     "proposal": proposal.model_dump()}), record

        # A measured controller must have a trustworthy CURRENT target reading.
        # Historical measured samples cannot legitimize a new AI/outlier reading.
        goal = next((g for g in cfg.goals if g.goal_id == proposal.goal_id), None)
        current_snap = snapshots.get(goal.target_variable) if goal is not None else None
        if proposal.source_type == "measured" and (
            current_snap is None or current_snap.record is None
            or not current_snap.record.usable_as_ground_truth
            or current_snap.stale or current_snap.missing
        ):
            msg = f"{GUARD} untrusted_current_observation: goal={proposal.goal_id}"
            record["status"] = "blocked"
            record["guard_events"].append(msg)
            return self.audit.write(loop_id, "action_blocked", "engine",
                                    {"cycle_id": cycle_id, "reason": msg,
                                     "proposal": proposal.model_dump()}), record

        # 需求 §7：前一次动作的效果还没观察到，不许再叠一个同类动作
        pending_conflict = self._pending_action_conflict(loop_id, cfg, proposal)
        if pending_conflict is not None:
            msg = (f"{GUARD} pending_action_blocked goal={proposal.goal_id} "
                   f"action={pending_conflict['id']}")
            record["status"] = "blocked_pending"
            record["guard_events"].append(msg)
            record["blocked_by_action"] = pending_conflict["id"]
            return self.audit.write(loop_id, "action_blocked", "engine",
                                    {"cycle_id": cycle_id, "goal_id": proposal.goal_id,
                                     "event": "pending_action_blocked",
                                     "blocked_by_action": pending_conflict["id"],
                                     "reason": msg,
                                     "proposal": proposal.model_dump()}), record

        decision = actuator.decide(proposal)
        record["guard_events"].extend(decision.guard_events)
        record["decision"] = decision.mode

        if decision.required_approval and self._has_pending_for_goal(loop_id, proposal.goal_id):
            record["status"] = "already_pending"
            return self.audit.write(loop_id, "action_blocked", "engine",
                                    {"cycle_id": cycle_id,
                                     "reason": "该目标已有一个待审批动作，本轮不重复堆积",
                                     "goal_id": proposal.goal_id}), record

        key = proposal.idempotency_key or f"{loop_id}|{cycle_seq}|{proposal.action_id}"
        action_id = self.store.insert_action(
            loop_id=loop_id,
            cycle_id=cycle_id,
            proposal=proposal,
            status=actuator.status_for_mode(decision),
            required_approval=decision.required_approval,
            idempotency_key=key,
            decision_reason=decision.reason,
            strategy_version=cfg.controller.strategy_version,
        )
        if action_id is None:
            record["status"] = "duplicate"
            return self.audit.write(loop_id, "action_blocked", "engine",
                                    {"cycle_id": cycle_id, "reason": "幂等键重复",
                                     "idempotency_key": key}), record

        record["action_id"] = action_id
        record["status"] = actuator.status_for_mode(decision)
        record["required_approval"] = decision.required_approval

        ref = self.audit.write(loop_id, "action_proposed", "engine",
                               {"cycle_id": cycle_id, "action_id": action_id,
                                "source_type": proposal.source_type,
                                "decision": decision.mode,
                                "reason": decision.reason,
                                "guard_events": decision.guard_events,
                                "params": proposal.params})

        if decision.guard_events:
            self.audit.write(loop_id, "guard", "actuator",
                             {"cycle_id": cycle_id, "action_id": action_id,
                              "events": decision.guard_events})

        if decision.mode == "auto":
            self._execute_action(loop_id, action_id, proposal, snapshots, errors, cycle_seq)
            row = self.store.get_action(action_id) or {}
            record["status"] = row.get("status")
            record["executed"] = row.get("status") in ("pending_verification", "verified")
        elif decision.required_approval:
            self.audit.write(loop_id, "action_awaiting_approval", "engine",
                             {"cycle_id": cycle_id, "action_id": action_id,
                              "reason": decision.reason})
        return ref, record

    def _execute_action(self, loop_id: str, action_id: int, proposal: ActionProposal,
                        snapshots: dict[str, Any], errors: list[Any],
                        cycle_seq: int) -> None:
        cfg, _, _ = self._require(loop_id)
        actuator = Actuator(self.store, loop_id, cfg.actuator, self.registry)
        # Capture baseline BEFORE actuator changes the external system.
        err = next((e for e in errors if e.goal_id == proposal.goal_id), None)
        snap = snapshots.get(err.target_variable) if err else None
        self._attach_baseline(loop_id, action_id, cycle_seq, snap, err)
        res = actuator.execute(action_id, actor="engine")
        if res.get("unknown"):
            # A timeout can still finish later. Never label it as definite failure.
            return
        if not res.get("ok"):
            # 执行失败也要落一条 verification 记录，否则统计里看不到 failed
            self.store.insert_verification(
                loop_id, action_id, cycle_seq,
                VerificationRecord(
                    action_id=str(action_id), verdict="failed",
                    baseline={}, after={}, delta={},
                    notes=[f"动作执行失败：{res.get('error')}"]),
            )
            return

    def _attach_baseline(self, loop_id: str, action_id: int, cycle_seq: int,
                         snap: Any = None, err: Any = None) -> None:
        """把"动作生效前的观测"写成基线；没有基线就无法验证，必须显式记下来。"""
        if snap is None or err is None:
            cfg, _, _ = self._require(loop_id)
            action = self.store.get_action(action_id) or {}
            goal = next((g for g in cfg.goals if g.goal_id == action.get("goal_id")), None)
            if goal is not None:
                mgr = ObservationManager(self.store, loop_id, cfg.observations)
                snap = mgr.snapshot(goal.target_variable)
                err = type("E", (), {
                    "error": (setpoint_error(goal.setpoint, snap.value)
                              if snap.value is not None else None)})()
        trusted = bool(snap is not None and getattr(snap, "record", None)
                       and snap.record.usable_as_ground_truth
                       and not getattr(snap, "stale", True))
        baseline = {
            "value": getattr(snap, "value", None) if trusted else None,
            "error": getattr(err, "error", None) if trusted else None,
            "ts": snap.record.ts if getattr(snap, "record", None) else None,
            "cycle_seq": cycle_seq,
        }
        row = self.store.get_action(action_id) or {}
        result = dict(row.get("result") or {})
        result["baseline"] = baseline
        result["baseline_cycle"] = cycle_seq
        self.store.update_action(action_id, result_json=util.dumps(result))

    # -- 状态查询 ---------------------------------------------------------

    def _check_controller_kind(self, kind: str) -> None:
        known = set(BUILTIN_KINDS) | set(self.custom_controllers)
        if kind not in known:
            raise ValueError(
                f"未知控制器类型 {kind}；已内置 {sorted(BUILTIN_KINDS)}，"
                "自定义控制器需先 register_controller(name, class)"
            )

    def _has_pending_for_goal(self, loop_id: str, goal_id: str | None) -> bool:
        for action in self.store.actions_awaiting_approval(loop_id):
            if action.get("goal_id") == goal_id:
                return True
        return False

    def _pending_action_conflict(self, loop_id: str, cfg: LoopConfig,
                                 proposal: ActionProposal) -> dict[str, Any] | None:
        """同一目标上是否有"还没结案"的同类动作挡路（需求 §7）。

        返回挡路的动作行，没有冲突则返回 None。判据只有一条：
        同一 goal、同一 action_type 的 pending_verification 动作数量
        达到 `actuator.max_pending_actions`（默认 1）就拒绝再提。
        动作一旦被验证结案（verified/failed）或人驳回，下一轮自动放行。
        """
        goal_id = proposal.goal_id
        if goal_id is None:
            return None
        limit = max(1, int(cfg.actuator.max_pending_actions))
        blockers = [
            action for action in self.store.actions_pending_verification(loop_id)
            if action.get("goal_id") == goal_id
            and action.get("action_type") == proposal.action_type
        ]
        blockers.extend(
            action for action in self.store.list_actions(loop_id, limit=1000)
            if action.get("status") == "outcome_unknown"
            and action.get("goal_id") == goal_id
            and action.get("action_type") == proposal.action_type
        )
        if len(blockers) < limit:
            return None
        return min(blockers, key=lambda a: a["id"])

    def _verification_summary(self, loop_id: str) -> dict[str, int]:
        return summarize_verdicts(self.store.verification_breakdown(loop_id))

    def _build_controller(self, loop_id: str, cfg: LoopConfig):
        kind = cfg.controller.kind
        advisor = None
        if kind == "ai_assisted":
            name = cfg.controller.params.get("advisor")
            advisor = self.ai_advisors.get(name) if name else None
        if kind in self.custom_controllers:
            return self.custom_controllers[kind](cfg.controller.params)
        params = dict(cfg.controller.params)
        if kind in ("pid", "mpc", "adaptive_arx"):
            params["_runtime_state"] = self.store.get_controller_state(
                loop_id, kind, cfg.controller.strategy_version)
        return build_controller(kind, params, advisor=advisor)

    def _pid_dt(self, loop_id: str, cfg: LoopConfig, now: str) -> float:
        # Current cycle already inserted. Compare previous cycle timestamps;
        # use explicit configured interval on the first cycle.
        cycles = self.store.list_cycles(loop_id, limit=2)
        if len(cycles) >= 2:
            dt = util.seconds_between(cycles[1]["started_at"], now)
            if 0 < dt < 1e8:
                return dt
        return float(cfg.controller.params.get("sample_time_seconds", 1.0))

    def _current_control(self, loop_id: str, cfg: LoopConfig) -> float | None:
        adapter = self.registry.get(cfg.actuator.adapter)
        if adapter is not None:
            try:
                state = adapter.state()
                if state.get("current_value") is not None:
                    return float(state["current_value"])
            except Exception:  # noqa: BLE001
                pass
        for action in self.store.list_actions(loop_id, limit=50):
            result = action.get("result") or {}
            if result.get("applied_value") is not None:
                return float(result["applied_value"])
        return None

    def _last_action_cycles(self, loop_id: str) -> dict[str, int]:
        """goal_id -> 最近一次动作发生在第几轮（用于冷却判定，重启后可重建）。"""
        cycles = sorted(c["id"] for c in self.store.list_cycles(loop_id, 1000))
        seq_of = {cid: i + 1 for i, cid in enumerate(cycles)}
        out: dict[str, int] = {}
        for action in self.store.list_actions(loop_id, limit=100):
            goal = action.get("goal_id")
            if not goal or goal in out:
                continue
            out[goal] = seq_of.get(action["cycle_id"], 0)
        return out

    def _prev_in_band(self, loop_id: str, goals: GoalManager) -> dict[str, bool]:
        last = self.store.last_cycle(loop_id)
        if not last:
            return {}
        out: dict[str, bool] = {}
        for err in last.get("errors", []):
            if err.get("error") is None:
                continue
            tol = float(err.get("tolerance", 0.0))
            out[str(err["goal_id"])] = abs(float(err["error"])) <= tol
        return out

    @staticmethod
    def _expected_outcome(cfg: LoopConfig, errors: list[Any]) -> dict[str, Any]:
        return {
            "description": "期望 |error| 在下一轮下降",
            "predicted_value": None,
            "note": "数值模型预测只在已配置 MPC/相关估计器时产生；预测不等于实际验证",
            "goals": [e.goal_id for e in errors if e.out_of_tolerance],
        }

    @staticmethod
    def _cycle_verification_status(executed: list[dict[str, Any]],
                                   verifications: list[dict[str, Any]],
                                   blocked: bool = False) -> str:
        """本轮验证状态的合成值。

        第 6 步（验证上一批动作）在第 4/5 步（产生新动作）之前，所以同一轮
        完全可能"刚给旧动作结了案"（verdict）又"提了新动作"（pending）——
        这两个事实都得留下。verdict 一律在最前（`verified:` 前缀不破），
        用 `+` 追加本轮同时发生的其它情况：
            verified:<v...>+action_pending+exec_failed
        """
        has_pending = any(r.get("status") == "pending_verification" for r in executed)
        has_failed = any(r.get("status") == "failed" for r in executed)
        has_unknown = any(r.get("status") == "outcome_unknown" for r in executed)
        if verifications:
            verdicts = sorted({v["verdict"] for v in verifications})
            status = "verified:" + ",".join(verdicts)
            if has_pending:
                status += "+action_pending"
            if has_failed:
                status += "+exec_failed"
            if has_unknown:
                status += "+outcome_unknown"
            return status
        if has_pending:
            return "pending_verification"
        if has_unknown:
            return "outcome_unknown"
        if has_failed:
            return "failed"
        if any(r.get("status") in ("awaiting_approval", "proposed") for r in executed):
            return "awaiting_human"
        if blocked:
            # 本轮没出动作是因为上一个动作还在等结论，不能报成"什么都没发生"
            return "blocked_pending_action"
        return "no_action"

    # -- 审批 / 人工反馈 ---------------------------------------------------

    def approve_action(self, action_id: int, by: str = "human") -> dict[str, Any]:
        action = self.store.get_action(action_id)
        if action is None:
            return {"ok": False, "error": "action 不存在"}
        if action["status"] != "awaiting_approval":
            return {"ok": False, "error": f"当前状态 {action['status']} 不可审批"}
        loop_id = action["loop_id"]
        cfg, _, _ = self._require(loop_id)
        self.store.update_action(
            action_id, status="approved", approval_by=by,
            approval_at=util.utcnow_iso(),
        )
        self.audit.write(loop_id, "action_approved", by,
                         {"action_id": action_id, "cycle_id": action["cycle_id"],
                          "source_type": action["source_type"]})
        actuator = Actuator(self.store, loop_id, cfg.actuator, self.registry)
        # Baseline must be recorded before the actual approved actuation.
        self._attach_baseline(loop_id, action_id, self.store.cycle_count(loop_id))
        res = actuator.execute(action_id, actor=by)
        return {"ok": bool(res.get("ok")), "action": self.store.get_action(action_id),
                "execution": res}

    def reject_action(self, action_id: int, by: str = "human", reason: str = "") -> dict[str, Any]:
        action = self.store.get_action(action_id)
        if action is None:
            return {"ok": False, "error": "action 不存在"}
        if action["status"] not in ("awaiting_approval", "proposed"):
            return {"ok": False, "error": f"当前状态 {action['status']} 不可驳回"}
        self.store.update_action(
            action_id, status="rejected", approval_by=by,
            approval_at=util.utcnow_iso(), verification_status="rejected",
            decision_reason=reason or "人工驳回",
        )
        self.audit.write(action["loop_id"], "action_rejected", by,
                         {"action_id": action_id, "reason": reason,
                          "cycle_id": action["cycle_id"]})
        return {"ok": True, "action": self.store.get_action(action_id)}

    def submit_human_feedback(self, loop_id: str, feedback: dict[str, Any]) -> dict[str, Any]:
        self._require(loop_id)
        decision = str(feedback.get("decision", "approve"))
        note = str(feedback.get("note", ""))
        action_id = feedback.get("action_id")

        if action_id is not None:
            action = self.store.get_action(int(action_id))
            if action is None or action.get("loop_id") != loop_id:
                return {"ok": False, "error": "动作不存在或不属于该控制实例"}
            if decision == "reject":
                return self.reject_action(int(action_id), "human", note)
            return self.approve_action(int(action_id), "human")

        # 无 action_id：人工直接给一个设定值 -> 建 human_input 动作
        value = feedback.get("value")
        goal_id = feedback.get("goal_id")
        if value is None:
            return {"ok": False, "error": "缺少 action_id 时必须提供 value"}
        cfg, _, _ = self._require(loop_id)
        proposal = ActionProposal(
            action_id=f"human:{uuid.uuid4().hex[:8]}",
            action_type="set_value",
            target="control_input",
            params={"value": float(value), "previous": self._current_control(loop_id, cfg)},
            expected_effect="人工设定的执行量",
            risk_level=str(feedback.get("risk_level", "medium")),
            source_type="human_input",
            origin_detail="human_feedback",
            rollback_method=feedback.get("rollback_method", "restore_previous_value"),
            reason=note or "人工反馈",
            goal_id=goal_id,
            proposed_value=float(value),
            idempotency_key=feedback.get("idempotency_key"),
        )
        cycle_seq = self.store.cycle_count(loop_id) + 1
        last = self.store.last_cycle(loop_id)
        cycle_id = last["id"] if last else self.store.insert_cycle(
            loop_id, {"started_at": util.utcnow_iso(), "verification_status": "human_input"})
        actuator = Actuator(self.store, loop_id, cfg.actuator, self.registry)
        used_key = proposal.idempotency_key or f"{loop_id}|human|{uuid.uuid4().hex[:8]}"
        aid = self.store.insert_action(
            loop_id=loop_id, cycle_id=cycle_id, proposal=proposal,
            status="approved", required_approval=False,
            idempotency_key=used_key, decision_reason="人工直接下发",
            strategy_version=cfg.controller.strategy_version,
        )
        if aid is None:
            return {"ok": False, "error": "幂等键重复，未产生新动作"}
        self.audit.write(loop_id, "human_feedback", "human",
                         {"action_id": aid, "value": value, "note": note,
                          "goal_id": goal_id, "cycle_id": cycle_id})
        self._attach_baseline(loop_id, aid, self.store.cycle_count(loop_id))
        res = actuator.execute(aid, actor="human")
        return {"ok": bool(res.get("ok")), "action": self.store.get_action(aid),
                "execution": res}

    # -- 回滚 -------------------------------------------------------------

    def rollback_action(self, action_id: int, by: str = "api") -> dict[str, Any]:
        action = self.store.get_action(action_id)
        if action is None:
            return {"ok": False, "error": "action 不存在"}
        cfg, _, _ = self._require(action["loop_id"])
        actuator = Actuator(self.store, action["loop_id"], cfg.actuator, self.registry)
        return actuator.rollback(action_id, actor=by)

    def rollback_strategy(self, loop_id: str, version: int) -> dict[str, Any]:
        self._require(loop_id)
        versions = {int(v["version"]): v for v in self.store.list_strategy_versions(loop_id)}
        if version not in versions:
            return {"ok": False, "error": f"策略版本 {version} 不存在"}
        if not self.store.activate_strategy_version(loop_id, version):
            return {"ok": False, "error": "激活失败"}
        cfg = LoopConfig.model_validate(util.loads(versions[version]["config_json"], {}))
        _, enabled, _ = self._require(loop_id)
        self.store.save_loop(loop_id, cfg, enabled)  # 保留原有启停状态
        self.audit.write(loop_id, "rollback", "api",
                         {"target": "strategy", "version": version})
        return {"ok": True, "active_version": version}

    # -- 查询 -------------------------------------------------------------

    def get_state(self, loop_id: str) -> dict[str, Any]:
        cfg, enabled, updated = self._require(loop_id)
        mgr = ObservationManager(self.store, loop_id, cfg.observations)
        snaps = mgr.snapshot_all()
        history = self.store.list_cycles(loop_id, 60)
        config_payload = cfg.model_dump()
        # config_json 里的 enabled 是创建时的快照，set_enabled 不会刷新它。
        # 对外只暴露一份真相：顶层 enabled 是运行时值，config.enabled 跟着它走。
        config_payload["enabled"] = enabled
        return {
            "loop_id": loop_id,
            "name": cfg.name,
            "enabled": enabled,
            "updated_at": updated,
            "config": config_payload,
            "observations": {k: v.as_dict() for k, v in snaps.items()},
            "history": list(reversed(history)),
            "last_cycle": history[0] if history else None,
            "pending_approvals": self.store.actions_awaiting_approval(loop_id),
            "strategy_stats": LearningManager(self.store, loop_id).strategy_stats(),
            "capabilities": capabilities_payload(),
        }

    def get_history(self, loop_id: str, limit: int = 50) -> list[dict[str, Any]]:
        self._require(loop_id)
        return list(reversed(self.store.list_cycles(loop_id, limit)))

    def get_verification_report(self, loop_id: str) -> dict[str, Any]:
        cfg, _, _ = self._require(loop_id)
        mgr = ObservationManager(self.store, loop_id, cfg.observations)
        verifier = Verifier(self.store, loop_id, mgr, noise_k=cfg.verification.noise_k)
        report = verifier.report()
        report["learning"] = LearningManager(self.store, loop_id).strategy_stats()
        return report

    def audit_chain(self, loop_id: str, cycle_id: int) -> dict[str, Any]:
        self._require(loop_id)
        cycle = self.store.get_cycle(cycle_id)
        return {
            "cycle": cycle,
            "chain": self.store.audit_chain(cycle_id),
            "actions": self.store.actions_of_cycle(cycle_id),
        }

    def tick(self, now: str | None = None) -> list[str]:
        """调度一拍：fixed / adaptive / cron 到期 loop，单例失败隔离。"""
        now = now or util.utcnow_iso()
        ran: list[str] = []
        for row in self.store.list_loops():
            if not row["enabled"]:
                continue
            loop_id = row["id"]
            try:
                cfg, enabled, _ = self._require(loop_id)
                if not enabled:
                    continue
                if not Scheduler(self.store).is_due(loop_id, cfg.scheduler, now):
                    continue
                self.run_cycle(loop_id, now)
                ran.append(loop_id)
            except Exception:  # noqa: BLE001 - A10：一个 loop 炸了不能影响别的
                log.exception("tick: loop %s 执行失败（已隔离）", loop_id)
                try:
                    self.audit.write(loop_id, "loop_error", "scheduler",
                                     {"stage": "tick"})
                except Exception:  # noqa: BLE001
                    pass
        return ran


def _cycle_result_payload(cycle: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "cycle_id", "loop_id", "started_at", "finished_at", "target", "observations",
        "estimated_state", "errors", "uncertainty", "anomalies", "proposed_actions",
        "executed_actions", "expected_outcome", "verification_status",
        "verification_summary", "next_evaluation_at", "human_review_required",
        "should_intervene", "verifications", "audit_ref", "guard_events",
    }
    payload = {k: cycle.get(k) for k in keys if k in cycle}
    payload["cycle_id"] = cycle.get("cycle_id") or cycle.get("id")
    payload["loop_id"] = cycle.get("loop_id")
    return payload


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value

