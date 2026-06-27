"""
Unit tests for OnionCrawler, WebhookManager, and JobManager.

These tests run without Docker or a live Tor connection.
They use tmp_path-backed SQLite and mock HTTP calls.
"""
import json
import time
from unittest.mock import MagicMock, patch, call

import httpx
import pytest
from bs4 import BeautifulSoup


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture()
def _patch_config(tmp_path, monkeypatch):
    config_content = """
api:
  title: "Unit Test API"
  version: "0.0.1"
  description: "Test"
  host: "127.0.0.1"
  port: 8000
tor:
  proxy: "socks5h://127.0.0.1:9050"
  check_url: "http://check.torproject.org/"
crawling:
  delay: 0
  max_depth: 2
  max_pages: 10
  max_results: 5
  timeout: 10
  seed_urls:
    - "http://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion"
retry:
  count: 2
  backoff_factor: 0
security:
  user_agent: "TestBot/1.0"
  api_key: ""
cors:
  origins: []
jobs:
  max_workers: 1
  db_path: "{db_path}"
webhook:
  db_path: "{webhook_db_path}"
  max_attempts: 3
  retry_delay: 0
  timeout: 5
  allow_insecure_urls: true
""".format(
        db_path=str(tmp_path / "test_jobs.db"),
        webhook_db_path=str(tmp_path / "test_webhooks.db"),
    )

    config_file = tmp_path / "config.yaml"
    config_file.write_text(config_content)

    from config import Settings
    monkeypatch.setattr("config.settings", Settings(str(config_file)))


@pytest.fixture()
def job_manager(_patch_config, tmp_path):
    from config import Settings
    settings = Settings(str(tmp_path / "config.yaml"))
    from core.job_manager import JobManager
    jm = JobManager(max_workers=1, db_path=settings.job_db_path)
    jm.startup()
    yield jm
    jm.shutdown()


@pytest.fixture()
def webhook_manager(_patch_config, tmp_path):
    from config import Settings
    settings = Settings(str(tmp_path / "config.yaml"))
    from core.webhook_manager import WebhookManager
    wm = WebhookManager(settings.webhook_db_path)
    wm.startup()
    yield wm


# ── OnionCrawler ─────────────────────────────────────────────────────


class TestOnionCrawler:
    def _make_crawler(self):
        from core.crawler import OnionCrawler
        tor = MagicMock()
        return OnionCrawler(tor), tor

    def _make_response(self, html: str) -> MagicMock:
        resp = MagicMock()
        resp.text = html
        resp.raise_for_status = MagicMock()
        return resp

    def test_crawl_finds_term_on_single_page(self, _patch_config):
        crawler, tor = self._make_crawler()
        html = "<html><head><title>Test Page</title></head><body>secret keyword found here</body></html>"
        tor.get_with_retries.return_value = self._make_response(html)

        results, crawled = crawler.crawl_and_search(
            start_url="http://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion",
            search_term="secret keyword",
            max_depth=1,
            max_pages=5,
            max_results=3,
            timeout=10,
        )

        assert len(results) == 1
        assert results[0]["url"].endswith(".onion")
        assert "secret keyword" in results[0]["snippet"].lower()
        assert crawled == 1

    def test_crawl_returns_empty_when_term_absent(self, _patch_config):
        crawler, tor = self._make_crawler()
        html = "<html><head><title>Nothing</title></head><body>no match here</body></html>"
        tor.get_with_retries.return_value = self._make_response(html)

        results, crawled = crawler.crawl_and_search(
            start_url="http://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion",
            search_term="completely absent phrase",
            max_depth=1,
            max_pages=5,
            max_results=3,
            timeout=10,
        )

        assert results == []
        assert crawled == 1

    def test_scrape_page_skips_blacklisted_path(self, _patch_config):
        crawler, tor = self._make_crawler()

        result = crawler.scrape_page(
            "http://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion/login",
            timeout=10,
        )

        assert result is None
        tor.get_with_retries.assert_not_called()

    def test_scrape_page_returns_none_on_network_error(self, _patch_config):
        crawler, tor = self._make_crawler()
        tor.get_with_retries.side_effect = httpx.RequestError("timeout")

        result = crawler.scrape_page(
            "http://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion/page",
            timeout=10,
        )

        assert result is None

    def test_crawl_respects_max_results(self, _patch_config):
        crawler, _ = self._make_crawler()
        # scrape_page returns a page that matches the term on every call
        with patch.object(crawler, "scrape_page") as mock_scrape:
            mock_scrape.return_value = (
                "Title",
                "target term appears here",
                BeautifulSoup("<html></html>", "lxml"),
            )
            results, _ = crawler.crawl_and_search(
                start_url="http://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion",
                search_term="target term",
                max_depth=1,
                max_pages=10,
                max_results=2,
                timeout=10,
            )

        assert len(results) <= 2


# ── WebhookManager ───────────────────────────────────────────────────


