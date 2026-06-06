"""
Tests for the DarkWeb Search API.

Run with: docker-compose run --rm api pytest tests/ -v
Or locally: pytest tests/ -v

These tests mock the Tor client so they work without a running Tor container.
"""
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _patch_config(tmp_path, monkeypatch):
    """Provide a minimal config.yaml for all tests."""
    config_content = """
api:
  title: "Test API"
  version: "0.0.1"
  description: "Test"
  host: "0.0.0.0"
  port: 8000
tor:
  proxy: "socks5h://tor:9050"
  check_url: "http://check.torproject.org/"
crawling:
  delay: 0
  max_depth: 1
  max_pages: 5
  max_results: 3
  timeout: 10
  seed_urls:
    - "http://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion"
retry:
  count: 1
  backoff_factor: 0
security:
  user_agent: "TestBot/1.0"
cors:
  origins:
    - "*"
jobs:
  max_workers: 1
  db_path: "{db_path}"
webhook:
  db_path: "{webhook_db_path}"
""".format(
        db_path=str(tmp_path / "test_jobs.db"),
        webhook_db_path=str(tmp_path / "test_webhooks.db"),
    )

    config_file = tmp_path / "config.yaml"
    config_file.write_text(config_content)

    # Reload settings from test config before importing app
    monkeypatch.setattr("config.settings", _make_settings(str(config_file)))


def _make_settings(config_path: str):
    """Create a fresh Settings instance from a config file."""
    from config import Settings
    return Settings(config_path)


@pytest.fixture()
def client(_patch_config):
    """Create a test client with mocked Tor client."""
    # Patch tor_client before importing app
    with patch("core.tor_client.tor_client") as mock_tor:
        mock_tor.test_connection.return_value = True
        mock_tor.create_session.return_value = MagicMock()

        # Re-import to pick up patched config/tor
        import importlib
        import main as main_module
        importlib.reload(main_module)

        with TestClient(main_module.app) as tc:
            yield tc


# ── Health ──────────────────────────────────────────────────────────


class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "tor_connected" in data
        assert "timestamp" in data

    def test_root(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        data = resp.json()
        assert "endpoints" in data
        assert "jobs" in data["endpoints"]


# ── Search (job submission) ─────────────────────────────────────────


class TestSearch:
    def test_submit_search_returns_202(self, client):
        resp = client.post(
            "/api/v1/search",
            json={"term": "test"},
        )
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "queued"
        assert data["poll_url"].startswith("/api/v1/jobs/")

    def test_submit_raw_json_without_content_type_returns_202(self, client):
        resp = client.post(
            "/api/v1/search",
            content='{"term":"test","max_results":5}',
        )
        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "queued"

    def test_submit_with_client_id(self, client):
        resp = client.post(
            "/api/v1/search",
            json={"term": "test"},
            headers={"X-Client-ID": "my-client"},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # Verify client_id stored
        job_resp = client.get(f"/api/v1/jobs/{job_id}")
        assert job_resp.json()["client_id"] == "my-client"

    def test_submit_invalid_url_returns_400(self, client):
        resp = client.post(
            "/api/v1/search",
            json={"term": "test", "start_url": "http://invalid.com"},
        )
        assert resp.status_code == 422 or resp.status_code == 400

    def test_submit_no_term_returns_422(self, client):
        resp = client.post("/api/v1/search", json={})
        assert resp.status_code == 422


# ── Jobs ────────────────────────────────────────────────────────────


class TestJobs:
    def _submit(self, client, term="test", client_id=None):
        headers = {}
        if client_id:
            headers["X-Client-ID"] = client_id
        resp = client.post("/api/v1/search", json={"term": term}, headers=headers)
        return resp.json()["job_id"]

    def test_get_job(self, client):
        job_id = self._submit(client)
        resp = client.get(f"/api/v1/jobs/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == job_id
        assert data["request"]["term"] == "test"

    def test_get_nonexistent_job_404(self, client):
        resp = client.get("/api/v1/jobs/does_not_exist")
        assert resp.status_code == 404

    def test_list_jobs(self, client):
        self._submit(client, "term1")
        self._submit(client, "term2")
        resp = client.get("/api/v1/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 2
        assert len(data["jobs"]) >= 2

    def test_list_jobs_by_client(self, client):
        self._submit(client, "a", client_id="alice")
        self._submit(client, "b", client_id="bob")
        self._submit(client, "c", client_id="alice")

        resp = client.get("/api/v1/jobs", headers={"X-Client-ID": "alice"})
        data = resp.json()
        assert all(j["client_id"] == "alice" for j in data["jobs"])

    def test_cancel_queued_job(self, client):
        job_id = self._submit(client)

        # Job might still be queued — try to cancel
        resp = client.post(f"/api/v1/jobs/{job_id}/cancel")
        # Either 200 (cancelled) or 409 (already running)
        assert resp.status_code in (200, 409)

    def test_cancel_nonexistent_404(self, client):
        resp = client.post("/api/v1/jobs/nope/cancel")
        assert resp.status_code == 404

    def test_delete_nonexistent_404(self, client):
        resp = client.delete("/api/v1/jobs/nope")
        assert resp.status_code == 404

    def test_list_jobs_pagination(self, client):
        for i in range(5):
            self._submit(client, f"term{i}")

        resp = client.get("/api/v1/jobs?limit=2&offset=0")
        data = resp.json()
        assert data["limit"] == 2
        assert len(data["jobs"]) <= 2

    def test_list_jobs_filter_status(self, client):
        self._submit(client)
        resp = client.get("/api/v1/jobs?status=queued")
        assert resp.status_code == 200
