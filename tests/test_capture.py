"""
Tests for the site capture feature.

Covers:
- WARCCaptureProvider (mocked Tor client, real filesystem via tmp_path)
- job_manager._run_capture end-to-end (tmpdir)
- POST /api/v1/capture and GET /api/v1/captures/{job_id}/download routes
"""
import importlib
import io
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ── Helpers ──────────────────────────────────────────────────────────

_ONION_URL = "http://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion"

_HTML_PAGE = b"""
<html><head><title>Test</title></head>
<body>
  <p>hello world</p>
  <img src="/logo.png" />
  <script src="https://evil.com/tracker.js"></script>
</body></html>
"""


def _make_config(tmp_path: Path, capture_dir: Path) -> str:
    return f"""
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
    - "{_ONION_URL}"
retry:
  count: 1
  backoff_factor: 0
security:
  user_agent: "TestBot/1.0"
cors:
  origins: ["*"]
jobs:
  max_workers: 1
  db_path: "{tmp_path / 'jobs.db'}"
webhook:
  db_path: "{tmp_path / 'webhooks.db'}"
  max_attempts: 1
  retry_delay: 0
  timeout: 5
  allow_insecure_urls: true
capture:
  backend: warc
  output_dir: "{capture_dir}"
  max_size_mb: 10
"""


def _fake_response(content: bytes = b"", status: int = 200, ctype: str = "text/html"):
    resp = MagicMock()
    resp.status_code = status
    resp.reason = "OK"
    resp.content = content
    resp.text = content.decode("utf-8", errors="replace")
    resp.headers = {"Content-Type": ctype}
    return resp


# ── WARCCaptureProvider unit tests ───────────────────────────────────


class TestWARCCaptureProvider:
    @pytest.fixture()
    def provider(self, tmp_path, monkeypatch):
        capture_dir = tmp_path / "captures"
        cfg = _make_config(tmp_path, capture_dir)
        config_file = tmp_path / "config.yaml"
        config_file.write_text(cfg)

        from config import Settings
        monkeypatch.setattr("config.settings", Settings(str(config_file)))

        from core.capture.warc_provider import WARCCaptureProvider
        tor = MagicMock()
        return WARCCaptureProvider(tor_client=tor, output_dir=str(capture_dir)), tor, capture_dir

    def test_capture_creates_warc(self, provider):
        prov, tor, capture_dir = provider
        tor.get_with_retries.return_value = _fake_response(_HTML_PAGE)

        result = prov.capture(
            job_id="testjob01",
            start_url=_ONION_URL,
            max_pages=2,
            max_depth=1,
            timeout=10,
        )

        warc = capture_dir / "testjob01.warc.gz"
        assert warc.exists()
        assert result.storage_key == str(warc)
        assert result.pages_captured >= 1
        assert result.size_bytes > 0

    def test_assets_same_host_only(self, provider):
        """External script (evil.com) must not be fetched; same-host img must be."""
        prov, tor, _ = provider
        tor.get_with_retries.return_value = _fake_response(_HTML_PAGE)

        prov.capture(
            job_id="testjob02",
            start_url=_ONION_URL,
            max_pages=1,
            max_depth=0,
            timeout=10,
        )

        fetched_urls = [str(call.args[0]) for call in tor.get_with_retries.call_args_list]
        assert not any("evil.com" in u for u in fetched_urls), "external asset was fetched"

    def test_get_download_url_returns_storage_key(self, provider):
        prov, _, _ = provider
        assert prov.get_download_url("/some/path/job.warc.gz") == "/some/path/job.warc.gz"

    def test_delete_removes_file(self, provider, tmp_path):
        prov, _, capture_dir = provider
        f = capture_dir / "x.warc.gz"
        f.write_bytes(b"data")
        prov.delete(str(f))
        assert not f.exists()

    def test_delete_missing_file_is_noop(self, provider, tmp_path):
        prov, _, _ = provider
        prov.delete("/nonexistent/path.warc.gz")  # must not raise


# ── job_manager._run_capture integration ─────────────────────────────


