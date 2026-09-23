"""
Tor client for SOCKS5 proxy communication.
Handles session creation, circuit renewal, and connectivity testing.
"""
import socket
import time
import logging
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)


class TorClient:
    """Manages Tor SOCKS5 proxy connections via httpx."""

    def __init__(self, proxy_url: Optional[str] = None):
        self.proxy_url = proxy_url or settings.tor_proxy
        self.session: Optional[httpx.Client] = None

    def _make_client(self) -> httpx.Client:
        """Build a configured httpx.Client for the Tor SOCKS5 proxy."""
        return httpx.Client(
            proxy=self.proxy_url,
            headers={
                "User-Agent": settings.user_agent,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Connection": "keep-alive",
                "DNT": "1",
                "Upgrade-Insecure-Requests": "1",
            },
            follow_redirects=True,
        )

    def create_session(self) -> httpx.Client:
        """Create a new httpx.Client configured for the Tor SOCKS5 proxy."""
        self.session = self._make_client()
        logger.info("Created new Tor session with SOCKS5 proxy")
        return self.session

    def check_reachable(
        self,
        url: str,
        connect_timeout: float = 15.0,
        read_timeout: float = 30.0,
        retries: int = 1,
    ) -> bool:
        """Quick reachability probe: True if the server sends any HTTP response.

        ProxyError means Tor failed to establish a circuit; ReadTimeoutException
        means the server accepted the connection but took longer than
        read_timeout to send its first bytes.  Onion sites are often slow-but-up:
        a fast control site answers in <5 s while a busy market can take ~20 s
        for the first byte (some take even longer), so the read budget must be
        generous (30 s default) or capturable targets get rejected.  The
        screenshot/capture stage that follows has its own larger navigation
        budget (job timeout), so the probe only needs to be permissive, not
        fast.  Any HTTP status code (200, 403, 404 …) means the server is up.

        Onion services are also flaky: a bad circuit can fail a live target, and
        a slow-but-up service can silently miss the read budget.  When the first
        probe fails with a proxy/timeout error, the Tor circuit is renewed
        (``SIGNAL NEWNYM``) and the probe is retried — ``retries`` times,
        default 1, i.e. up to two probes — before the target is declared
        unreachable.  Retries run on a dedicated throwaway client so the shared
        session, which other jobs may be using concurrently (production runs
        max_workers=2), is never closed or replaced underneath them.
        """
        if not self.session:
            self.create_session()
        timeout = httpx.Timeout(read_timeout, connect=connect_timeout)

        try:
            self.session.get(url, timeout=timeout)
            return True
        except (httpx.ProxyError, httpx.TimeoutException):
            logger.debug(f"Initial probe failed for {url} — renewing Tor circuit")
        except Exception:
            logger.debug(f"Probe failed for {url} with unexpected error")
            return False

        for attempt in range(1, retries + 1):
            self.renew_circuit()
            try:
                with self._make_client() as probe:
                    probe.get(url, timeout=timeout)
                return True
            except (httpx.ProxyError, httpx.TimeoutException):
                logger.debug(f"Retry {attempt}/{retries} failed for {url} — circuit renewed")
            except Exception:
                logger.debug(f"Retry {attempt}/{retries} failed for {url} with unexpected error")
                return False

        logger.debug(f"Target {url} unreachable after {retries + 1} probe attempts")
        return False

    def test_connection(self) -> bool:
        """Test if Tor connection is working by checking torproject.org."""
        if not self.session:
            self.create_session()

        try:
            logger.info("Testing Tor connection...")
            response = self.session.get(
                settings.tor_check_url,
                timeout=settings.default_timeout,
            )
            if "Congratulations" in response.text:
                logger.info("Tor connection successful - anonymity enabled")
                return True
            logger.warning("Tor connection test returned unexpected response")
            return False
        except httpx.RequestError as e:
            logger.error(f"Tor connection test failed: {e}")
            return False

    def get_with_retries(
        self,
        url: str,
        retries: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> httpx.Response:
        """
        HTTP GET with retry logic and exponential backoff.

        Raises:
            httpx.RequestError: If all retry attempts fail
        """
        if not self.session:
            self.create_session()

        retries = retries if retries is not None else settings.retry_count
        timeout = timeout if timeout is not None else settings.default_timeout
        last_exception: Optional[Exception] = None

        for attempt in range(1, retries + 1):
            try:
                logger.debug(f"Attempt {attempt}/{retries} for {url}")
                response = self.session.get(url, timeout=timeout)
                response.raise_for_status()
                return response
            except (httpx.RequestError, httpx.HTTPStatusError) as e:
                logger.warning(f"Attempt {attempt} failed for {url}: {e}")
                last_exception = e
                if attempt < retries:
                    sleep_time = settings.backoff_factor * (2 ** (attempt - 1))
                    logger.info(f"Retrying in {sleep_time}s...")
                    time.sleep(sleep_time)

        logger.error(f"All {retries} attempts failed for {url}")
        raise last_exception

    def renew_circuit(self) -> None:
        """Request a new Tor circuit via the control port (SIGNAL NEWNYM).

        Subsequent SOCKS5 connections will use a different exit node.
        Logs a warning on failure but never raises — jobs continue regardless.
        """
        try:
            with socket.create_connection(
                (settings.tor_control_host, settings.tor_control_port), timeout=5
            ) as ctrl:
                ctrl.sendall(
                    f'AUTHENTICATE "{settings.tor_control_password}"\r\nSIGNAL NEWNYM\r\nQUIT\r\n'.encode()
                )
                response = ctrl.recv(1024).decode(errors="replace")
            if "250 OK" in response:
                logger.info("Tor circuit renewed (SIGNAL NEWNYM)")
            else:
                logger.warning(f"Unexpected control port response: {response!r}")
        except Exception as e:
            logger.warning(f"Circuit renewal failed (continuing anyway): {e}")

    def close(self):
        """Close the session and cleanup resources."""
        if self.session:
            self.session.close()
            logger.info("Tor session closed")


# Global Tor client instance
tor_client = TorClient()
