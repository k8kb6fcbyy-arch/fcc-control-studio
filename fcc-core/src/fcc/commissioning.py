"""通用场景 commissioning / onboarding 层。

不以自然语言猜测数学模型，不直接实施业务动作。
只把用户已确认的可测指标转换为受保护的 FCC monitor 控制回路，
按周期创建本地可持久化报告收件箱，提出候选干预但不假设动作已执行。
"""
from __future__ import annotations

import datetime as dt
import math
import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import util
from .engine import Engine, LoopNotFound
from .models import LoopConfig
from .store import Store


class Intake(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    # 自然语言目标 & 数值代理需明确区分；无需假设用户理解控制理论。
    title: str | None = None
    desired_outcome: str | None = None
    metric_name: str | None = None
    unit: str = ""
    target_value: float | None = None
    target_mode: Literal["exact", "at_least", "at_most"] = "exact"
    tolerance: float = Field(default=0.0, ge=0)
    # 登记的 baseline 只是用户声明，不能代替日后独立观测
    claimed_baseline: float | None = None
    data_source: Literal["human_input", "measured", "ai_inference"] | None = None
    data_delivery: Literal["manual", "push", "registered_adapter"] = "manual"
    adapter_name: str | None = None
    sampling_seconds: float | None = Field(default=None, gt=0)
    evaluation_seconds: float | None = Field(default=None, gt=0)
    report_seconds: float | None = Field(default=None, gt=0)
    verification_delay_seconds: float = Field(default=0, ge=0)
    max_data_age_seconds: float | None = Field(default=None, gt=0)
    sustained_samples: int = Field(default=3, ge=1, le=100)
    horizon_days: float | None = Field(default=None, gt=0)
    controllable_levers: list[str] = Field(default_factory=list)
    action_authority: Literal["observe", "suggest", "approval_required"] | None = None
    constraints: list[str] = Field(default_factory=list)
    side_effect_metrics: list[str] = Field(default_factory=list)
    failure_policy: Literal["pause_and_alert", "continue_monitoring"] = "pause_and_alert"
    timezone: str = "UTC"
    report_channel: Literal["local_inbox"] = "local_inbox"
    success_definition: str | None = None
    data_owner: str | None = None

    @model_validator(mode="after")
    def constraints_check(self):
        # 采样/反馈周期按不同的时钟处理，避免评估速度超过数据产生速度。
        if self.sampling_seconds and self.evaluation_seconds and self.evaluation_seconds < self.sampling_seconds:
            raise ValueError("评估频率不能快于声明的数据采样频率；请修正，否则将反复评估旧数据")
        if self.data_delivery == "registered_adapter" and self.adapter_name == "manual":
            raise ValueError("registered_adapter 必须使用真实的适配器名称")
        if self.report_seconds and self.report_seconds < 1:
            raise ValueError("报告间隔至少一秒")
        return self


# 按当前缺口提问；必需信息少，复杂功能后置。
FIELDS: tuple[dict[str, Any], ...] = (
    {"key":"title","step":1,"required":True,"label":"如何称呼这个持续改善任务？","example":"我的长期优化任务"},
    {"key":"desired_outcome","step":1,"required":True,"label":"你最终想改善什么？请用普通话描述。","example":"让某项结果稳定达到既定标准"},
    {"key":"metric_name","step":2,"required":True,"label":"用哪个可验证的指标判断目标是否达成？","example":"成功率 / 延迟 / 错误数 / 温度"},
    {"key":"target_value","step":2,"required":True,"label":"这个指标要达到多少？","example":95},
    {"key":"target_mode","step":2,"required":False,"label":"达到目标的标准是什么？","options":["exact","at_least","at_most"]},
    {"key":"tolerance","step":2,"required":False,"label":"允许与目标偏差多少？默认 0。","example":3},
    {"key":"unit","step":2,"required":False,"label":"指标单位是什么？","example":"% / 次 / 秒 / 摄氏度"},
    {"key":"data_source","step":3,"required":True,"label":"数据来自哪里？实测 / 人工输入 / AI 推断？","options":["measured","human_input","ai_inference"]},
    {"key":"data_delivery","step":3,"required":False,"label":"数据如何持续进入 FCC？手动填写、API 推送或注册的采集器？","options":["manual","push","registered_adapter"]},
    {"key":"adapter_name","step":3,"required":False,"label":"如果已注册采集器，其适配器名称是什么？","example":"my_registered_observation_adapter"},
    {"key":"sampling_seconds","step":3,"required":True,"label":"多久产生一次新的观测数据（秒）？","example":3600},
    {"key":"evaluation_seconds","step":4,"required":True,"label":"多久做一次偏差检查（秒）？不能快于采样。","example":3600},
    {"key":"report_seconds","step":4,"required":True,"label":"多久向用户生成一次阶段报告（秒）？","example":86400},
    {"key":"controllable_levers","step":5,"required":False,"label":"实际允许改变什么？列出可控手段。若不清楚，可以先只观察。","example":["可调整的流程参数"]},
    {"key":"action_authority","step":5,"required":True,"label":"FCC 可以做到哪一步？只观察、给建议、执行前必须人工批准。","options":["observe","suggest","approval_required"]},
    {"key":"constraints","step":5,"required":False,"label":"哪些事情绝对不能做？列出预算、安全、不可逆操作等约束。","example":["未经批准不得修改配置"]},
    {"key":"side_effect_metrics","step":5,"required":False,"label":"还要监控哪些潜在副作用？","example":["成本","可靠性"]},
    {"key":"success_definition","step":5,"required":False,"label":"怎样才算最终成功？会默认要求连续多次达标，并保留数据证据。"},
)
REQUIRED_KEYS = tuple(f["key"] for f in FIELDS if f["required"])


def missing(data: Intake) -> list[str]:
    return [k for k in REQUIRED_KEYS if getattr(data, k) is None or (isinstance(getattr(data, k), str) and not getattr(data, k).strip())]


def next_questions(data: Intake) -> list[dict[str, Any]]:
    """只索要当前最早未完成阶段的关键问题，不把十几项表单一次砸给用户。"""
    gaps = set(missing(data))
    if not gaps:
        return []
    first_step = min(f["step"] for f in FIELDS if f["key"] in gaps)
    return [f for f in FIELDS if f["required"] and f["step"] == first_step and f["key"] in gaps]


def _metric_id(name: str) -> str:
    # 使用固定内部标识，允许非 ASCII 标签，防止用户自然语言破坏字段名。
    return "primary_metric"


def compile_plan(data: Intake) -> dict[str, Any]:
    """明确保留未经证实的假设。返回有效 LoopConfig + 启动条件，不运行 AI 推断。"""
    if missing(data):
        raise ValueError("缺少必填项: " + ", ".join(missing(data)))
    metric = _metric_id(data.metric_name or "")
    sampling = float(data.sampling_seconds or 3600)
    evaluation = float(data.evaluation_seconds or sampling)
    fresh = float(data.max_data_age_seconds or max(sampling * 2, evaluation * 1.5))
    if data.data_delivery == "registered_adapter" and not data.adapter_name:
        raise ValueError("选择 registered_adapter 需要 adapter_name，且必须已在 FCC 注册")
    if data.action_authority == "approval_required" and not data.controllable_levers:
        raise ValueError("要求执行前批准时，仍须明确至少一个可控手段；否则请选择 observe 或 suggest")
    # 本产品的 onboarding 不自动把文字变成物理执行动作。设定值只用于 compare。
    config = LoopConfig.model_validate({
        "name": data.title,
        "goals": [{"goal_id":"primary_goal","target_variable":metric,
                   "setpoint":{"kind":"value", "value":data.target_value},
                   "tolerance": data.tolerance, "direction":{"exact":"approach","at_least":"increase","at_most":"decrease"}[data.target_mode]}],
        "observations":[{"metric_id":metric,
                         "source": data.adapter_name if data.data_delivery == "registered_adapter" else "manual",
                         "unit":data.unit, "sampling_interval": sampling,
                         "freshness_limit": fresh,
                         "missing_policy":"stale", "importance":"high"}],
        "controller":{"kind":"monitor"},
        "scheduler":{"mode":"fixed", "interval_seconds":evaluation},
        "actuator":{"permission_mode":"suggest_only", "adapter":"manual"},
        "verification":{"delay_seconds":data.verification_delay_seconds},
        "enabled":False,
        "metadata":{"product":"fcc-commissioning-1.5",
                    "desired_outcome":data.desired_outcome,
                    "proxy_metric":data.metric_name,
                    "claimed_baseline":data.claimed_baseline,
                    "claimed_baseline_verified":False,
                    "action_authority":data.action_authority,
                    "controllable_levers":data.controllable_levers,
                    "constraints":data.constraints,
                    "side_effect_metrics":data.side_effect_metrics,
                    "sampling_seconds":sampling,
                    "evaluation_seconds":evaluation,
                    "report_seconds":data.report_seconds,
                    "data_delivery":data.data_delivery,
                    "report_delivery":"local_inbox_only"}
    }).model_dump()
    warnings = ["默认只监控和生成建议；自然语言控制手段不会自动转换为实际执行器"]
    if data.data_source == "ai_inference":
        warnings.append("数据来自 AI 推断，必须提交置信度及证据；不能用 AI 自评证明目标已达成")
    if data.data_delivery == "manual":
        warnings.append("手动数据模式不会自行采集新数据；长期运行需要用户输入或接入外部数据源")
    if data.report_channel == "local_inbox":
        warnings.append("阶段报告保存在本地收件箱，尚未配置真实的外部主动通知")
    if data.action_authority == "approval_required":
        warnings.append("需要额外注册具体执行器、动作映射和审批通道，本向导不会自行安装")
    return {"loop_config":config,"warnings":warnings,
            "readiness":"ready_for_monitoring", "requires_first_verified_sample":True,
            "adaptation":"monitor_trend_and_propose_candidate; never silently tune controller"}


class CommissioningService:
    """共用 Store SQLite 连接和锁；保持单机模式，不暗示分布式定时可靠性。"""

    def __init__(self, store: Store, engine: Engine):
        self.store = store
        self.engine = engine
        with self.store._lock, self.store._conn:
            self.store._conn.executescript("""
            CREATE TABLE IF NOT EXISTS commissioning_plan(
                id TEXT PRIMARY KEY, loop_id TEXT UNIQUE, draft_json TEXT NOT NULL,
                status TEXT NOT NULL, started_at TEXT, last_report_at TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS commissioning_outbox(
                id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id TEXT NOT NULL,
                created_at TEXT NOT NULL, kind TEXT NOT NULL,
                payload_json TEXT NOT NULL, read_at TEXT,
                UNIQUE(plan_id, kind, created_at)
            );
            CREATE INDEX IF NOT EXISTS idx_comm_outbox ON commissioning_outbox(plan_id, id DESC);
            CREATE TABLE IF NOT EXISTS commissioning_intervention(
                id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id TEXT NOT NULL,
                description TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL,
                proposed_at TEXT NOT NULL, applied_at TEXT, baseline_value REAL,
                baseline_ts TEXT, result_json TEXT
            );
            """)

    def create(self, patch: dict[str, Any] | None = None) -> dict[str, Any]:
        data = Intake.model_validate(patch or {})
        plan_id = "plan_" + uuid.uuid4().hex[:14]
        now = util.utcnow_iso()
        with self.store._lock, self.store._conn:
            self.store._conn.execute("INSERT INTO commissioning_plan(id,draft_json,status,created_at,updated_at) VALUES(?,?,?,?,?)",
                                     (plan_id, util.dumps(data.model_dump()), "draft", now, now))
        return self.get(plan_id)

    def _row(self, plan_id: str) -> dict[str, Any]:
        with self.store._lock:
            row = self.store._conn.execute("SELECT * FROM commissioning_plan WHERE id=?",(plan_id,)).fetchone()
        if not row:
            raise KeyError(f"计划不存在: {plan_id}")
        return dict(row)

    def get(self, plan_id: str) -> dict[str, Any]:
        row = self._row(plan_id)
        intake = Intake.model_validate(util.loads(row["draft_json"]))
        gaps = missing(intake)
        return {"plan_id":plan_id, "status":row["status"], "loop_id":row["loop_id"],
                "intake":intake.model_dump(),"required_missing":gaps,
                "next_questions":next_questions(intake),
                "can_compile":not gaps, "started_at":row["started_at"],
                "last_report_at":row["last_report_at"]}

    def update(self, plan_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        with self.store._lock, self.store._conn:
            row = self._row(plan_id)
            if row["loop_id"]:
                raise ValueError("已启动计划不可直接覆盖配置：请暂停后通过 Core 版本化配置更新，或新建计划")
            merged = util.loads(row["draft_json"])
            merged.update(patch)
            data = Intake.model_validate(merged)
            self.store._conn.execute("UPDATE commissioning_plan SET draft_json=?,updated_at=? WHERE id=?",
                                     (util.dumps(data.model_dump()), util.utcnow_iso(), plan_id))
        return self.get(plan_id)

    def compile(self, plan_id: str) -> dict[str, Any]:
        return compile_plan(Intake.model_validate(self.get(plan_id)["intake"]))

    def activate(self, plan_id: str, *, run_scheduler: bool = True) -> dict[str, Any]:
        row = self._row(plan_id)
        if row["loop_id"]:
            return {"plan_id":plan_id,"loop_id":row["loop_id"],"already_activated":True}
        plan = self.compile(plan_id)
        intake = Intake.model_validate(self.get(plan_id)["intake"])
        if intake.data_delivery == "registered_adapter" and intake.adapter_name not in self.engine.obs_adapters:
            raise ValueError(f"未注册观测适配器: {intake.adapter_name}")
        # 用户明确调用 activate，允许定时监视；不具备执行权限。
        config = LoopConfig.model_validate(plan["loop_config"])
        loop_id = self.engine.create_loop(config)
        now = util.utcnow_iso()
        with self.store._lock, self.store._conn:
            self.store._conn.execute("UPDATE commissioning_plan SET loop_id=?,status='running',started_at=?,last_report_at=?,updated_at=? WHERE id=?",
                                     (loop_id,now,now,now,plan_id))
        if run_scheduler:
            self.engine.set_enabled(loop_id,True,"commissioning")
        return {"plan_id":plan_id,"loop_id":loop_id,"status":"running",
                "run_scheduler":run_scheduler,"warnings":plan["warnings"]}

    def pause(self, plan_id: str) -> dict[str, Any]:
        row = self._row(plan_id)
        if not row["loop_id"]:
            raise ValueError("计划尚未启动")
        self.engine.set_enabled(row["loop_id"],False,"commissioning")
        with self.store._lock, self.store._conn:
            self.store._conn.execute("UPDATE commissioning_plan SET status='paused',updated_at=? WHERE id=?",(util.utcnow_iso(),plan_id))
        return self.get(plan_id)

    def resume(self, plan_id: str) -> dict[str, Any]:
        row = self._row(plan_id)
        if not row["loop_id"]:
            raise ValueError("计划尚未启动")
        self.engine.set_enabled(row["loop_id"],True,"commissioning")
        with self.store._lock, self.store._conn:
            self.store._conn.execute("UPDATE commissioning_plan SET status='running',updated_at=? WHERE id=?",(util.utcnow_iso(),plan_id))
        return self.get(plan_id)

    def list(self) -> list[dict[str, Any]]:
        with self.store._lock:
            rows=self.store._conn.execute("SELECT id FROM commissioning_plan ORDER BY created_at DESC").fetchall()
        return [self.get(row["id"]) for row in rows]

    def observation(self, plan_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        row = self._row(plan_id)
        if not row["loop_id"]:
            raise ValueError("先完成建模和激活")
        intake = Intake.model_validate(util.loads(row["draft_json"]))
        if payload.get("source_type") != intake.data_source:
            raise ValueError(f"数据来源与声明不符，预期 {intake.data_source}；不得冒充实测")
        payload = {**payload, "metric_id":"primary_metric"}
        return self.engine.submit_observation(row["loop_id"], payload)

    def snapshot(self, plan_id: str) -> dict[str, Any]:
        row = self._row(plan_id)
        if not row["loop_id"]:
            return {"plan_id":plan_id,"status":"draft","needs":self.get(plan_id)["required_missing"]}
        data = Intake.model_validate(util.loads(row["draft_json"]))
        loop_id = row["loop_id"]
        obs = self.store.latest_observation(loop_id, "primary_metric")
        history = self.store.observation_history(loop_id,"primary_metric",limit=max(50,data.sustained_samples + 3))
        fresh = False
        trustworthy = False
        if obs is not None:
            max_age = data.max_data_age_seconds or max(float(data.sampling_seconds or 1)*2,float(data.evaluation_seconds or 1)*1.5)
            fresh = 0 <= util.seconds_between(obs.ts,util.utcnow_iso()) <= max_age
            trustworthy = obs.usable_as_ground_truth
        eligible = bool(obs and fresh and trustworthy)
        value = obs.value if obs else None
        target = float(data.target_value) if data.target_value is not None else None
        raw_error = target - value if value is not None and target is not None else None
        tol = data.tolerance
        if raw_error is None:
            diff = None
            out = False
        elif data.target_mode == "at_least":
            diff = max(raw_error, 0.0)
            out = raw_error > tol
        elif data.target_mode == "at_most":
            diff = min(raw_error, 0.0)
            out = raw_error < -tol
        else:
            diff = raw_error
            out = abs(diff) > tol
        reliable = []
        for record in history:
            if record.usable_as_ground_truth:
                reliable.append(record)
            else:
                break
        # success requires independent recent trusted consecutive observations
        recent = reliable[:data.sustained_samples]
        # 防止同一时间戳或密集重复提交冒充跨周期稳定性。
        min_spacing = float(data.sampling_seconds or 1.0) * 0.5
        if any(util.seconds_between(recent[i+1].ts, recent[i].ts) < min_spacing
               for i in range(len(recent)-1)):
            recent = []
        def meets(v):
            if data.target_mode == "at_least":
                return v >= target - tol
            if data.target_mode == "at_most":
                return v <= target + tol
            return abs(v - target) <= tol
        sustained = eligible and len(recent) >= data.sustained_samples and all(meets(v.value) for v in recent)
        # Directional progress from unverified claimed baseline is informational only
        progress = None
        if value is not None and data.claimed_baseline is not None and target != data.claimed_baseline:
            progress = (value - data.claimed_baseline) / (target - data.claimed_baseline)
        recommendation = None
        if not eligible:
            recommendation = "当前缺少新鲜且非 AI 推断的可信观测；请补数据，不应修改策略"
        elif out and data.action_authority in ("suggest", "approval_required"):
            recommendation = "已发现偏差。请分析可控原因与副作用，提出可验证的候选干预，待授权后再执行"
        elif out:
            recommendation = "目标尚未达成；当前权限为仅观察，继续采样并评估趋势"
        elif not sustained:
            recommendation = f"当前达到容差，但需要连续 {data.sustained_samples} 次可信观测才能确认稳定达标"
        else:
            recommendation = "已获得连续达标的可信观测；建议保持监测，不要宣称未来一定持续达标"
        elapsed_fraction=None
        deadline_missed=False
        if data.horizon_days and row["started_at"]:
            elapsed_fraction = max(0., util.seconds_between(row["started_at"],util.utcnow_iso()) / (86400. * data.horizon_days))
            deadline_missed = bool(elapsed_fraction > 1. and not sustained)
        return {"plan_id":plan_id,"loop_id":loop_id,"status":row["status"],
                "elapsed_fraction_of_planned_horizon":elapsed_fraction,
                "deadline_missed":deadline_missed,
                "side_effect_metrics_not_independently_monitored":list(data.side_effect_metrics),
                "target":target,"target_mode":data.target_mode,"value":value,"unit":data.unit,"error":diff,
                "out_of_tolerance":out,"current_sample_trusted":eligible,
                "data_fresh":fresh,"source_type":obs.source_type if obs else None,
                "sample_ts":obs.ts if obs else None,"consecutive_verified_count":len(recent),
                "sustained_success":bool(sustained),"progress_from_claimed_baseline":progress,
                "progress_is_verified":False,
                "recommendation":recommendation,"action_authority":data.action_authority,
                "unverified_metric_warning": bool(data.data_source == "ai_inference"),
                "report_delivery":"local_inbox_only"}

    def propose_intervention(self, plan_id: str, description: str, reason: str = "") -> dict[str, Any]:
        """仅记录候选动作；FCC 不替用户决定如何执行或把记录当成真实执行。"""
        row = self._row(plan_id)
        if not row["loop_id"]:
            raise ValueError("请先激活控制计划")
        data = Intake.model_validate(util.loads(row["draft_json"]))
        if data.action_authority == "observe":
            raise ValueError("仅观察模式不允许创建干预候选；先完成权限调整与确认")
        if not description.strip() or len(description) > 2000:
            raise ValueError("干预描述长度必须为 1–2000 字")
        now = util.utcnow_iso()
        with self.store._lock,self.store._conn:
            cur=self.store._conn.execute(
                "INSERT INTO commissioning_intervention(plan_id,description,reason,status,proposed_at) VALUES(?,?,?,?,?)",
                (plan_id,description.strip(),reason.strip(),"proposed",now))
        return {"id":cur.lastrowid,"status":"proposed","requires_human_confirmation":True,
                "meaning":"建议已登记，尚未执行；不会触发外部系统控制"}

    def mark_applied(self, plan_id: str, intervention_id: int, *, actor: str, evidence: str) -> dict[str, Any]:
        """只登记人/外部适配器确认已执行；绝不冒充成功执行。"""
        row = self._row(plan_id)
        if not actor.strip() or not evidence.strip():
            raise ValueError("必须提供实际执行者和执行证据")
        if not row["loop_id"]:
            raise ValueError("计划未激活")
        data=Intake.model_validate(util.loads(row["draft_json"]))
        obs=self.store.latest_observation(row["loop_id"],"primary_metric")
        if obs is None or not obs.usable_as_ground_truth:
            raise ValueError("没有最近的非 AI 推断观测作为干预前基线，禁止标记可验证的执行")
        max_age=data.max_data_age_seconds or max(float(data.sampling_seconds or 1)*2,float(data.evaluation_seconds or 1)*1.5)
        if not 0 <= util.seconds_between(obs.ts, util.utcnow_iso()) <= max_age:
            raise ValueError("执行前基线已过期或来自未来；请先采集新数据")
        now = util.utcnow_iso()
        with self.store._lock,self.store._conn:
            cur=self.store._conn.execute("UPDATE commissioning_intervention SET status='applied_pending_verification',applied_at=?,baseline_value=?,baseline_ts=?,result_json=? WHERE id=? AND plan_id=? AND status='proposed'",
                 (now,obs.value,obs.ts,util.dumps({"actor":actor,"evidence":evidence}),intervention_id,plan_id))
        if cur.rowcount != 1:
            raise ValueError("候选动作不存在，或已被标记执行；禁止重复标记")
        return {"id":intervention_id,"status":"applied_pending_verification", "baseline":obs.value,
                "next_step":"等待验证延迟后重新采样，不能把声明执行当成有效果"}

    def verify_intervention(self, plan_id: str, intervention_id: int) -> dict[str, Any]:
        row=self._row(plan_id)
        if not row["loop_id"]:
            raise ValueError("计划未激活")
        with self.store._lock:
            found=self.store._conn.execute("SELECT * FROM commissioning_intervention WHERE id=? AND plan_id=?",(intervention_id,plan_id)).fetchone()
        if not found:
            raise KeyError("干预记录不存在")
        record=dict(found)
        if record["status"]!="applied_pending_verification":
            raise ValueError("只有执行后等待验证的动作可以进行验证")
        data=Intake.model_validate(util.loads(row["draft_json"]))
        now=util.utcnow_iso()
        if util.seconds_between(record["applied_at"],now)<data.verification_delay_seconds:
            return {"id":intervention_id,"status":"awaiting_settling","verified":False,
                    "reason":"尚未达到设定的验证等待时间"}
        obs=self.store.latest_observation(row["loop_id"],"primary_metric")
        earliest_valid = util.parse_iso(record["applied_at"]) + dt.timedelta(seconds=data.verification_delay_seconds)
        if obs is None or not obs.usable_as_ground_truth or util.parse_iso(obs.ts)<=earliest_valid:
            return {"id":intervention_id,"status":"awaiting_new_observation","verified":False,
                    "reason":"缺少执行后的新鲜非 AI 观测数据"}
        max_age=data.max_data_age_seconds or max(float(data.sampling_seconds or 1)*2,float(data.evaluation_seconds or 1)*1.5)
        if util.seconds_between(obs.ts,now)>max_age:
            return {"id":intervention_id,"status":"awaiting_new_observation","verified":False,
                    "reason":"执行后观测数据已过期"}
        target=float(data.target_value)
        baseline=float(record["baseline_value"])
        def gap(v):
            if data.target_mode=="at_least": return max(target-v,0.)
            if data.target_mode=="at_most": return max(v-target,0.)
            return abs(v-target)
        before=gap(baseline);after=gap(obs.value)
        if after < before-1e-9:verdict="improved"
        elif after > before+1e-9:verdict="worsened"
        else:verdict="unchanged"
        result={"id":intervention_id,"status":"verified_observational","verified":True,
                "verdict":verdict,"before":baseline,"after":obs.value,
                "previous_gap":before,"new_gap":after,
                "caution":"仅是干预前后观察关联，不能排除外部扰动，不能证明因果；副作用需额外评估"}
        with self.store._lock,self.store._conn:
            self.store._conn.execute("UPDATE commissioning_intervention SET status='verified_observational',result_json=? WHERE id=? AND plan_id=?",
                                    (util.dumps(result),intervention_id,plan_id))
        return result

    def interventions(self, plan_id: str) -> list[dict[str, Any]]:
        self._row(plan_id)
        with self.store._lock:
            rows=self.store._conn.execute("SELECT * FROM commissioning_intervention WHERE plan_id=? ORDER BY id DESC",(plan_id,)).fetchall()
        return [dict(row) | {"result":util.loads(row["result_json"])} for row in rows]

    def report(self, plan_id: str, kind: str = "checkpoint", *, now: str | None = None) -> dict[str, Any]:
        row = self._row(plan_id)
        if row["status"] != "running":
            raise ValueError("只能为运行中的计划生成周期报告")
        snapshot = self.snapshot(plan_id)
        intake = Intake.model_validate(util.loads(row["draft_json"]))
        history = self.store.observation_history(row["loop_id"],"primary_metric",limit=200)
        verified = [o for o in reversed(history) if o.usable_as_ground_truth]
        def error_gap(v):
            t=float(intake.target_value)
            if intake.target_mode == "at_least": return max(t-v,0.0)
            if intake.target_mode == "at_most": return max(v-t,0.0)
            return abs(t-v)
        first=verified[0] if verified else None
        last=verified[-1] if verified else None
        gap_change=(error_gap(first.value)-error_gap(last.value)) if first and last and len(verified)>=2 else None
        if gap_change is None: trend="insufficient_verified_history"
        elif gap_change > 1e-9: trend="closer_to_target"
        elif gap_change < -1e-9: trend="farther_from_target"
        else: trend="unchanged"
        deployments=self.interventions(plan_id)
        applied=[i for i in deployments if i["status"] in ("applied_pending_verification","verified_observational")]
        content = {"kind":kind,"time":now or util.utcnow_iso(),"goal":intake.desired_outcome,
                   "metric":intake.metric_name,"checkpoint":snapshot,
                   "verified_observation_count":len(verified),
                   "trend":trend,"gap_change_from_first_verified":gap_change,
                   "recorded_interventions":len(applied),
                   "unverified_interventions":len([i for i in applied if i["status"]=="applied_pending_verification"]),
                   "deadline_missed":snapshot.get("deadline_missed"),
                   "side_effect_measurements_missing":snapshot.get("side_effect_metrics_not_independently_monitored", []),
                   "analysis_limit":"趋势不证明干预因果，缺失的副作用观测不可假定为安全",
                   "next_step":snapshot.get("recommendation"),
                   "decision_required": bool(snapshot["out_of_tolerance"] and intake.action_authority=="approval_required")}
        with self.store._lock, self.store._conn:
            cur = self.store._conn.execute("INSERT INTO commissioning_outbox(plan_id,created_at,kind,payload_json) VALUES(?,?,?,?)",
                                           (plan_id,now or util.utcnow_iso(),kind,util.dumps(content)))
            if kind=="checkpoint":
                self.store._conn.execute("UPDATE commissioning_plan SET last_report_at=?,updated_at=? WHERE id=?",
                                         (now or util.utcnow_iso(),util.utcnow_iso(),plan_id))
        return {"id":cur.lastrowid,**content}

    def tick(self, now: str | None = None) -> list[str]:
        """由持久运行的服务调度线程调用；只写报告，不发外部消息。"""
        now = now or util.utcnow_iso()
        with self.store._lock:
            rows = [dict(row) for row in self.store._conn.execute("SELECT * FROM commissioning_plan WHERE status='running'").fetchall()]
        made=[]
        for row in rows:
            try:
                if not row["loop_id"]:
                    continue
                data=Intake.model_validate(util.loads(row["draft_json"]))
                if util.seconds_between(row["last_report_at"] or row["started_at"], now) >= float(data.report_seconds or 86400):
                    self.report(row["id"],now=now)
                    made.append(row["id"])
            except Exception:
                # 错误不能阻断其他计划；调用方记录日志
                import logging
                logging.getLogger("fcc.commissioning").exception("checkpoint failed: %s",row["id"])
        return made

    def outbox(self, plan_id: str, limit: int = 50, unread_only: bool = False) -> list[dict[str, Any]]:
        self._row(plan_id)
        limit = max(1,min(int(limit),200))
        with self.store._lock:
            rows=self.store._conn.execute(
                "SELECT * FROM commissioning_outbox WHERE plan_id=?" + (" AND read_at IS NULL" if unread_only else "") + " ORDER BY id DESC LIMIT ?",(plan_id,limit)).fetchall()
        return [{"id":r["id"],"created_at":r["created_at"],"kind":r["kind"],
                 "payload":util.loads(r["payload_json"]),"read_at":r["read_at"]} for r in rows]

    def acknowledge(self, plan_id: str, report_id: int) -> dict[str, Any]:
        with self.store._lock,self.store._conn:
            self._row(plan_id)
            cur=self.store._conn.execute("UPDATE commissioning_outbox SET read_at=? WHERE id=? AND plan_id=? AND read_at IS NULL",
                                         (util.utcnow_iso(),report_id,plan_id))
        return {"ok":cur.rowcount>0}