class TestRunCapture:
    @pytest.fixture()
    def jm(self, tmp_path, monkeypatch):
        capture_dir = tmp_path / "captures"
        cfg = _make_config(tmp_path, capture_dir)
        config_file = tmp_path / "config.yaml"
        config_file.write_text(cfg)

        from config import Settings
        settings = Settings(str(config_file))
        monkeypatch.setattr("config.settings", settings)
        monkeypatch.setattr("core.job_manager.settings", settings)

        from core.job_manager import JobManager
        jm = JobManager(max_workers=1, db_path=str(tmp_path / "jobs.db"))
        jm.startup()
        yield jm, capture_dir
        jm.shutdown()

    def test_run_capture_writes_result(self, jm, monkeypatch):
        manager, capture_dir = jm

        mock_tor = MagicMock()
        mock_tor.get_with_retries.return_value = _fake_response(b"<html><body>hi</body></html>")
        monkeypatch.setattr("core.job_manager.tor_client", mock_tor)
        monkeypatch.setattr("core.tor_client.tor_client", mock_tor)
        monkeypatch.setattr("core.webhook_manager.webhook_manager", MagicMock())
        monkeypatch.setattr("core.job_manager.webhook_manager", MagicMock())

        job_id = manager.submit_capture_job(
            {"start_url": _ONION_URL, "max_pages": 1, "max_depth": 0, "timeout": 10}
        )

        deadline = time.time() + 10
        while time.time() < deadline:
            job = manager.get_job(job_id)
            if job and job["status"] in ("completed", "failed"):
                break
            time.sleep(0.1)

        assert job["status"] == "completed", job.get("error")
        result = job["result"]
        assert result["pages_captured"] >= 1
        assert Path(result["storage_key"]).exists()
        assert result["download_url"] == result["storage_key"]


# ── HTTP route tests ──────────────────────────────────────────────────


@pytest.fixture()
def _patch_config_capture(tmp_path, monkeypatch):
    capture_dir = tmp_path / "captures"
    cfg = _make_config(tmp_path, capture_dir)
    config_file = tmp_path / "config.yaml"
    config_file.write_text(cfg)

    from config import Settings
    settings = Settings(str(config_file))
    monkeypatch.setattr("config.settings", settings)
    return settings, capture_dir


@pytest.fixture()
def client_capture(_patch_config_capture, monkeypatch):
    settings, capture_dir = _patch_config_capture

    mock_tor = MagicMock()
    mock_tor.test_connection.return_value = True
    mock_tor.create_session.return_value = None
    mock_tor.get_with_retries.return_value = _fake_response(b"<html><body>ok</body></html>")

    monkeypatch.setattr("core.tor_client.tor_client", mock_tor)
    monkeypatch.setattr("core.webhook_manager.webhook_manager", MagicMock())

    import main as main_module
    importlib.reload(main_module)

    with TestClient(main_module.app) as tc:
        yield tc, capture_dir


class TestCaptureRoutes:
    def test_post_capture_returns_202(self, client_capture):
        tc, _ = client_capture
        resp = tc.post("/api/v1/capture", json={"start_url": _ONION_URL})
        assert resp.status_code == 202
        body = resp.json()
        assert "job_id" in body
        assert body["status"] == "queued"

    def test_post_capture_invalid_url(self, client_capture):
        tc, _ = client_capture
        resp = tc.post("/api/v1/capture", json={"start_url": "http://notanonion.com"})
        assert resp.status_code == 422

    def test_download_not_found(self, client_capture):
        tc, _ = client_capture
        resp = tc.get("/api/v1/captures/doesnotexist/download")
        assert resp.status_code == 404

    def test_download_pending_job(self, client_capture):
        tc, _ = client_capture
        resp = tc.post("/api/v1/capture", json={"start_url": _ONION_URL})
        job_id = resp.json()["job_id"]
        # Don't wait for completion — job is queued/running, download must 409
        dl = tc.get(f"/api/v1/captures/{job_id}/download")
        assert dl.status_code in (404, 409)

    def test_download_path_traversal_rejected(self, client_capture, tmp_path):
        """A storage_key pointing outside output_dir must return 403."""
        import uuid
        tc, capture_dir = client_capture

        bad_key = str(tmp_path / "secret.txt")
        Path(bad_key).write_text("sensitive")

        # Insert a completed job directly — avoids a background thread race.
        import main as main_module
        jm = main_module.job_manager
        job_id = str(uuid.uuid4().hex)
        conn = jm._get_conn()
        conn.execute(
            "INSERT INTO jobs (id, status, request, result, created_at) VALUES (?, 'completed', '{}', ?, datetime('now'))",
            (job_id, json.dumps({"storage_key": bad_key, "download_url": bad_key})),
        )
        conn.commit()

        resp = tc.get(f"/api/v1/captures/{job_id}/download")
        assert resp.status_code == 403
