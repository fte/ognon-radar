"""
Tests for the DarkWeb Search API.

Run with: docker-compose run --rm api pytest tests/ -v
Or locally: pytest tests/ -v

These tests mock the Tor client so they work without a running Tor container.
"""
import json
import time
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import yaml
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
  check_url: "https://check.torproject.org/"
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


# ── OpenAPI YAML ─────────────────────────────────────────────────


class TestOpenAPIYaml:
    @pytest.mark.parametrize("path", ["/openapi.yaml", "/openapi.yml"])
    def test_serves_openapi_yaml(self, client, path):
        resp = client.get(path)
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/yaml"
        data = yaml.safe_load(resp.text)
        assert data["openapi"].startswith("3.")
        assert isinstance(data["paths"], dict) and len(data["paths"]) > 0
        assert "/api/v1/search" in data["paths"]

    def test_matches_committed_file(self, client):
        from pathlib import Path
        committed = (Path(__file__).resolve().parent.parent / "openapi.yaml").read_bytes()
        assert client.get("/openapi.yml").content == committed


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
        data = resp.json()
        job_id = data["job_id"]
        assert data["client_id"] == "my-client"

        # Verify client_id stored and accessible with same client
        job_resp = client.get(f"/api/v1/jobs/{job_id}", headers={"X-Client-ID": "my-client"})
        assert job_resp.json()["client_id"] == "my-client"

    def test_submit_without_client_id_generates_one(self, client):
        resp = client.post("/api/v1/search", json={"term": "test"})
        assert resp.status_code == 202
        data = resp.json()
        assert "client_id" in data
        assert data["client_id"] != ""

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
    _HDR = {"X-Client-ID": "test-client"}

    def _submit(self, client, term="test", client_id="test-client"):
        headers = {"X-Client-ID": client_id} if client_id else {}
        resp = client.post("/api/v1/search", json={"term": term}, headers=headers)
        return resp.json()["job_id"]

    def test_get_job(self, client):
        job_id = self._submit(client)
        resp = client.get(f"/api/v1/jobs/{job_id}", headers=self._HDR)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == job_id
        assert data["request"]["term"] == "test"

    def test_get_nonexistent_job_404(self, client):
        resp = client.get("/api/v1/jobs/does_not_exist", headers=self._HDR)
        assert resp.status_code == 404

    def test_get_job_wrong_client_403(self, client):
        job_id = self._submit(client, client_id="owner")
        resp = client.get(f"/api/v1/jobs/{job_id}", headers={"X-Client-ID": "stranger"})
        assert resp.status_code == 403

    @staticmethod
    def _insert_terminal_job(
        job_id: str,
        status: str,
        error: str | None = None,
        result: dict | None = None,
        job_type: str = "search",
        client_id: str = "test-client",
    ):
        """Insert a job row directly (deterministic — skips the worker thread)."""
        from core.job_manager import job_manager

        conn = job_manager._get_conn()
        conn.execute(
            "INSERT INTO jobs (id, type, client_id, status, request, result, error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id,
                job_type,
                client_id,
                status,
                json.dumps({"term": "test", "start_url": "http://x.onion/"}),
                json.dumps(result) if result is not None else None,
                error,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()

    def test_get_failed_job_returns_422(self, client):
        self._insert_terminal_job(
            "failed-422",
            status="failed",
            error="Target unreachable via Tor: http://x.onion/",
            job_type="screenshot",
        )
        resp = client.get("/api/v1/jobs/failed-422", headers=self._HDR)
        assert resp.status_code == 422
        # The body is the full job object (same shape as a 200 response).
        data = resp.json()
        assert data["id"] == "failed-422"
        assert data["status"] == "failed"
        assert data["type"] == "screenshot"
        assert "unreachable via Tor" in data["error"]

    def test_get_completed_job_returns_200(self, client):
        self._insert_terminal_job(
            "done-200",
            status="completed",
            result={"term": "test", "results": [], "total": 0},
        )
        resp = client.get("/api/v1/jobs/done-200", headers=self._HDR)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "done-200"
        assert data["status"] == "completed"
        assert data["result"]["total"] == 0

    def test_list_jobs(self, client):
        self._submit(client, "term1")
        self._submit(client, "term2")
        resp = client.get("/api/v1/jobs", headers=self._HDR)
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
        resp = client.post(f"/api/v1/jobs/{job_id}/cancel", headers=self._HDR)
        # Either 200 (cancelled) or 409 (already running)
        assert resp.status_code in (200, 409)

    def test_cancel_nonexistent_404(self, client):
        resp = client.post("/api/v1/jobs/nope/cancel", headers=self._HDR)
        assert resp.status_code == 404

    def test_delete_nonexistent_404(self, client):
        resp = client.delete("/api/v1/jobs/nope", headers=self._HDR)
        assert resp.status_code == 404

    def test_list_jobs_pagination(self, client):
        for i in range(5):
            self._submit(client, f"term{i}")

        resp = client.get("/api/v1/jobs?limit=2&offset=0", headers=self._HDR)
        data = resp.json()
        assert data["limit"] == 2
        assert len(data["jobs"]) <= 2

    def test_list_jobs_filter_status(self, client):
        self._submit(client)
        resp = client.get("/api/v1/jobs?status=queued", headers=self._HDR)
        assert resp.status_code == 200


# ── Permalink + Export (results sharing & CSV/XLS) ─────────────


_COMPLETED_SEARCH_RESULT = {
    "term": "test",
    "results": [
        {
            "url": "http://a.onion/",
            "title": "A <b>page</b> & co",
            "snippet": "Les resultats « partagés »",
            "term_count": 2,
            "depth": 1,
            "paragraphs": ["Ligne un", "Ligne deux"],
        },
        {
            "url": "http://b.onion/",
            "title": "B",
            "snippet": "extrait",
            "term_count": 1,
            "depth": 2,
        },
    ],
    "total": 2,
    "crawled_pages": 3,
    "duration_seconds": 1.5,
    "tor_connected": True,
    "start_url": "http://x.onion/",
}


class TestJobPermalink:
    """GET /jobs/{id}/permalink — public read for completed search jobs."""

    @staticmethod
    def _fresh_id(prefix: str) -> str:
        # Job ids must be unique ACROSS tests: the job_manager singleton keeps
        # one sqlite connection for the whole pytest session, and a failed
        # INSERT (UNIQUE constraint) would poison it ("database is locked")
        # for every subsequent test.
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

    def _insert_completed_search(self, term="test"):
        job_id = self._fresh_id("ognse-done-test")
        result = dict(_COMPLETED_SEARCH_RESULT)
        result["term"] = term
        TestJobs._insert_terminal_job(
            job_id,
            status="completed",
            result=result,
            job_type="search",
            client_id="owner",
        )
        return job_id

    def test_public_read_without_credentials(self, client):
        job_id = self._insert_completed_search()
        resp = client.get(f"/api/v1/jobs/{job_id}/permalink")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == job_id
        assert data["term"] == "test"
        assert data["status"] == "completed"
        assert data["total_results"] == 2
        assert len(data["results"]) == 2
        assert data["results"][0]["url"] == "http://a.onion/"
        assert data["summary"]["total"] == 2
        assert data["summary"]["crawled_pages"] == 3

    def test_pagination(self, client):
        job_id = self._insert_completed_search()
        resp = client.get(f"/api/v1/jobs/{job_id}/permalink?offset=1&limit=1")
        data = resp.json()
        assert resp.status_code == 200
        assert data["offset"] == 1 and data["limit"] == 1
        assert [r["url"] for r in data["results"]] == ["http://b.onion/"]

    def test_non_search_job_is_private(self, client):
        job_id = self._fresh_id("ognc-capture-done")
        TestJobs._insert_terminal_job(
            job_id,
            status="completed",
            result={"pages_captured": 3},
            job_type="capture",
            client_id="owner",
        )
        resp = client.get(f"/api/v1/jobs/{job_id}/permalink")
        assert resp.status_code == 403

    def test_incomplete_search_is_private(self, client):
        job_id = self._fresh_id("ognse-queued-test")
        TestJobs._insert_terminal_job(
            job_id,
            status="queued",
            job_type="search",
            client_id="owner",
        )
        resp = client.get(f"/api/v1/jobs/{job_id}/permalink")
        assert resp.status_code == 403

    def test_owner_and_admin_still_read_private_jobs(self, client):
        job_id = self._fresh_id("ognse-queued-test")
        TestJobs._insert_terminal_job(
            job_id,
            status="queued",
            job_type="search",
            client_id="owner",
        )
        resp = client.get(
            f"/api/v1/jobs/{job_id}/permalink",
            headers={"X-Client-ID": "owner"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "queued"

    def test_stranger_cannot_read_private_job(self, client):
        job_id = self._fresh_id("ognse-queued-test")
        TestJobs._insert_terminal_job(
            job_id,
            status="queued",
            job_type="search",
            client_id="owner",
        )
        resp = client.get(
            f"/api/v1/jobs/{job_id}/permalink",
            headers={"X-Client-ID": "stranger"},
        )
        assert resp.status_code == 403

    def test_unknown_job_404(self, client):
        resp = client.get("/api/v1/jobs/does_not_exist/permalink")
        assert resp.status_code == 404


class TestJobExport:
    """GET /jobs/{id}/export?format=csv|xls — same read rule as the permalink."""

    _fresh_id = staticmethod(TestJobPermalink._fresh_id)

    def _insert_completed_search(self, term="test"):
        job_id = self._fresh_id("ognse-done-export")
        result = dict(_COMPLETED_SEARCH_RESULT)
        result["term"] = term
        TestJobs._insert_terminal_job(
            job_id,
            status="completed",
            result=result,
            job_type="search",
            client_id="owner",
        )
        return job_id

    def test_csv_export(self, client):
        job_id = self._insert_completed_search()
        resp = client.get(f"/api/v1/jobs/{job_id}/export?format=csv")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/csv")
        assert resp.headers["result-count"] == "2"
        assert "attachment" in resp.headers["content-disposition"]
        assert resp.headers["content-disposition"].endswith('.csv"')

        text = resp.content.decode("utf-8-sig")  # BOM handled
        lines = text.strip().split("\r\n")
        assert lines[0] == "#,url,title,snippet,term_count,depth,paragraphs"
        # Crawl content never breaks the CSV structure (quoted / escaped).
        assert 'A ""<b>""page"" & co"' not in text
        assert '"A <b>page</b> & co"' in text or "A <b>page</b> & co" in text
        assert lines[1].startswith("1,http://a.onion/,")
        assert 'Ligne un\nLigne deux' in text

    def test_xls_export_spreadsheetml(self, client):
        job_id = self._insert_completed_search()
        resp = client.get(f"/api/v1/jobs/{job_id}/export?format=xls")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/vnd.ms-excel")
        body = resp.content.decode("utf-8")
        assert body.startswith('<?xml version="1.0"?>')
        assert 'urn:schemas-microsoft-com:office:spreadsheet' in body
        assert '<Worksheet ss:Name="Résumé">' in body
        assert '<Worksheet ss:Name="Résultats">' in body
        # Untrusted crawl content is XML-escaped inside the sheet.
        assert "A &lt;b&gt;page&lt;/b&gt; &amp; co" in body
        assert "http://b.onion/" in body

    def test_export_without_headers_public_read(self, client):
        job_id = self._insert_completed_search()
        resp = client.get(f"/api/v1/jobs/{job_id}/export")  # no credentials
        assert resp.status_code == 200

    def test_invalid_format_422(self, client):
        job_id = self._insert_completed_search()
        resp = client.get(f"/api/v1/jobs/{job_id}/export?format=xlsx")
        assert resp.status_code == 422

    def test_capture_job_cannot_export_409(self, client):
        job_id = self._fresh_id("ognc-capture-done")
        TestJobs._insert_terminal_job(
            job_id,
            status="completed",
            result={"pages_captured": 3},
            job_type="capture",
            client_id="owner",
        )
        resp = client.get(f"/api/v1/jobs/{job_id}/export", headers={"X-Client-ID": "owner"})
        assert resp.status_code == 409

    def test_unknown_job_404(self, client):
        resp = client.get("/api/v1/jobs/does_not_exist/export")
        assert resp.status_code == 404
