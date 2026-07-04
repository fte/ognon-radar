"""
Tor client for SOCKS5 proxy communication.
Handles session creation, circuit renewal, and connectivity testing.
"""
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

    def create_session(self) -> httpx.Client:
        """Create a new httpx.Client configured for the Tor SOCKS5 proxy."""
        self.session = httpx.Client(
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
        logger.info("Created new Tor session with SOCKS5 proxy")
        return self.session

    def check_reachable(self, url: str, connect_timeout: float = 15.0) -> bool:
        """Quick reachability probe: True if the server sends any HTTP response.

        ProxyError means Tor failed to establish a circuit (service is down).
        TimeoutException means the connect window expired — treated as unreachable.
        Any HTTP status code (200, 403, 404 …) means the server is up.
        """
        if not self.session:
            self.create_session()
        try:
            self.session.get(url, timeout=httpx.Timeout(5.0, connect=connect_timeout))
            return True
        except httpx.ProxyError:
            logger.debug(f"Tor circuit failed for {url} — service down")
            return False
        except httpx.TimeoutException:
            logger.debug(f"Connect timeout for {url} — treating as unreachable")
            return False
        except Exception:
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

    def close(self):
        """Close the session and cleanup resources."""
        if self.session:
            self.session.close()
            logger.info("Tor session closed")


# Global Tor client instance
tor_client = TorClient()
