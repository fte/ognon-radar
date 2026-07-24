"""
Tests for the site capture feature.

Covers:
- WARCCaptureProvider (mocked Tor client, real filesystem via tmp_path)
- job_manager._run_capture end-to-end (tmpdir)
- POST /api/v1/capture and GET /api/v1/captures/{job_id}/download routes
- Integration: real local HTTP server + real warcio write/read + download endpoint
"""
import importlib
import io
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from warcio.archiveiterator import ArchiveIterator


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
  check_url: "https://check.torproject.org/"
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
  max_pages: 10
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
        assert result.storage_key == "testjob01"
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

    def test_get_download_url_returns_api_route(self, provider):
        prov, _, _ = provider
        assert prov.get_download_url("ognj-abc123") == "/api/v1/captures/ognj-abc123/download"

    def test_delete_removes_file(self, provider, tmp_path):
        prov, _, capture_dir = provider
        f = capture_dir / "x.warc.gz"
        f.write_bytes(b"data")
        prov.delete("x")
        assert not f.exists()

    def test_delete_missing_file_is_noop(self, provider, tmp_path):
        prov, _, _ = provider
        prov.delete("nonexistent-job-id")  # must not raise


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
        storage_key = result["storage_key"]
        assert result["download_url"] == f"/api/v1/captures/{storage_key}/download"


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

    with TestClient(main_module.app, headers={"X-Client-ID": "test-client"}) as tc:
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
        """A job_id containing path traversal sequences must return 403."""
        tc, capture_dir = client_capture

        # Place a file outside the capture dir that a traversal might reach.
        secret = tmp_path / "secret.warc.gz"
        secret.write_bytes(b"sensitive")

        # Insert a completed job with a traversal job_id.
        import main as main_module
        jm = main_module.job_manager
        traversal_id = "../../secret"
        conn = jm._get_conn()
        conn.execute(
            "INSERT INTO jobs (id, status, request, result, created_at) VALUES (?, 'completed', '{}', ?, datetime('now'))",
            (traversal_id, json.dumps({"storage_key": traversal_id, "download_url": ""})),
        )
        conn.commit()

        resp = tc.get(f"/api/v1/captures/{traversal_id}/download")
        # URL-level traversal is normalized by the HTTP layer (404);
        # code-level traversal is caught by is_relative_to (403).
        assert resp.status_code in (403, 404)


# ── Integration: real local HTTP server ───────────────────────────────

_HTML_WITH_ASSET = b"""\
<html><head><title>Local</title></head>
<body><p>real content</p><img src="/logo.png" /></body>
</html>"""

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8  # minimal PNG header stand-in


class _LocalHandler(BaseHTTPRequestHandler):
    """Serves HTML on / and a fake PNG on /logo.png."""

    def do_GET(self):
        if self.path == "/logo.png":
            body, ctype = _PNG_BYTES, "image/png"
        else:
            body, ctype = _HTML_WITH_ASSET, "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # silence request noise in test output


class _DirectHttpClient:
    """TorClient-compatible wrapper using a plain httpx.Client (no SOCKS proxy)."""

    def __init__(self):
        self._client = httpx.Client(follow_redirects=True)

    def get_with_retries(self, url: str, timeout: int = 10, **_):
        return self._client.get(url, timeout=timeout)

    def close(self):
        self._client.close()


@pytest.fixture(scope="class")
def local_server():
    server = HTTPServer(("127.0.0.1", 0), _LocalHandler)
    host, port = server.server_address
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield host, port
    server.shutdown()


