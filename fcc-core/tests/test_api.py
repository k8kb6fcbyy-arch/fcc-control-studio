"""HTTP 通道：A1 一条命令起服务、A12 能力台账、A13 面板可用。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import cfg
from fcc.server import create_app


@pytest.fixture()
def api(tmp_path):
    app = create_app(str(tmp_path / "api.db"))
    with TestClient(app) as c:
        yield c


def _create(api, data: dict) -> str:
    r = api.post("/api/loops", json={"config": data})
    assert r.status_code == 200, r.text
    return r.json()["loop_id"]


def test_A1_health(api):
    r = api.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_A12_capabilities_declare_every_module(api):
    r = api.get("/api/capabilities")
    assert r.status_code == 200
    body = r.json()
    modules = body["modules"]
    assert modules["controller.pid"]["status"] == "implemented"
    assert modules["learning.auto_upgrade"]["status"] == "not_implemented"
    assert modules["verification"]["status"] == "implemented"
    assert body["counts"]["implemented"] > 0
    for name, item in modules.items():
        assert item["status"] in ("implemented", "interface_only", "not_implemented")
        assert item["note"], f"{name} 缺说明"


def test_A12_red_lines_declared(api):
    body = api.get("/api/capabilities").json()
    assert body["red_lines"]["ai_only_deviation_cannot_intervene"] is True
    assert body["red_lines"]["ai_action_forced_approval"] is True
    assert body["red_lines"]["no_success_without_verification"] is True


def test_A13_panel_serves_html(api):
    r = api.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    assert "FCC" in body
    assert "Input · 输入端" in body
    assert "Output · 输出端" in body
    assert "agentPrompt" in body
    r_expert = api.get("/expert")
    assert r_expert.status_code == 200
    assert "echarts" in r_expert.text.lower()
    assert "未实现" in r_expert.text


def test_create_loop_and_run_cycle_over_http(api):
    data = cfg()
    loop = _create(api, data)
    assert api.post(f"/api/loops/{loop}/observations",
                    json={"metric_id": "temp", "value": 10.0}).status_code == 200
    r = api.post(f"/api/loops/{loop}/cycle")
    assert r.status_code == 200
    body = r.json()
    assert body["errors"][0]["error"] == pytest.approx(40.0)
    assert body["verification_status"] == "pending_verification"

    state = api.get(f"/api/loops/{loop}").json()
    assert state["loop_id"] == loop
    assert state["last_cycle"]["cycle_id"] == body["cycle_id"]


def test_loop_state_config_enabled_matches_runtime(api):
    """D2：顶层 enabled（运行时）与 config.enabled（创建快照）不许互相矛盾。"""
    loop = _create(api, cfg())
    state = api.get(f"/api/loops/{loop}").json()
    assert state["enabled"] is False
    assert state["config"]["enabled"] is False

    assert api.post(f"/api/loops/{loop}/start").status_code == 200
    state = api.get(f"/api/loops/{loop}").json()
    assert state["enabled"] is True
    assert state["config"]["enabled"] is True

    assert api.post(f"/api/loops/{loop}/pause").status_code == 200
    state = api.get(f"/api/loops/{loop}").json()
    assert state["enabled"] is False
    assert state["config"]["enabled"] is False


def test_http_ai_observation_validation(api):
    loop = _create(api, cfg())
    r = api.post(f"/api/loops/{loop}/observations", json={
        "metric_id": "temp", "value": 10.0, "source_type": "ai_inference"})
    assert r.status_code == 422
    assert "confidence" in r.text


def test_http_approval_flow(api):
    data = cfg()
    data["actuator"]["permission_mode"] = "require_approval"
    loop = _create(api, data)
    api.post(f"/api/loops/{loop}/observations", json={"metric_id": "temp", "value": 10.0})
    api.post(f"/api/loops/{loop}/cycle")

    pending = api.get("/api/actions/pending").json()["actions"]
    assert len(pending) == 1
    aid = pending[0]["id"]
    r = api.post(f"/api/actions/{aid}/approve")
    assert r.status_code == 200
    assert r.json()["action"]["status"] == "pending_verification"


def test_http_reject(api):
    data = cfg()
    data["actuator"]["permission_mode"] = "require_approval"
    loop = _create(api, data)
    api.post(f"/api/loops/{loop}/observations", json={"metric_id": "temp", "value": 10.0})
    api.post(f"/api/loops/{loop}/cycle")
    aid = api.get("/api/actions/pending").json()["actions"][0]["id"]
    r = api.post(f"/api/actions/{aid}/reject", params={"reason": "不需要"})
    assert r.json()["action"]["status"] == "rejected"


def test_http_404_and_400(api):
    assert api.get("/api/loops/nope").status_code == 404
    bad = cfg()
    bad["goals"][0]["target_variable"] = "unregistered"
    r = api.post("/api/loops", json={"config": bad})
    assert r.status_code == 400


def test_http_history_and_audit(api):
    loop = _create(api, cfg())
    api.post(f"/api/loops/{loop}/observations", json={"metric_id": "temp", "value": 10.0})
    cycle = api.post(f"/api/loops/{loop}/cycle").json()
    hist = api.get(f"/api/loops/{loop}/history").json()["cycles"]
    assert len(hist) == 1
    audit = api.get(f"/api/loops/{loop}/audit",
                    params={"cycle_id": cycle["cycle_id"]}).json()
    assert audit["chain"]
    assert {e["kind"] for e in audit["chain"]} >= {"cycle_started", "action_executed"}


def test_http_sim_plant_endpoint(api):
    r = api.post("/api/sim/plants", json={"tau": 2.0, "dead_time": 0.0,
                                          "noise_sigma": 0.1, "x0": 0.0})
    assert r.status_code == 200
    name = r.json()["name"]
    adapters = api.get("/api/adapters").json()
    assert name in adapters["sim_plants"]
    assert name in adapters["actuators"]


def test_http_verification_report(api):
    loop = _create(api, cfg())
    api.post(f"/api/loops/{loop}/observations", json={"metric_id": "temp", "value": 10.0})
    api.post(f"/api/loops/{loop}/cycle")
    body = api.get(f"/api/loops/{loop}/verification").json()
    assert body["total"] == 0
    assert set(body["counts"]) == {"improved", "unchanged", "worsened",
                                   "failed", "inconclusive"}


def test_http_config_patch(api):
    loop = _create(api, cfg())
    r = api.patch(f"/api/loops/{loop}/config",
                  json={"goals": [{"goal_id": "g1", "target_variable": "temp",
                                   "setpoint": {"kind": "value", "value": 77.0},
                                   "tolerance": 1.0}]})
    assert r.status_code == 200
    assert r.json()["config"]["goals"][0]["setpoint"]["value"] == 77.0
