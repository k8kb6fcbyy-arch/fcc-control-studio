"""HTTP 服务 + 单页面板。

启动：python -m fcc.server --port 8099
面板：http://127.0.0.1:8099/
"""

from __future__ import annotations

import argparse
import logging
import os
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel, Field

from . import util
from .actuator import AdapterRegistry, ManualAdapter
from .capability import capabilities_payload
from .engine import Engine, LoopNotFound
from .models import LoopConfig, ObservationIn
from .commissioning import CommissioningService
from .simplant import SimulatedPlant
from .store import Store

log = logging.getLogger("fcc.server")

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


# --------------------------------------------------------------------------
# 模拟对象管理（给面板做演示用，不是框架核心）
# --------------------------------------------------------------------------


class SimPlantManager:
    """把 SimulatedPlant 挂成观测源 + 执行适配器，并按秒推进它。"""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.plants: dict[str, SimulatedPlant] = {}
        self._registry = AdapterRegistry()
        self._registry.register("manual", ManualAdapter())
        self._lock = threading.RLock()

    def create(self, name: str | None = None, **kwargs: Any) -> str:
        with self._lock:
            plant_name = name or f"sim_{util.utcnow().strftime('%H%M%S')}_{len(self.plants)}"
            plant = SimulatedPlant(**kwargs)
            self.plants[plant_name] = plant
            self._registry.register(plant_name, plant)
            self.engine.register_actuator(plant_name, plant)

            def reader(_loop_id: str, _spec) -> dict[str, Any]:
                reading = plant.read()
                return {"metric_id": _spec.metric_id, "value": reading["value"],
                        "source_type": "measured", "note": "simulated_plant"}

            self.engine.register_observation_adapter(plant_name, reader)
            return plant_name

    def step_all(self, dt: float = 1.0) -> None:
        with self._lock:
            for plant in self.plants.values():
                plant.advance(dt)

    def get(self, name: str) -> SimulatedPlant | None:
        return self.plants.get(name)


# --------------------------------------------------------------------------
# 应用
# --------------------------------------------------------------------------


class LoopCreateBody(BaseModel):
    config: dict[str, Any]


class ObservationBody(BaseModel):
    metric_id: str
    value: float
    source_type: str = "measured"
    confidence: float | None = None
    evidence: list[str] | None = None
    is_ground_truth: bool = False
    model: str | None = None
    prompt_version: str | None = None
    note: str | None = None


class FeedbackBody(BaseModel):
    action_id: int | None = None
    decision: str = "approve"
    value: float | None = None
    goal_id: str | None = None
    risk_level: str = "medium"
    rollback_method: str | None = "restore_previous_value"
    note: str = ""
    idempotency_key: str | None = None


class SimBody(BaseModel):
    tau: float = Field(default=3.0, gt=0.01)
    dead_time: float = Field(default=1.0, ge=0)
    noise_sigma: float = Field(default=0.3, ge=0)
    x0: float = 0.0
    gain: float = 1.0


