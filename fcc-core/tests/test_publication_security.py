"""Release checks for GitHub redistribution."""
import pytest
from fastapi.testclient import TestClient
from fcc.server import create_app, main


def test_public_api_contains_source_and_safety_headers(tmp_path):
    app = create_app(str(tmp_path / "test.db"))
    with TestClient(app) as client:
        result = client.get("/api/health")
        assert result.status_code == 200
        assert "Chans" in result.json()["source_credit"]
        assert "Chans" in result.headers["x-fcc-attribution"]
        assert result.headers["x-content-type-options"] == "nosniff"


def test_unexpected_host_rejected(tmp_path):
    app = create_app(str(tmp_path / "test.db"))
    with TestClient(app) as client:
        response = client.get("/api/health", headers={"host": "malicious.example"})
        assert response.status_code == 400


def test_non_loopback_cli_is_refused():
    with pytest.raises(SystemExit) as exc:
        main(["--host", "0.0.0.0", "--no-scheduler"])
    assert exc.value.code == 2


def test_html_contains_attribution(tmp_path):
    app = create_app(str(tmp_path / "test.db"))
    with TestClient(app) as client:
        for route in ("/", "/setup", "/expert"):
            response = client.get(route)
            assert response.status_code == 200
            assert "Powered by FCC Control Studio" in response.text


def test_cross_origin_write_rejected(tmp_path):
    app = create_app(str(tmp_path / "test.db"))
    with TestClient(app) as client:
        r = client.post("/api/onboarding/plans", json={}, headers={"Origin":"https://other.example"})
        assert r.status_code == 403
        r = client.post("/api/onboarding/plans", json={}, headers={"Origin":"http://testserver"})
        assert r.status_code == 200


def test_cross_site_fetch_header_rejected(tmp_path):
    app = create_app(str(tmp_path / "test.db"))
    with TestClient(app) as client:
        r = client.post("/api/onboarding/plans", json={}, headers={"Sec-Fetch-Site":"cross-site"})
        assert r.status_code == 403


def test_expert_dashboard_escapes_user_visible_fields():
    from fcc.server import WEB_DIR
    from pathlib import Path
    html = (Path(WEB_DIR) / "index.html").read_text(encoding="utf-8")
    assert "${escapeHtml(obs.unit||'')}" in html
    assert "${escapeHtml(a.decision_reason||'')}" in html
    assert "${escapeHtml(v.note)}" in html