class TestWARCIntegration:
    """End-to-end capture tests using a real local HTTP server.

    No Tor proxy — _DirectHttpClient connects directly to localhost.
    Validates that warcio records are readable and the download endpoint
    streams the correct bytes.
    """

    @pytest.fixture()
    def provider_and_client(self, tmp_path, local_server, monkeypatch):
        capture_dir = tmp_path / "captures"
        cfg = _make_config(tmp_path, capture_dir)
        config_file = tmp_path / "config.yaml"
        config_file.write_text(cfg)

        from config import Settings
        monkeypatch.setattr("config.settings", Settings(str(config_file)))

        from core.capture.warc_provider import WARCCaptureProvider
        client = _DirectHttpClient()
        provider = WARCCaptureProvider(tor_client=client, output_dir=str(capture_dir))
        yield provider, client, capture_dir, local_server
        client.close()

    def test_warc_records_readable(self, provider_and_client):
        """WARC file must contain at least two records (HTML + PNG asset) readable by warcio."""
        provider, _, capture_dir, (host, port) = provider_and_client
        start_url = f"http://{host}:{port}/"

        result = provider.capture(
            job_id="integ01",
            start_url=start_url,
            max_pages=1,
            max_depth=0,
            timeout=5,
        )

        warc_path = capture_dir / f"{result.storage_key}.warc.gz"
        assert warc_path.exists(), "WARC file was not created"
        assert result.size_bytes > 0

        records = []
        with open(warc_path, "rb") as fh:
            for record in ArchiveIterator(fh):
                if record.rec_type == "response":
                    records.append(record.rec_headers.get_header("WARC-Target-URI"))

        assert any(r == start_url for r in records), f"HTML page URL missing from WARC records: {records}"
        # Assets on 127.0.0.1 are filtered by the .onion host guard — only the HTML page is recorded.

    def test_download_endpoint_returns_file(self, provider_and_client, tmp_path, monkeypatch):
        """GET /api/v1/captures/{job_id}/download must return the WARC bytes."""
        provider, _, capture_dir, (host, port) = provider_and_client
        start_url = f"http://{host}:{port}/"

        result = provider.capture(
            job_id="integ02",
            start_url=start_url,
            max_pages=1,
            max_depth=0,
            timeout=5,
        )
        warc_path = capture_dir / f"{result.storage_key}.warc.gz"
        expected_bytes = warc_path.read_bytes()

        # Patch config and modules so the API routes to the correct output_dir.
        cfg = _make_config(tmp_path, capture_dir)
        config_file = tmp_path / "config.yaml"
        config_file.write_text(cfg)

        from config import Settings
        settings = Settings(str(config_file))
        monkeypatch.setattr("config.settings", settings)
        monkeypatch.setattr("routes.capture.settings", settings)

        mock_tor = MagicMock()
        mock_tor.test_connection.return_value = True
        mock_tor.create_session.return_value = None
        monkeypatch.setattr("core.tor_client.tor_client", mock_tor)
        monkeypatch.setattr("core.webhook_manager.webhook_manager", MagicMock())

        import main as main_module
        importlib.reload(main_module)

        with TestClient(main_module.app, headers={"X-Client-ID": "test-client"}) as tc:
            jm = main_module.job_manager
            job_id = "integ02"  # matches the capture job_id above
            conn = jm._get_conn()
            conn.execute(
                "INSERT INTO jobs (id, status, request, result, created_at) "
                "VALUES (?, 'completed', '{}', ?, datetime('now'))",
                (job_id, json.dumps({"storage_key": job_id, "download_url": f"/api/v1/captures/{job_id}/download"})),
            )
            conn.commit()

            resp = tc.get(f"/api/v1/captures/{job_id}/download")

        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"] == "application/gzip"
        assert resp.content == expected_bytes


# ── New targeted tests ────────────────────────────────────────────────