def create_app(db_path: str | None = None) -> FastAPI:
    store = Store(db_path or os.environ.get("FCC_DB", "fcc.db"))
    engine = Engine(store, AdapterRegistry())
    engine.register_actuator("manual", ManualAdapter())
    sims = SimPlantManager(engine)
    commissioning = CommissioningService(store, engine)

    app = FastAPI(title="FCC · 通用反馈控制核心", version="1.5.0")
    # Local-only prototype: reject DNS-rebinding / unexpected Host headers.
    # testserver is Starlette's HTTP test-client host and is not a public DNS name.
    app.add_middleware(TrustedHostMiddleware,
                       allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])

    @app.middleware("http")
    async def provenance_header(request, call_next):
        # In browsers, disallow cross-site state-changing requests to the
        # unauthenticated loopback service. This does not replace auth.
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            fetch_site = request.headers.get("sec-fetch-site", "")
            if fetch_site == "cross-site":
                return JSONResponse({"detail": "cross-site writes are forbidden"}, status_code=403)
            if origin:
                parsed = urlsplit(origin)
                expected = request.headers.get("host", "").casefold()
                if parsed.scheme not in {"http", "https"} or parsed.netloc.casefold() != expected:
                    return JSONResponse({"detail": "cross-origin writes are forbidden"}, status_code=403)
        response = await call_next(request)
        response.headers["X-FCC-Attribution"] = (
            "FCC Control Studio; Chans; chenchen_wang@foxmail.com"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response
    app.state.engine = engine
    app.state.store = store
    app.state.sims = sims
    app.state.commissioning = commissioning
    app.state.scheduler_thread = None

    # -- 基础 -------------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        scheduler_thread = app.state.scheduler_thread
        return {"ok": True, "service": "fcc", "version": "1.5.0",
                "utc": util.utcnow_iso(), "loops": len(store.list_loops()),
                "scheduler_running": bool(scheduler_thread and scheduler_thread.is_alive()),
                "source_credit": "FCC Control Studio — Chans (chenchen_wang@foxmail.com)",
                "last_scheduler_tick": scheduler_thread.last_tick if scheduler_thread else None}

    @app.get("/api/capabilities")
    def capabilities() -> dict[str, Any]:
        return capabilities_payload()

    @app.get("/api/adapters")
    def adapters() -> dict[str, Any]:
        return {"actuators": engine.registry.names(),
                "observation_sources": sorted(engine.obs_adapters),
                "sim_plants": sorted(sims.plants)}

    @app.get("/api/bootstrap")
    def bootstrap_status() -> dict[str, Any]:
        """Factual local-service status; AI-host permissions cannot be inferred."""
        sched = app.state.scheduler_thread
        return {
            "version": "1.5.0", "service_running": True,
            "scheduler_running": bool(sched and sched.is_alive()),
            "last_scheduler_tick": sched.last_tick if sched else None,
            "observation_adapters": sorted(engine.obs_adapters),
            "report_delivery": "local_outbox_only", "automatic_message_delivery": False,
            "ai_host_permissions": "unknown",
            "recommended_setup": "/setup",
            "source_credit": "FCC Control Studio — Chans (chenchen_wang@foxmail.com)",
        }

    # -- 模拟对象 ---------------------------------------------------------

    @app.post("/api/sim/plants")
    def create_sim(body: SimBody) -> dict[str, Any]:
        name = sims.create(tau=body.tau, dead_time=body.dead_time,
                           noise_sigma=body.noise_sigma, x0=body.x0, gain=body.gain)
        return {"name": name}

    @app.post("/api/sim/plants/{name}/step")
    def step_sim(name: str, times: int = 1) -> dict[str, Any]:
        plant = sims.get(name)
        if plant is None:
            raise HTTPException(404, "模拟对象不存在")
        for _ in range(min(500, max(1, times))):
            plant.advance()
        return {"ok": True, "step": plant.step_count}

    # -- 通用需求采集 / 量化建模 / 长期反馈 -------------------------------

    @app.get("/api/onboarding/questions")
    def onboarding_questions() -> dict[str, Any]:
        from .commissioning import FIELDS
        return {"fields": list(FIELDS), "description":"逐阶段采集，不强制一次填写全部"}

    @app.post("/api/onboarding/plans")
    def onboarding_create(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return commissioning.create(body)
        except Exception as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.get("/api/onboarding/plans")
    def onboarding_list() -> dict[str, Any]:
        return {"plans":commissioning.list()}

    @app.get("/api/onboarding/plans/{plan_id}")
    def onboarding_get(plan_id: str) -> dict[str, Any]:
        try:
            return commissioning.get(plan_id)
        except KeyError as exc:
            raise HTTPException(404,str(exc)) from exc

    @app.patch("/api/onboarding/plans/{plan_id}")
    def onboarding_patch(plan_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return commissioning.update(plan_id,body)
        except KeyError as exc:
            raise HTTPException(404,str(exc)) from exc
        except Exception as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.get("/api/onboarding/plans/{plan_id}/compile")
    def onboarding_compile(plan_id: str) -> dict[str, Any]:
        try:
            return commissioning.compile(plan_id)
        except KeyError as exc:
            raise HTTPException(404,str(exc)) from exc
        except Exception as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.post("/api/onboarding/plans/{plan_id}/activate")
    def onboarding_activate(plan_id: str) -> dict[str, Any]:
        try:
            return commissioning.activate(plan_id)
        except KeyError as exc:
            raise HTTPException(404,str(exc)) from exc
        except Exception as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.post("/api/onboarding/plans/{plan_id}/pause")
    def onboarding_pause(plan_id: str) -> dict[str, Any]:
        try:
            return commissioning.pause(plan_id)
        except KeyError as exc:
            raise HTTPException(404,str(exc)) from exc
        except Exception as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.post("/api/onboarding/plans/{plan_id}/resume")
    def onboarding_resume(plan_id: str) -> dict[str, Any]:
        try:
            return commissioning.resume(plan_id)
        except KeyError as exc:
            raise HTTPException(404,str(exc)) from exc
        except Exception as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.post("/api/onboarding/plans/{plan_id}/observations")
    def onboarding_observe(plan_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            return commissioning.observation(plan_id,body)
        except KeyError as exc:
            raise HTTPException(404,str(exc)) from exc
        except Exception as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.get("/api/onboarding/plans/{plan_id}/status")
    def onboarding_status(plan_id: str) -> dict[str, Any]:
        try:
            return commissioning.snapshot(plan_id)
        except KeyError as exc:
            raise HTTPException(404,str(exc)) from exc

    @app.post("/api/onboarding/plans/{plan_id}/checkpoint")
    def onboarding_checkpoint(plan_id: str) -> dict[str, Any]:
        try:
            return commissioning.report(plan_id,kind="manual_checkpoint")
        except KeyError as exc:
            raise HTTPException(404,str(exc)) from exc
        except Exception as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.post("/api/onboarding/plans/{plan_id}/interventions")
    def propose_intervention(plan_id: str,body: dict[str,Any]) -> dict[str,Any]:
        try:
            return commissioning.propose_intervention(plan_id,body.get("description", ""),body.get("reason", ""))
        except KeyError as exc:
            raise HTTPException(404,str(exc)) from exc
        except Exception as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.get("/api/onboarding/plans/{plan_id}/interventions")
    def get_interventions(plan_id: str) -> dict[str,Any]:
        try:
            return {"interventions":commissioning.interventions(plan_id)}
        except KeyError as exc:
            raise HTTPException(404,str(exc)) from exc

    @app.post("/api/onboarding/plans/{plan_id}/interventions/{intervention_id}/applied")
    def applied_intervention(plan_id: str,intervention_id: int,body: dict[str,Any]) -> dict[str,Any]:
        try:
            return commissioning.mark_applied(plan_id,intervention_id,actor=body.get("actor",""),evidence=body.get("evidence",""))
        except KeyError as exc:
            raise HTTPException(404,str(exc)) from exc
        except Exception as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.post("/api/onboarding/plans/{plan_id}/interventions/{intervention_id}/verify")
    def verify_intervention(plan_id: str,intervention_id: int) -> dict[str,Any]:
        try:
            return commissioning.verify_intervention(plan_id,intervention_id)
        except KeyError as exc:
            raise HTTPException(404,str(exc)) from exc
        except Exception as exc:
            raise HTTPException(422,str(exc)) from exc

    @app.get("/api/onboarding/plans/{plan_id}/outbox")
    def onboarding_outbox(plan_id: str,limit: int=50,unread_only: bool=False) -> dict[str, Any]:
        try:
            return {"reports":commissioning.outbox(plan_id,limit,unread_only)}
        except KeyError as exc:
            raise HTTPException(404,str(exc)) from exc

    @app.post("/api/onboarding/plans/{plan_id}/outbox/{report_id}/ack")
    def onboarding_ack(plan_id: str,report_id: int) -> dict[str, Any]:
        try:
            return commissioning.acknowledge(plan_id,report_id)
        except KeyError as exc:
            raise HTTPException(404,str(exc)) from exc

    # -- loop -------------------------------------------------------------

    @app.get("/api/loops")
    def list_loops() -> dict[str, Any]:
        return {"loops": store.list_loops()}

    @app.post("/api/loops")
    def create_loop(body: LoopCreateBody) -> dict[str, Any]:
        try:
            cfg = LoopConfig.model_validate(body.config)
            loop_id = engine.create_loop(cfg)
        except Exception as exc:  # noqa: BLE001 - 配置错误要原样回给用户
            raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc
        return {"loop_id": loop_id}

    @app.get("/api/loops/{loop_id}")
    def get_loop(loop_id: str) -> dict[str, Any]:
        try:
            return engine.get_state(loop_id)
        except LoopNotFound:
            raise HTTPException(404, "loop 不存在")

    @app.delete("/api/loops/{loop_id}")
    def delete_loop(loop_id: str) -> dict[str, Any]:
        try:
            engine.delete_loop(loop_id)
        except LoopNotFound:
            raise HTTPException(404, "loop 不存在")
        return {"ok": True}

    @app.post("/api/loops/{loop_id}/start")
    def start_loop(loop_id: str) -> dict[str, Any]:
        return _lifecycle(loop_id, "start")

    @app.post("/api/loops/{loop_id}/pause")
    def pause_loop(loop_id: str) -> dict[str, Any]:
        return _lifecycle(loop_id, "pause")

    @app.post("/api/loops/{loop_id}/resume")
    def resume_loop(loop_id: str) -> dict[str, Any]:
        return _lifecycle(loop_id, "resume")

    @app.post("/api/loops/{loop_id}/stop")
    def stop_loop(loop_id: str) -> dict[str, Any]:
        return _lifecycle(loop_id, "stop")

    def _lifecycle(loop_id: str, action: str) -> dict[str, Any]:
        try:
            enabled = action in ("start", "resume")
            engine.set_enabled(loop_id, enabled, "api")
            return {"ok": True, "enabled": enabled}
        except LoopNotFound:
            raise HTTPException(404, "loop 不存在")

    @app.post("/api/loops/{loop_id}/cycle")
    def run_cycle(loop_id: str, event_key: str | None = None) -> dict[str, Any]:
        try:
            result = engine.run_cycle(loop_id, event_key=event_key)
        except LoopNotFound:
            raise HTTPException(404, "loop 不存在")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, f"{type(exc).__name__}: {exc}") from exc
        return result.model_dump()

    @app.get("/api/loops/{loop_id}/history")
    def history(loop_id: str, limit: int = 50) -> dict[str, Any]:
        try:
            return {"cycles": engine.get_history(loop_id, limit)}
        except LoopNotFound:
            raise HTTPException(404, "loop 不存在")

    @app.get("/api/loops/{loop_id}/verification")
    def verification(loop_id: str) -> dict[str, Any]:
        try:
            return engine.get_verification_report(loop_id)
        except LoopNotFound:
            raise HTTPException(404, "loop 不存在")

    @app.patch("/api/loops/{loop_id}/config")
    def patch_config(loop_id: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            cfg = engine.update_config(loop_id, body)
        except LoopNotFound:
            raise HTTPException(404, "loop 不存在")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"{type(exc).__name__}: {exc}") from exc
        return {"ok": True, "config": cfg.model_dump()}

    @app.post("/api/loops/{loop_id}/rollback-strategy/{version}")
    def rollback_strategy(loop_id: str, version: int) -> dict[str, Any]:
        return engine.rollback_strategy(loop_id, version)

    # -- 观测 / 反馈 ------------------------------------------------------

    @app.post("/api/loops/{loop_id}/observations")
    def submit_observation(loop_id: str, body: ObservationBody) -> dict[str, Any]:
        try:
            payload = ObservationIn.model_validate(body.model_dump())
            return engine.submit_observation(loop_id, payload)
        except LoopNotFound:
            raise HTTPException(404, "loop 不存在")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(422, f"{type(exc).__name__}: {exc}") from exc

    @app.post("/api/loops/{loop_id}/feedback")
    def feedback(loop_id: str, body: FeedbackBody) -> dict[str, Any]:
        try:
            return engine.submit_human_feedback(loop_id, body.model_dump())
        except LoopNotFound:
            raise HTTPException(404, "loop 不存在")

    @app.get("/api/loops/{loop_id}/audit")
    def audit(loop_id: str, cycle_id: int | None = None, limit: int = 200) -> dict[str, Any]:
        try:
            if cycle_id is not None:
                return engine.audit_chain(loop_id, cycle_id)
            return {"entries": engine.audit.history(loop_id, limit)}
        except LoopNotFound:
            raise HTTPException(404, "loop 不存在")

    # -- 审批队列 ---------------------------------------------------------

    @app.get("/api/actions/pending")
    def pending_actions(loop_id: str | None = None) -> dict[str, Any]:
        loops = [loop_id] if loop_id else [r["id"] for r in store.list_loops()]
        items: list[dict[str, Any]] = []
        for lid in loops:
            items.extend(store.actions_awaiting_approval(lid))
        return {"actions": items}

    @app.post("/api/actions/{action_id}/approve")
    def approve(action_id: int) -> dict[str, Any]:
        return engine.approve_action(action_id, "api")

    @app.post("/api/actions/{action_id}/reject")
    def reject(action_id: int, reason: str = "") -> dict[str, Any]:
        return engine.reject_action(action_id, "api", reason)

    @app.post("/api/actions/{action_id}/rollback")
    def rollback(action_id: int) -> dict[str, Any]:
        return engine.rollback_action(action_id, "api")

    @app.get("/api/actions")
    def list_actions(loop_id: str, limit: int = 100) -> dict[str, Any]:
        return {"actions": store.list_actions(loop_id, limit=limit)}

    # -- 面板 -------------------------------------------------------------

    @app.get("/start")
    def bootstrap_page() -> FileResponse:
        path = os.path.join(WEB_DIR, "start.html")
        if not os.path.exists(path):
            raise HTTPException(500, "启动引导页面缺失")
        return FileResponse(path, media_type="text/html")

    @app.get("/setup")
    def setup() -> FileResponse:
        path = os.path.join(WEB_DIR,"setup.html")
        if not os.path.exists(path):
            raise HTTPException(500,"需求采集面板缺失")
        return FileResponse(path,media_type="text/html")

    @app.get("/expert")
    def expert() -> FileResponse:
        path = os.path.join(WEB_DIR, "index.html")
        if not os.path.exists(path):
            raise HTTPException(500, "面板文件缺失: web/index.html")
        return FileResponse(path, media_type="text/html")

    @app.get("/")
    @app.get("/easy")
    def easy() -> FileResponse:
        path = os.path.join(WEB_DIR, "easy.html")
        if not os.path.exists(path):
            raise HTTPException(500, "简易面板文件缺失: web/easy.html")
        return FileResponse(path, media_type="text/html")

    @app.exception_handler(ValueError)
    def value_error_handler(_request, exc: ValueError):  # noqa: ANN001
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


# --------------------------------------------------------------------------
# 调度线程
# --------------------------------------------------------------------------


class SchedulerThread(threading.Thread):
    """每秒一拍：推进模拟对象 + 让到期的 loop 跑周期。

    单个 loop 抛异常不影响其他 loop（A10），engine.tick 内部已隔离。
    """

    def __init__(self, engine: Engine, sims: SimPlantManager, commissioning: CommissioningService | None = None, interval: float = 1.0) -> None:
        super().__init__(daemon=True, name="fcc-scheduler")
        self.engine = engine
        self.sims = sims
        self.commissioning = commissioning
        self.interval = interval
        self._stop_event = threading.Event()
        self.last_tick: str | None = None

    def run(self) -> None:
        while not self._stop_event.wait(self.interval):
            try:
                self.sims.step_all(1.0)
                self.engine.tick()
                if self.commissioning is not None:
                    self.commissioning.tick()
                self.last_tick = util.utcnow_iso()
            except Exception:  # noqa: BLE001
                log.exception("调度线程异常（已吞掉，继续跑）")

    def stop(self) -> None:
        self._stop_event.set()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="fcc.server", description="FCC 服务")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--db", default=os.environ.get("FCC_DB", "fcc.db"))
    parser.add_argument("--no-scheduler", action="store_true", help="不启动后台调度线程")
    args = parser.parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("This prototype can listen only on localhost. Do not publish unauthenticated APIs.")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = create_app(args.db)
    thread = None
    if not args.no_scheduler:
        thread = SchedulerThread(app.state.engine, app.state.sims, app.state.commissioning)
        app.state.scheduler_thread = thread
        thread.start()
        log.info("后台调度线程已启动（1s/拍）")

    import uvicorn

    print(f"FCC AI 自动引导入口: http://{args.host}:{args.port}/start")
    print(f"FCC 引导式项目创建: http://{args.host}:{args.port}/setup")
    print(f"FCC I/O 面板: http://{args.host}:{args.port}/")
    print(f"API 文档: http://{args.host}:{args.port}/docs")
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        if thread is not None:
            thread.stop()


if __name__ == "__main__":
    main()
