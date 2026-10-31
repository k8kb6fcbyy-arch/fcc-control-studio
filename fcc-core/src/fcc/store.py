"""SQLite 持久化层。

一张连接 + 一把可重入锁：单机单用户场景（派单包 §3.3 明确不做多租户），
用锁换掉连接池带来的不确定性。所有写操作都是事务性的。
"""

from __future__ import annotations

import sqlite3
import threading
from typing import Any, Iterable

from . import util
from .models import (
    ActionProposal,
    LoopConfig,
    ObservationRecord,
    VerificationRecord,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ctrl_loop (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ctrl_goal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loop_id TEXT NOT NULL,
    goal_id TEXT NOT NULL,
    target_variable TEXT NOT NULL,
    setpoint_json TEXT NOT NULL,
    direction TEXT NOT NULL,
    tolerance REAL NOT NULL,
    priority INTEGER NOT NULL,
    constraints_json TEXT NOT NULL,
    success_criteria_json TEXT NOT NULL,
    UNIQUE(loop_id, goal_id)
);

CREATE TABLE IF NOT EXISTS ctrl_observation_spec (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loop_id TEXT NOT NULL,
    metric_id TEXT NOT NULL,
    source TEXT NOT NULL,
    value_type TEXT NOT NULL,
    unit TEXT NOT NULL,
    sampling_interval REAL NOT NULL,
    valid_range_json TEXT,
    freshness_limit REAL NOT NULL,
    missing_policy TEXT NOT NULL,
    outlier_policy TEXT NOT NULL,
    importance TEXT NOT NULL,
    UNIQUE(loop_id, metric_id)
);

CREATE TABLE IF NOT EXISTS ctrl_observation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loop_id TEXT NOT NULL,
    metric_id TEXT NOT NULL,
    value REAL NOT NULL,
    source_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_json TEXT NOT NULL,
    is_ground_truth INTEGER NOT NULL,
    ts TEXT NOT NULL,
    quality_flags_json TEXT NOT NULL,
    model TEXT,
    prompt_version TEXT
);
CREATE INDEX IF NOT EXISTS idx_obs_loop_metric_ts
    ON ctrl_observation(loop_id, metric_id, ts DESC);

