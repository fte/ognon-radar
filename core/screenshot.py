"""
Screenshot capture via Playwright + Tor SOCKS5 proxy.
Uses the synchronous API to stay compatible with the threaded job executor.
"""
import logging
from pathlib import Path
from types import TracebackType
from typing import Optional

logger = logging.getLogger(__name__)

_PROXY = {"server": "socks5://tor:9050"}
_LAUNCH_ARGS = ["--disable-dev-shm-usage"]


class ScreenshotSession:
    """
    Context manager that keeps one Chromium browser alive for a batch of
    screenshots, avoiding per-URL browser startup cost.

    Each call to take() reuses the same browser but opens a fresh page so
    prior navigation state cannot leak between URLs.
    """

    def __init__(self, timeout_ms: int = 15000) -> None:
        self.timeout_ms = timeout_ms
        self._pw = None
        self._browser = None

    def __enter__(self) -> "ScreenshotSession":
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(proxy=_PROXY, args=_LAUNCH_ARGS)
        return self

    def __exit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()

    def take(self, url: str, output_path: Path) -> bool:
        """Navigate to url and save a viewport screenshot. Returns True on success."""
        if self._browser is None:
            raise RuntimeError("ScreenshotSession must be used as a context manager")
        try:
            page = self._browser.new_page()
            try:
                page.goto(url, timeout=self.timeout_ms, wait_until="domcontentloaded")
                page.screenshot(path=str(output_path), full_page=False)
            finally:
                page.close()
            return True
        except Exception as exc:
            logger.warning(f"Screenshot failed for {url}: {exc}")
            return False


def take_screenshot(url: str, output_path: Path, timeout_ms: int = 15000) -> bool:
    """
    Single-URL convenience wrapper. Launches and closes a browser for one shot.
    Use ScreenshotSession for batches.
    """
    with ScreenshotSession(timeout_ms=timeout_ms) as session:
        return session.take(url, output_path)
