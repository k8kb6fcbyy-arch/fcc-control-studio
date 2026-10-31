"""AI Bootstrap 1.5: no false autonomy or silent installation."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from fcc.bootstrap import detect, local_url, main
from fcc.server import create_app


def test_loopback_only():
    for value in ("https://127.0.0.1:8099", "http://example.com", "http://127.0.0.1:8099/private",
                  "http://127.0.0.1:8099?x=1", "http://user:pass@localhost:8099", "file:///etc/passwd"):
        with pytest.raises(ValueError):
            local_url(value)
    assert local_url("http://127.0.0.1:8099/") == "http://127.0.0.1:8099"


def test_conversation_mode_without_core(tmp_path, monkeypatch):
    monkeypatch.setattr("fcc.bootstrap._read_json", lambda _: {})
    state = detect(root=tmp_path)
    assert state["mode"] == "conversation_only"
    assert state["service"]["automatic_observation_confirmed"] is False
    assert state["ai_host"]["persistent_runtime"] == "unknown"
    assert not (tmp_path / "fcc-core").exists()


def test_core_present_but_no_server_is_not_running(tmp_path, monkeypatch):
    core = tmp_path / "fcc-core"
    (core / "src/fcc").mkdir(parents=True)
    (core / "src/fcc/server.py").write_text("# test", encoding="utf-8")
    (core / "pyproject.toml").write_text("# test", encoding="utf-8")
    monkeypatch.setattr("fcc.bootstrap._read_json", lambda _: {})
    state = detect(root=tmp_path)
    assert state["mode"] == "local_setup_available"
    assert not state["service"]["scheduler_running"]


def test_detect_scheduler_status_and_no_auto_data(tmp_path, monkeypatch):
    def read(url):
        if url.endswith("/api/health"):
            return {"ok": True, "service": "fcc", "scheduler_running": True, "version": "1.5.0"}
        return {"observation_sources": ["manual"]}
    monkeypatch.setattr("fcc.bootstrap._read_json", read)
    state = detect(root=tmp_path)
    assert state["mode"] == "local_scheduler_available"
    assert state["service"]["automatic_observation_confirmed"] is False
    assert state["service"]["report_delivery"] == "local_outbox_only"


def test_detect_service_without_scheduler(tmp_path, monkeypatch):
    monkeypatch.setattr("fcc.bootstrap._read_json", lambda _: {"ok":True, "service":"fcc", "scheduler_running":False})
    state = detect(root=tmp_path)
    assert state["mode"] == "local_service_without_scheduler"


def test_detect_unknown_service_not_trusted(tmp_path, monkeypatch):
    monkeypatch.setattr("fcc.bootstrap._read_json", lambda _: {"ok":True, "service":"something_else"})
    state = detect(root=tmp_path)
    assert state["mode"] == "service_mismatch"


def test_install_or_serve_requires_explicit_yes():
    for arg in ("install", "serve"):
        with pytest.raises(SystemExit) as exc:
            main([arg])
        assert exc.value.code == 2


def test_doctor_rejects_external_endpoint():
    with pytest.raises(SystemExit) as exc:
        main(["doctor", "--server", "http://8.8.8.8:8099"])
    assert exc.value.code == 2


def test_bootstrap_web_ui_and_status(tmp_path):
    with TestClient(create_app(str(tmp_path / "isolated.sqlite"))) as client:
        page=client.get("/start")
        assert page.status_code == 200
        assert "模式 A" in page.text and "模式 B" in page.text
        assert "chenchen_wang@foxmail.com" in page.text
        data=client.get("/api/bootstrap").json()
        assert data["version"] == "1.5.0"
        assert data["scheduler_running"] is False
        assert data["automatic_message_delivery"] is False
        assert data["ai_host_permissions"] == "unknown"


def test_skill_only_bootstrap_downgrades(tmp_path):
    script = Path(__file__).resolve().parents[2] / "fcc-skill" / "scripts" / "fcc_bootstrap.py"
    target=tmp_path / "fcc-skill" / "scripts" / "fcc_bootstrap.py"
    target.parent.mkdir(parents=True)
    target.write_bytes(script.read_bytes())
    result=subprocess.run([sys.executable,str(target),"doctor","--json"],capture_output=True,text=True,check=True)
    data=json.loads(result.stdout)
    assert data["mode"] == "conversation_only"
    assert data["core"]["source_present"] is False


def test_full_bundle_bootstrap_cli_json(tmp_path):
    script = Path(__file__).resolve().parents[2] / "fcc-skill" / "scripts" / "fcc_bootstrap.py"
    result = subprocess.run([sys.executable,str(script),"doctor","--server","http://127.0.0.1:65497","--json"],
                            capture_output=True,text=True,check=True,timeout=10)
    state=json.loads(result.stdout)
    assert state["version"] == "1.5.0"
    assert state["core"]["source_present"] is True
    assert state["mode"] == "local_setup_available"
