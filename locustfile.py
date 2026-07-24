"""
Locust load-test scenarios for the DarkWeb API.

Usage:
    docker-compose --profile testing up          # Web UI at http://localhost:8089
    docker-compose run --rm locust -f /mnt/locust/locustfile.py \
        --headless -u 10 -r 2 --run-time 30s     # Headless (no CSV)
    docker-compose run --rm locust -f /mnt/locust/locustfile.py \
        --headless -u 10 -r 2 --run-time 30s --csv=/mnt/locust/results  # +CSV
"""
import logging

from locust import HttpUser, constant, task

logging.getLogger("locust").setLevel(logging.WARNING)

ONION_URL = "http://duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion"
CLIENT_ID = "loadtest-client"


class RateLimitUser(HttpUser):
    """Simulates a real user moving through the API at a fixed pace.

    Each virtual user runs 1 request every ~650ms → ~92 req/min/user.
    With 10 users: ~15 req/s total, which is enough to trigger rate
    limits on tight endpoints (search: 10/min, capture: 5/min).
    429 responses are tracked as successes (not failures) so locust's
    built-in statistics reflect throughput, not rate-limit alerts.
    """

    wait_time = constant(0.65)
    host = "http://api:8000"

    def on_start(self):
        self.search_job_id = None
        self._headers = {"X-Client-ID": CLIENT_ID}

    # ── High-weight: endpoints called frequently ────────────────────

    @task(50)
    def health(self):
        """Health check: exempt from rate limiting, expect always 200."""
        with self.client.get(
            "/api/v1/health",
            catch_response=True,
            name="GET /health",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Expected 200, got {resp.status_code}")

    @task(20)
    def list_jobs(self):
        """List jobs for the test client. 30/min limit."""
        with self.client.get(
            "/api/v1/jobs",
            headers=self._headers,
            catch_response=True,
            name="GET /jobs",
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            elif resp.status_code == 429:
                resp.success()  # Rate limited — expected under load
            else:
                resp.failure(f"Unexpected: {resp.status_code}")

    @task(10)
    def get_job_detail(self):
        """Get a specific job if we have one. 30/min limit."""
        if not self.search_job_id:
            self.search_job_id = self._create_search_job()
            if not self.search_job_id:
                return
        with self.client.get(
            f"/api/v1/jobs/{self.search_job_id}",
            headers=self._headers,
            catch_response=True,
            name="GET /jobs/{id}",
        ) as resp:
            if resp.status_code in (200, 404, 429):
                resp.success()
            else:
                resp.failure(f"Unexpected: {resp.status_code}")

    @task(8)
    def submit_search(self):
        """Submit a new search. 10/min + 100/hour limit."""
        self.search_job_id = self._create_search_job()

    def _create_search_job(self):
        with self.client.post(
            "/api/v1/search",
            json={"term": "loadtest", "max_results": 3, "max_pages": 5},
            headers={"Content-Type": "application/json", **self._headers},
            catch_response=True,
            name="POST /search",
        ) as resp:
            if resp.status_code == 202:
                data = resp.json()
                return data.get("job_id")
            elif resp.status_code == 429:
                resp.success()  # Expected under load
            else:
                resp.failure(f"Expected 202/429, got {resp.status_code}")
            return None

    # ── Lower-weight: heavy endpoints ──────────────────────────────

    @task(5)
    def submit_capture(self):
        """Submit a capture. 5/min + 30/hour — most restrictive."""
        with self.client.post(
            "/api/v1/capture",
            json={"start_url": ONION_URL, "max_pages": 1, "max_depth": 0, "timeout": 10},
            headers={"Content-Type": "application/json", **self._headers},
            catch_response=True,
            name="POST /capture",
        ) as resp:
            if resp.status_code == 202:
                resp.success()
            elif resp.status_code == 429:
                resp.success()  # Expected
            else:
                resp.failure(f"Expected 202/429, got {resp.status_code}")

    @task(5)
    def submit_screenshot(self):
        """Submit a screenshot. 10/min + 60/hour limit."""
        with self.client.post(
            "/api/v1/screenshots",
            json={"start_url": ONION_URL, "timeout": 10},
            headers={"Content-Type": "application/json", **self._headers},
            catch_response=True,
            name="POST /screenshots",
        ) as resp:
            if resp.status_code in (202, 429):
                resp.success()
            else:
                resp.failure(f"Expected 202/429, got {resp.status_code}")

    @task(3)
    def create_stream_token(self):
        """Mint a stream token. 10/min limit."""
        if not self.search_job_id:
            return
        with self.client.post(
            f"/api/v1/jobs/{self.search_job_id}/stream-token",
            headers=self._headers,
            catch_response=True,
            name="POST /jobs/{id}/stream-token",
        ) as resp:
            if resp.status_code in (200, 404, 429):
                resp.success()
            else:
                resp.failure(f"Unexpected: {resp.status_code}")

    @task(3)
    def cancel_job(self):
        """Cancel a job. 10/min — mostly 409 since job is running."""
        if not self.search_job_id:
            return
        with self.client.post(
            f"/api/v1/jobs/{self.search_job_id}/cancel",
            headers=self._headers,
            catch_response=True,
            name="POST /jobs/{id}/cancel",
        ) as resp:
            if resp.status_code in (200, 409, 429):
                resp.success()
            else:
                resp.failure(f"Unexpected: {resp.status_code}")

    @task(2)
    def get_webhook_config(self):
        """Get webhook config. 20/min limit."""
        with self.client.get(
            "/api/v1/webhooks/config",
            headers=self._headers,
            catch_response=True,
            name="GET /webhooks/config",
        ) as resp:
            if resp.status_code in (200, 404, 429):
                resp.success()
            else:
                resp.failure(f"Unexpected: {resp.status_code}")

    @task(2)
    def list_deliveries(self):
        """List webhook deliveries. 20/min limit."""
        with self.client.get(
            "/api/v1/webhooks/deliveries",
            headers=self._headers,
            catch_response=True,
            name="GET /webhooks/deliveries",
        ) as resp:
            if resp.status_code in (200, 429):
                resp.success()
            else:
                resp.failure(f"Unexpected: {resp.status_code}")

    @task(2)
    def generate_api_key(self):
        """Generate a client API key. 5/min + 20/hour — very sensitive."""
        with self.client.post(
            "/api/v1/client/key",
            headers=self._headers,
            catch_response=True,
            name="POST /client/key",
        ) as resp:
            if resp.status_code in (200, 429):
                resp.success()
            else:
                resp.failure(f"Unexpected: {resp.status_code}")

    @task(2)
    def delete_jobs(self):
        """Delete jobs. 10/min limit."""
        with self.client.delete(
            "/api/v1/jobs",
            headers=self._headers,
            catch_response=True,
            name="DELETE /jobs",
        ) as resp:
            if resp.status_code in (200, 429):
                resp.success()
            else:
                resp.failure(f"Unexpected: {resp.status_code}")