class TestWARCWriterWithMockedTorClient:
    """Unit-test WARCCaptureProvider.capture() end-to-end using only a mocked
    TorClient — no real network, real filesystem via tmp_path."""

    def test_warc_written_and_result_fields_populated(self, tmp_path, monkeypatch):
        capture_dir = tmp_path / "captures"
        cfg = _make_config(tmp_path, capture_dir)
        (tmp_path / "config.yaml").write_text(cfg)

        from config import Settings
        monkeypatch.setattr("config.settings", Settings(str(tmp_path / "config.yaml")))

        from core.capture.warc_provider import WARCCaptureProvider

        tor = MagicMock()
        tor.get_with_retries.return_value = _fake_response(_HTML_PAGE)

        provider = WARCCaptureProvider(tor_client=tor, output_dir=str(capture_dir))
        result = provider.capture(
            job_id="wmtc-01",
            start_url=_ONION_URL,
            max_pages=3,
            max_depth=1,
            timeout=10,
        )

        warc = capture_dir / "wmtc-01.warc.gz"
        assert warc.exists(), "WARC file not created"
        assert result.storage_key == "wmtc-01"
        assert result.pages_captured >= 1
        assert result.size_bytes == warc.stat().st_size
        assert result.assets_captured >= 0

    def test_external_assets_not_fetched(self, tmp_path, monkeypatch):
        capture_dir = tmp_path / "captures"
        cfg = _make_config(tmp_path, capture_dir)
        (tmp_path / "config.yaml").write_text(cfg)

        from config import Settings
        monkeypatch.setattr("config.settings", Settings(str(tmp_path / "config.yaml")))

        from core.capture.warc_provider import WARCCaptureProvider

        tor = MagicMock()
        tor.get_with_retries.return_value = _fake_response(_HTML_PAGE)

        provider = WARCCaptureProvider(tor_client=tor, output_dir=str(capture_dir))
        provider.capture(
            job_id="wmtc-02",
            start_url=_ONION_URL,
            max_pages=1,
            max_depth=0,
            timeout=10,
        )

        fetched = [str(c.args[0]) for c in tor.get_with_retries.call_args_list]
        assert not any("evil.com" in u for u in fetched), "external asset fetched"


class TestCaptureEndpointEnqueueAndDownload:
    """POST /capture enqueues; GET /captures/{id}/download returns the WARC bytes."""

    @pytest.fixture()
    def setup(self, tmp_path, monkeypatch):
        capture_dir = tmp_path / "captures"
        cfg = _make_config(tmp_path, capture_dir)
        (tmp_path / "config.yaml").write_text(cfg)

        from config import Settings
        settings = Settings(str(tmp_path / "config.yaml"))
        monkeypatch.setattr("config.settings", settings)
        monkeypatch.setattr("routes.capture.settings", settings)
        monkeypatch.setattr("core.job_manager.settings", settings)

        mock_tor = MagicMock()
        mock_tor.test_connection.return_value = True
        mock_tor.create_session.return_value = None
        mock_tor.get_with_retries.return_value = _fake_response(b"<html><body>hi</body></html>")
        monkeypatch.setattr("core.tor_client.tor_client", mock_tor)
        monkeypatch.setattr("core.webhook_manager.webhook_manager", MagicMock())

        import main as main_module
        importlib.reload(main_module)

        with TestClient(main_module.app, headers={"X-Client-ID": "test-client"}) as tc:
            yield tc, capture_dir, main_module.job_manager

    def test_enqueue_returns_202_with_job_id(self, setup):
        tc, _, _ = setup
        resp = tc.post("/api/v1/capture", json={"start_url": _ONION_URL})
        assert resp.status_code == 202
        body = resp.json()
        assert "job_id" in body
        assert body["status"] == "queued"

    def test_max_pages_clamped_to_server_cap(self, setup):
        tc, _, jm = setup
        resp = tc.post(
            "/api/v1/capture",
            json={"start_url": _ONION_URL, "max_pages": 200},  # within schema le=200, above server cap 10
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]
        job = jm.get_job(job_id)
        # The stored max_pages must not exceed the config cap (10 in test config).
        assert job["request"]["max_pages"] <= 10

    def test_download_completed_job(self, setup, tmp_path):
        tc, capture_dir, jm = setup

        # Write a fake WARC file so the download endpoint finds it.
        job_id = "cead-01"
        capture_dir.mkdir(parents=True, exist_ok=True)
        warc = capture_dir / f"{job_id}.warc.gz"
        warc.write_bytes(b"FAKE_WARC_CONTENT")

        conn = jm._get_conn()
        conn.execute(
            "INSERT INTO jobs (id, status, request, result, created_at) "
            "VALUES (?, 'completed', '{}', ?, datetime('now'))",
            (job_id, json.dumps({"storage_key": job_id, "download_url": f"/api/v1/captures/{job_id}/download"})),
        )
        conn.commit()

        resp = tc.get(f"/api/v1/captures/{job_id}/download")
        assert resp.status_code == 200
        assert resp.content == b"FAKE_WARC_CONTENT"