class TestWebhookManager:
    def _setup_config(self, wm, url="http://localhost:9999/hook"):
        wm.set_webhook_config(
            client_id="client-a",
            url=url,
            events=["job.completed", "job.failed"],
            secret="test-secret",
            active=True,
        )

    def _job_data(self):
        return {
            "created_at": "2026-01-01T00:00:00Z",
            "completed_at": "2026-01-01T00:01:00Z",
            "request": {"term": "test", "start_url": "http://x.onion"},
        }

    def test_send_webhook_success(self, webhook_manager):
        self._setup_config(webhook_manager)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.post.return_value = mock_resp

            result = webhook_manager.send_webhook(
                job_id="job-1",
                client_id="client-a",
                status="completed",
                job_data=self._job_data(),
            )

        assert result is True

    def test_send_webhook_retries_on_failure_then_succeeds(self, webhook_manager):
        self._setup_config(webhook_manager)

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            # Fail once, then succeed
            instance.post.side_effect = [
                httpx.RequestError("connection refused"),
                ok_resp,
            ]

            result = webhook_manager.send_webhook(
                job_id="job-2",
                client_id="client-a",
                status="completed",
                job_data=self._job_data(),
            )

        assert result is True
        assert instance.post.call_count == 2

    def test_send_webhook_fails_after_max_attempts(self, webhook_manager):
        self._setup_config(webhook_manager)

        with patch("httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.post.side_effect = httpx.RequestError("connection refused")

            result = webhook_manager.send_webhook(
                job_id="job-3",
                client_id="client-a",
                status="completed",
                job_data=self._job_data(),
            )

        assert result is False

    def test_send_webhook_no_config_returns_false(self, webhook_manager):
        result = webhook_manager.send_webhook(
            job_id="job-4",
            client_id="no-config-client",
            status="completed",
            job_data=self._job_data(),
        )
        assert result is False

    def test_send_webhook_records_delivery_attempts(self, webhook_manager):
        self._setup_config(webhook_manager)

        with patch("httpx.Client") as MockClient:
            instance = MockClient.return_value.__enter__.return_value
            instance.post.side_effect = httpx.RequestError("fail")

            webhook_manager.send_webhook(
                job_id="job-5",
                client_id="client-a",
                status="failed",
                job_data=self._job_data(),
            )

        deliveries, total = webhook_manager.get_delivery_attempts(job_id="job-5")
        assert total == 1
        assert deliveries[0]["status"] == "failed"


# ── JobManager ───────────────────────────────────────────────────────


class TestJobManager:
    def _request(self):
        return {
            "term": "test",
            "start_url": "http://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion",
        }

    def test_submit_creates_queued_job(self, job_manager):
        with patch.object(job_manager, "_submit_to_pool"):
            job_id = job_manager.submit_job(self._request(), client_id="c1")

        job = job_manager.get_job(job_id)
        assert job is not None
        assert job["status"] == "queued"
        assert job["client_id"] == "c1"

    def test_get_job_returns_none_for_unknown_id(self, job_manager):
        assert job_manager.get_job("nonexistent-id") is None

    def test_cancel_queued_job(self, job_manager):
        with patch.object(job_manager, "_submit_to_pool"):
            job_id = job_manager.submit_job(self._request(), client_id="c1")

        cancelled = job_manager.cancel_job(job_id)

        assert cancelled is True
        assert job_manager.get_job(job_id)["status"] == "cancelled"

    def test_cancel_running_job_returns_false(self, job_manager):
        with patch.object(job_manager, "_submit_to_pool"):
            job_id = job_manager.submit_job(self._request(), client_id="c1")

        # Manually advance status to running
        conn = job_manager._get_conn()
        conn.execute("UPDATE jobs SET status = 'running' WHERE id = ?", (job_id,))
        conn.commit()

        cancelled = job_manager.cancel_job(job_id)
        assert cancelled is False

    def test_delete_completed_job(self, job_manager):
        with patch.object(job_manager, "_submit_to_pool"):
            job_id = job_manager.submit_job(self._request(), client_id="c1")

        conn = job_manager._get_conn()
        conn.execute("UPDATE jobs SET status = 'completed' WHERE id = ?", (job_id,))
        conn.commit()

        deleted = job_manager.delete_job(job_id)
        assert deleted is True
        assert job_manager.get_job(job_id) is None

    def test_delete_queued_job_not_allowed(self, job_manager):
        with patch.object(job_manager, "_submit_to_pool"):
            job_id = job_manager.submit_job(self._request(), client_id="c1")

        deleted = job_manager.delete_job(job_id)
        assert deleted is False

    def test_list_jobs_filtered_by_client(self, job_manager):
        with patch.object(job_manager, "_submit_to_pool"):
            job_manager.submit_job(self._request(), client_id="client-x")
            job_manager.submit_job(self._request(), client_id="client-y")

        jobs, total = job_manager.list_jobs(client_id="client-x")
        assert total == 1
        assert jobs[0]["client_id"] == "client-x"

    def test_startup_resets_orphaned_running_jobs(self, tmp_path, _patch_config):
        from config import Settings
        settings = Settings(str(tmp_path / "config.yaml"))
        from core.job_manager import JobManager

        jm = JobManager(max_workers=1, db_path=settings.job_db_path)
        jm.startup()

        # Insert a job stuck in 'running' (simulates crash)
        conn = jm._get_conn()
        conn.execute(
            "INSERT INTO jobs (id, client_id, status, request, created_at) VALUES (?, ?, ?, ?, ?)",
            ("orphan-1", "c1", "running", "{}", "2026-01-01T00:00:00Z"),
        )
        conn.commit()
        jm.shutdown()

        # Startup should reset it to 'failed'
        jm2 = JobManager(max_workers=1, db_path=settings.job_db_path)
        jm2.startup()
        job = jm2.get_job("orphan-1")
        assert job["status"] == "failed"
        jm2.shutdown()