CREATE TABLE IF NOT EXISTS ctrl_cycle (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loop_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    target_json TEXT NOT NULL,
    observations_json TEXT NOT NULL,
    estimated_state_json TEXT NOT NULL,
    errors_json TEXT NOT NULL,
    uncertainty REAL NOT NULL,
    anomalies_json TEXT NOT NULL,
    proposed_actions_json TEXT NOT NULL,
    executed_actions_json TEXT NOT NULL,
    expected_outcome_json TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    next_evaluation_at TEXT,
    human_review_required INTEGER NOT NULL DEFAULT 0,
    should_intervene INTEGER NOT NULL DEFAULT 0,
    verifications_json TEXT NOT NULL DEFAULT '[]',
    verification_summary_json TEXT NOT NULL DEFAULT '{}',
    event_key TEXT,
    audit_ref TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ctrl_action (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loop_id TEXT NOT NULL,
    cycle_id INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    target TEXT NOT NULL,
    params_json TEXT NOT NULL,
    expected_effect TEXT NOT NULL,
    risk_level TEXT NOT NULL,
    required_approval INTEGER NOT NULL,
    approval_by TEXT,
    approval_at TEXT,
    status TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    rollback_method TEXT,
    source_type TEXT NOT NULL,
    origin_detail TEXT,
    goal_id TEXT,
    decision_reason TEXT,
    strategy_version INTEGER NOT NULL DEFAULT 1,
    verification_status TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL,
    executed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cycle_event
    ON ctrl_cycle(loop_id, event_key) WHERE event_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_action_loop ON ctrl_action(loop_id, status);
CREATE INDEX IF NOT EXISTS idx_action_cycle ON ctrl_action(cycle_id);

CREATE TABLE IF NOT EXISTS ctrl_verification (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loop_id TEXT NOT NULL,
    action_id INTEGER NOT NULL,
    cycle_id INTEGER,
    baseline_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    verdict TEXT NOT NULL,
    delta_json TEXT NOT NULL,
    noise_estimate REAL,
    notes_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ver_action ON ctrl_verification(action_id);

CREATE TABLE IF NOT EXISTS ctrl_strategy_version (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loop_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    config_json TEXT NOT NULL,
    active INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(loop_id, version)
);

CREATE TABLE IF NOT EXISTS ctrl_controller_state (
    loop_id TEXT PRIMARY KEY,
    controller_kind TEXT NOT NULL,
    strategy_version INTEGER NOT NULL,
    state_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ctrl_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loop_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_loop_ts ON ctrl_audit(loop_id, ts DESC);
"""

# 老库升级：建表语句里新增的列在这里登记，启动时按 PRAGMA table_info 补齐。
# 只允许"加列 + 给默认值"，不在这里做数据搬家（迁移失败会让服务起不来）。
COLUMN_UPGRADES: dict[str, dict[str, str]] = {
    "ctrl_cycle": {
        "verification_summary_json": "TEXT NOT NULL DEFAULT '{}'",
    },
}


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Store:
    def __init__(self, path: str = "fcc.db") -> None:
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._upgrade_columns()
            self._conn.commit()

    def _upgrade_columns(self) -> None:
        """给老库补列：新库 executescript 已建好，老库缺哪列补哪列。"""
        for table, columns in COLUMN_UPGRADES.items():
            existing = {
                row["name"] for row in self._exec(f"PRAGMA table_info({table})").fetchall()
            }
            for column, decl in columns.items():
                if column not in existing:
                    self._exec(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    # Persistent state for stateful controllers, version isolated.
    def get_controller_state(self, loop_id: str, kind: str, version: int) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT state_json FROM ctrl_controller_state WHERE loop_id=? AND controller_kind=? AND strategy_version=?",
                (loop_id, kind, version)).fetchone()
        return util.loads(row["state_json"]) if row else {}

    def set_controller_state(self, loop_id: str, kind: str, version: int, state: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO ctrl_controller_state(loop_id,controller_kind,strategy_version,state_json,updated_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(loop_id) DO UPDATE SET "
                "controller_kind=excluded.controller_kind, strategy_version=excluded.strategy_version, "
                "state_json=excluded.state_json,updated_at=excluded.updated_at",
                (loop_id, kind, version, util.dumps(state), util.utcnow_iso()))

    # -- 基础设施 ---------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _exec(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self._conn.execute(sql, tuple(params))

    def _commit(self) -> None:
        self._conn.commit()

    # -- loop -------------------------------------------------------------

    def save_loop(self, loop_id: str, config: LoopConfig, enabled: bool) -> None:
        now = util.utcnow_iso()
        with self._lock:
            exists = self._exec("SELECT id, created_at FROM ctrl_loop WHERE id=?", (loop_id,))
            row = exists.fetchone()
            payload = util.dumps(config.model_dump())
            if row is None:
                self._exec(
                    "INSERT INTO ctrl_loop(id,name,enabled,config_json,created_at,updated_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (loop_id, config.name, int(enabled), payload, now, now),
                )
            else:
                self._exec(
                    "UPDATE ctrl_loop SET name=?, enabled=?, config_json=?, updated_at=? WHERE id=?",
                    (config.name, int(enabled), payload, now, loop_id),
                )
            self._sync_children(loop_id, config)
            self._exec(
                "INSERT INTO ctrl_strategy_version(loop_id,version,config_json,active,created_at)"
                " VALUES(?,?,?,1,?) ON CONFLICT(loop_id,version) DO UPDATE SET"
                " config_json=excluded.config_json, active=1",
                (loop_id, config.controller.strategy_version, payload, now),
            )
            self._commit()

    def _sync_children(self, loop_id: str, config: LoopConfig) -> None:
        self._exec("DELETE FROM ctrl_goal WHERE loop_id=?", (loop_id,))
        for g in config.goals:
            self._exec(
                "INSERT INTO ctrl_goal(loop_id,goal_id,target_variable,setpoint_json,direction,"
                "tolerance,priority,constraints_json,success_criteria_json)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    loop_id,
                    g.goal_id,
                    g.target_variable,
                    util.dumps(g.setpoint.model_dump()),
                    g.direction,
                    g.tolerance,
                    g.priority,
                    util.dumps(g.constraints),
                    util.dumps({"tolerance": g.tolerance, "direction": g.direction}),
                ),
            )
        self._exec("DELETE FROM ctrl_observation_spec WHERE loop_id=?", (loop_id,))
        for o in config.observations:
            self._exec(
                "INSERT INTO ctrl_observation_spec(loop_id,metric_id,source,value_type,unit,"
                "sampling_interval,valid_range_json,freshness_limit,missing_policy,"
                "outlier_policy,importance) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    loop_id,
                    o.metric_id,
                    o.source,
                    o.value_type,
                    o.unit,
                    o.sampling_interval,
                    util.dumps(list(o.valid_range) if o.valid_range else None),
                    o.freshness_limit,
                    o.missing_policy,
                    o.outlier_policy,
                    o.importance,
                ),
            )

    def get_loop(self, loop_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._exec("SELECT * FROM ctrl_loop WHERE id=?", (loop_id,)).fetchone()
        return _row_to_dict(row)

    def get_loop_config(self, loop_id: str) -> tuple[LoopConfig, bool, str] | None:
        row = self.get_loop(loop_id)
        if row is None:
            return None
        config = LoopConfig.model_validate(util.loads(row["config_json"], {}))
        return config, bool(row["enabled"]), row["updated_at"]

    def list_loops(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._exec(
                "SELECT id,name,enabled,created_at,updated_at FROM ctrl_loop ORDER BY created_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def set_enabled(self, loop_id: str, enabled: bool) -> None:
        with self._lock:
            self._exec(
                "UPDATE ctrl_loop SET enabled=?, updated_at=? WHERE id=?",
                (int(enabled), util.utcnow_iso(), loop_id),
            )
            self._commit()

    def delete_loop(self, loop_id: str) -> None:
        with self._lock:
            for table in (
                "ctrl_goal",
                "ctrl_observation_spec",
                "ctrl_observation",
                "ctrl_cycle",
                "ctrl_action",
                "ctrl_verification",
                "ctrl_strategy_version",
                "ctrl_audit",
            ):
                self._exec(f"DELETE FROM {table} WHERE loop_id=?", (loop_id,))
            self._exec("DELETE FROM ctrl_loop WHERE id=?", (loop_id,))
            self._commit()

    # -- 观测 -------------------------------------------------------------

    def insert_observation(self, obs: ObservationRecord) -> int:
        with self._lock:
            cur = self._exec(
                "INSERT INTO ctrl_observation(loop_id,metric_id,value,source_type,confidence,"
                "evidence_json,is_ground_truth,ts,quality_flags_json,model,prompt_version)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    obs.loop_id,
                    obs.metric_id,
                    obs.value,
                    obs.source_type,
                    obs.confidence,
                    util.dumps(obs.evidence),
                    int(obs.is_ground_truth),
                    obs.ts,
                    util.dumps(obs.quality_flags),
                    obs.model,
                    obs.prompt_version,
                ),
            )
            self._commit()
            return int(cur.lastrowid)

    def latest_observation(self, loop_id: str, metric_id: str) -> ObservationRecord | None:
        with self._lock:
            row = self._exec(
                "SELECT * FROM ctrl_observation WHERE loop_id=? AND metric_id=?"
                " ORDER BY ts DESC, id DESC LIMIT 1",
                (loop_id, metric_id),
            ).fetchone()
        if row is None:
            return None
        return ObservationRecord(
            id=row["id"],
            loop_id=row["loop_id"],
            metric_id=row["metric_id"],
            value=row["value"],
            source_type=row["source_type"],
            confidence=row["confidence"],
            evidence=util.loads(row["evidence_json"], []),
            is_ground_truth=bool(row["is_ground_truth"]),
            ts=row["ts"],
            quality_flags=util.loads(row["quality_flags_json"], []),
            model=row["model"],
            prompt_version=row["prompt_version"],
        )

    def observation_history(
        self, loop_id: str, metric_id: str, limit: int = 50
    ) -> list[ObservationRecord]:
        with self._lock:
            rows = self._exec(
                "SELECT * FROM ctrl_observation WHERE loop_id=? AND metric_id=?"
                " ORDER BY ts DESC, id DESC LIMIT ?",
                (loop_id, metric_id, limit),
            ).fetchall()
        out: list[ObservationRecord] = []
        for row in rows:
            out.append(
                ObservationRecord(
                    id=row["id"],
                    loop_id=row["loop_id"],
                    metric_id=row["metric_id"],
                    value=row["value"],
                    source_type=row["source_type"],
                    confidence=row["confidence"],
                    evidence=util.loads(row["evidence_json"], []),
                    is_ground_truth=bool(row["is_ground_truth"]),
                    ts=row["ts"],
                    quality_flags=util.loads(row["quality_flags_json"], []),
                )
            )
        out.reverse()
        return out

    def observations_since(
        self, loop_id: str, metric_id: str, since_iso: str
    ) -> list[ObservationRecord]:
        with self._lock:
            rows = self._exec(
                "SELECT * FROM ctrl_observation WHERE loop_id=? AND metric_id=? AND ts>=?"
                " ORDER BY ts DESC, id DESC LIMIT 50",
                (loop_id, metric_id, since_iso),
            ).fetchall()
        return [
            ObservationRecord(
                id=r["id"],
                loop_id=r["loop_id"],
                metric_id=r["metric_id"],
                value=r["value"],
                source_type=r["source_type"],
                confidence=r["confidence"],
                evidence=util.loads(r["evidence_json"], []),
                is_ground_truth=bool(r["is_ground_truth"]),
                ts=r["ts"],
                quality_flags=util.loads(r["quality_flags_json"], []),
                model=r["model"],
                prompt_version=r["prompt_version"],
            )
            for r in rows
        ]

    # -- cycle ------------------------------------------------------------

    def find_cycle_by_event(self, loop_id: str, event_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._exec(
                "SELECT * FROM ctrl_cycle WHERE loop_id=? AND event_key=?",
                (loop_id, event_key),
            ).fetchone()
        return _decode_cycle(row)

    def insert_cycle(self, loop_id: str, payload: dict[str, Any]) -> int:
        with self._lock:
            cur = self._exec(
                "INSERT INTO ctrl_cycle(loop_id,started_at,finished_at,target_json,"
                "observations_json,estimated_state_json,errors_json,uncertainty,anomalies_json,"
                "proposed_actions_json,executed_actions_json,expected_outcome_json,"
                "verification_status,next_evaluation_at,human_review_required,"
                "should_intervene,audit_ref,verifications_json,verification_summary_json,"
                "event_key)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    loop_id,
                    payload["started_at"],
                    payload.get("finished_at"),
                    util.dumps(payload.get("target", {})),
                    util.dumps(payload.get("observations", {})),
                    util.dumps(payload.get("estimated_state", {})),
                    util.dumps(payload.get("errors", [])),
                    float(payload.get("uncertainty", 0.0)),
                    util.dumps(payload.get("anomalies", [])),
                    util.dumps(payload.get("proposed_actions", [])),
                    util.dumps(payload.get("executed_actions", [])),
                    util.dumps(payload.get("expected_outcome", {})),
                    payload.get("verification_status", "pending_verification"),
                    payload.get("next_evaluation_at"),
                    int(bool(payload.get("human_review_required"))),
                    int(bool(payload.get("should_intervene"))),
                    util.dumps(payload.get("audit_ref", [])),
                    util.dumps(payload.get("verifications", [])),
                    util.dumps(payload.get("verification_summary", {})),
                    payload.get("event_key"),
                ),
            )
            self._commit()
            return int(cur.lastrowid)

    def update_cycle(self, cycle_id: int, payload: dict[str, Any]) -> None:
        mapping = {
            "finished_at": "finished_at",
            "verification_status": "verification_status",
            "human_review_required": "human_review_required",
            "next_evaluation_at": "next_evaluation_at",
        }
        sets, params = [], []
        for key, col in mapping.items():
            if key in payload:
                value = payload[key]
                if key == "human_review_required":
                    value = int(bool(value))
                sets.append(f"{col}=?")
                params.append(value)
        json_fields = {
            "observations": "observations_json",
            "errors": "errors_json",
            "proposed_actions": "proposed_actions_json",
            "executed_actions": "executed_actions_json",
            "expected_outcome": "expected_outcome_json",
            "anomalies": "anomalies_json",
            "audit_ref": "audit_ref",
            "estimated_state": "estimated_state_json",
            "verifications": "verifications_json",
            "verification_summary": "verification_summary_json",
            "target": "target_json",
        }
        for key, col in json_fields.items():
            if key in payload:
                sets.append(f"{col}=?")
                params.append(util.dumps(payload[key]))
        if "uncertainty" in payload:
            sets.append("uncertainty=?")
            params.append(float(payload["uncertainty"]))
        if "should_intervene" in payload:
            sets.append("should_intervene=?")
            params.append(int(bool(payload["should_intervene"])))
        if not sets:
            return
        params.append(cycle_id)
        with self._lock:
            self._exec(f"UPDATE ctrl_cycle SET {', '.join(sets)} WHERE id=?", params)
            self._commit()

    def get_cycle(self, cycle_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._exec("SELECT * FROM ctrl_cycle WHERE id=?", (cycle_id,)).fetchone()
        return _decode_cycle(row)

    def list_cycles(self, loop_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._exec(
                "SELECT * FROM ctrl_cycle WHERE loop_id=? ORDER BY id DESC LIMIT ?",
                (loop_id, limit),
            ).fetchall()
        return [c for c in (_decode_cycle(r) for r in rows) if c]

    def last_cycle(self, loop_id: str) -> dict[str, Any] | None:
        cycles = self.list_cycles(loop_id, 1)
        return cycles[0] if cycles else None

    def cycle_count(self, loop_id: str) -> int:
        with self._lock:
            row = self._exec(
                "SELECT COUNT(*) AS n FROM ctrl_cycle WHERE loop_id=?", (loop_id,)
            ).fetchone()
        return int(row["n"])

    # -- action -----------------------------------------------------------

    def insert_action(
        self,
        *,
        loop_id: str,
        cycle_id: int,
        proposal: ActionProposal,
        status: str,
        required_approval: bool,
        idempotency_key: str,
        decision_reason: str = "",
        strategy_version: int = 1,
    ) -> int | None:
        """幂等：同一 idempotency_key 已存在则返回 None。"""
        now = util.utcnow_iso()
        with self._lock:
            existing = self._exec(
                "SELECT id FROM ctrl_action WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                return None
            cur = self._exec(
                "INSERT INTO ctrl_action(loop_id,cycle_id,action_type,target,params_json,"
                "expected_effect,risk_level,required_approval,status,idempotency_key,"
                "rollback_method,source_type,origin_detail,goal_id,decision_reason,"
                "strategy_version,verification_status,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    loop_id,
                    cycle_id,
                    proposal.action_type,
                    proposal.target,
                    util.dumps(proposal.params),
                    proposal.expected_effect,
                    proposal.risk_level,
                    int(required_approval),
                    status,
                    idempotency_key,
                    proposal.rollback_method,
                    proposal.source_type,
                    proposal.origin_detail,
                    proposal.goal_id,
                    decision_reason,
                    strategy_version,
                    "pending_verification" if status == "pending_verification" else None,
                    now,
                ),
            )
            self._commit()
            return int(cur.lastrowid)

    def get_action(self, action_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._exec("SELECT * FROM ctrl_action WHERE id=?", (action_id,)).fetchone()
        return _decode_action(row)

    def update_action(self, action_id: int, **fields: Any) -> None:
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._exec(
                f"UPDATE ctrl_action SET {sets} WHERE id=?", (*fields.values(), action_id)
            )
            self._commit()

    def list_actions(
        self, loop_id: str, status: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM ctrl_action WHERE loop_id=?"
        params: list[Any] = [loop_id]
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._exec(sql, params).fetchall()
        return [a for a in (_decode_action(r) for r in rows) if a]

    def actions_of_cycle(self, cycle_id: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._exec(
                "SELECT * FROM ctrl_action WHERE cycle_id=? ORDER BY id", (cycle_id,)
            ).fetchall()
        return [a for a in (_decode_action(r) for r in rows) if a]

    def actions_pending_verification(self, loop_id: str) -> list[dict[str, Any]]:
        return self.list_actions(loop_id, status="pending_verification", limit=200)

    def actions_awaiting_approval(self, loop_id: str) -> list[dict[str, Any]]:
        return self.list_actions(loop_id, status="awaiting_approval", limit=200)

    def verification_breakdown(self, loop_id: str) -> list[dict[str, int | str]]:
        """按动作的**当前结案状态**聚合本 loop 全部动作（不限条数）。

        每个动作只算一票：已结案（verified）按 verdict 归类，失败记 failed，
        其余（pending_verification / awaiting_approval / proposed …）一律记 pending
        —— 哪怕中间试判过 inconclusive，只要动作还挂着，它在面板上就是"没有结论"。
        返回 [{"verdict": str, "n": n}]，由 verification.summarize_verdicts 汇总。
        """
        with self._lock:
            rows = self._exec(
                "SELECT CASE"
                "  WHEN status='verified' THEN COALESCE(verification_status,'pending')"
                "  WHEN status='failed' THEN 'failed'"
                "  WHEN status IN ('rejected','rolled_back') THEN status"
                "  ELSE 'pending' END AS verdict, COUNT(*) AS n"
                " FROM ctrl_action WHERE loop_id=? GROUP BY verdict",
                (loop_id,),
            ).fetchall()
        return [{"verdict": str(r["verdict"]), "n": int(r["n"])} for r in rows]

    # -- verification -----------------------------------------------------

    def insert_verification(self, loop_id: str, action_id: int, cycle_id: int,
                            record: VerificationRecord) -> int:
        with self._lock:
            cur = self._exec(
                "INSERT INTO ctrl_verification(loop_id,action_id,cycle_id,baseline_json,"
                "after_json,verdict,delta_json,noise_estimate,notes_json,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    loop_id,
                    action_id,
                    cycle_id,
                    util.dumps(record.baseline),
                    util.dumps(record.after),
                    record.verdict,
                    util.dumps(record.delta),
                    record.noise_estimate,
                    util.dumps(record.notes),
                    util.utcnow_iso(),
                ),
            )
            self._commit()
            return int(cur.lastrowid)

    def list_verifications(self, loop_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._exec(
                "SELECT * FROM ctrl_verification WHERE loop_id=? ORDER BY id DESC LIMIT ?",
                (loop_id, limit),
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            for k in ("baseline_json", "after_json", "delta_json", "notes_json"):
                d[k.replace("_json", "")] = util.loads(d.pop(k), None)
            out.append(d)
        return out

    # -- audit ------------------------------------------------------------

    def add_audit(self, loop_id: str, kind: str, actor: str, payload: dict[str, Any]) -> int:
        with self._lock:
            cur = self._exec(
                "INSERT INTO ctrl_audit(loop_id,ts,kind,actor,payload_json) VALUES(?,?,?,?,?)",
                (loop_id, util.utcnow_iso(), kind, actor, util.dumps(payload)),
            )
            self._commit()
            return int(cur.lastrowid)

    def list_audit(self, loop_id: str, limit: int = 200, kind: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM ctrl_audit WHERE loop_id=?"
        params: list[Any] = [loop_id]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._exec(sql, params).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["payload"] = util.loads(d.pop("payload_json"), {})
            out.append(d)
        out.reverse()
        return out

    def audit_chain(self, cycle_id: int) -> list[dict[str, Any]]:
        """按 A7 的要求，从一个 cycle 拼出完整链。

        收录三类审计条目：cycle.audit_ref 里显式引用的、payload.cycle_id 指向本轮的、
        以及本轮动作产生的（payload.action_id 命中）。
        """
        cycle = self.get_cycle(cycle_id)
        if not cycle:
            return []
        loop_id = cycle["loop_id"]
        refs = {int(x) for x in cycle.get("audit_ref", []) if str(x).isdigit()}
        action_ids = {a["id"] for a in self.actions_of_cycle(cycle_id)}
        rows = self.list_audit(loop_id, limit=5000)
        picked = [
            r for r in rows
            if r["id"] in refs
            or r["payload"].get("cycle_id") == cycle_id
            or r["payload"].get("action_id") in action_ids
        ]
        return picked

    # -- strategy ---------------------------------------------------------

    def list_strategy_versions(self, loop_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._exec(
                "SELECT * FROM ctrl_strategy_version WHERE loop_id=? ORDER BY version",
                (loop_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def activate_strategy_version(self, loop_id: str, version: int) -> bool:
        with self._lock:
            row = self._exec(
                "SELECT id FROM ctrl_strategy_version WHERE loop_id=? AND version=?",
                (loop_id, version),
            ).fetchone()
            if row is None:
                return False
            self._exec("UPDATE ctrl_strategy_version SET active=0 WHERE loop_id=?", (loop_id,))
            self._exec(
                "UPDATE ctrl_strategy_version SET active=1 WHERE loop_id=? AND version=?",
                (loop_id, version),
            )
            self._commit()
            return True


def _decode_cycle(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    d["target"] = util.loads(d.pop("target_json"), {})
    d["observations"] = util.loads(d.pop("observations_json"), {})
    d["estimated_state"] = util.loads(d.pop("estimated_state_json"), {})
    d["errors"] = util.loads(d.pop("errors_json"), [])
    d["anomalies"] = util.loads(d.pop("anomalies_json"), [])
    d["proposed_actions"] = util.loads(d.pop("proposed_actions_json"), [])
    d["executed_actions"] = util.loads(d.pop("executed_actions_json"), [])
    d["expected_outcome"] = util.loads(d.pop("expected_outcome_json"), {})
    d["audit_ref"] = util.loads(d.pop("audit_ref"), [])
    d["verifications"] = util.loads(d.pop("verifications_json", None), [])
    d["verification_summary"] = util.loads(d.pop("verification_summary_json", None), {})
    d["human_review_required"] = bool(d.get("human_review_required"))
    d["should_intervene"] = bool(d.get("should_intervene"))
    d["cycle_id"] = d["id"]  # 对外统一叫 cycle_id（DB 主键仍叫 id）
    return d


def _decode_action(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    d["params"] = util.loads(d.pop("params_json"), {})
    d["result"] = util.loads(d.pop("result_json"), None)
    d["required_approval"] = bool(d.get("required_approval"))
    return d
