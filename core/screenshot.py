"""
Screenshot capture via Playwright + Tor SOCKS5 proxy.

Uses the ASYNC API (not the sync one): Playwright's sync API refuses to work
when it detects an asyncio event loop in the process and raises
"It looks like you are using Playwright Sync API inside the asyncio loop.
Please use the Async API instead." — exactly what happens under uvicorn/FastAPI
(production deployment runs on an asyncio loop).

Each job runs in a background thread of the ThreadPoolExecutor; each worker
thread here drives its OWN dedicated event loop (run_until_complete for the
batch session, asyncio.run for the single-shot wrapper), so the threaded job
executor and the rest of the sync codebase stay unchanged.
"""
import asyncio
import logging
import os
from pathlib import Path
from types import TracebackType
from typing import Optional

logger = logging.getLogger(__name__)

# TOR_PROXY is set by scripts/container / deploy to tor's real IP (the vmnet
# network does not resolve container names); default matches config.yaml's
# value and only works under Docker's embedded DNS.
_PROXY = {"server": os.getenv("TOR_PROXY", "socks5://tor:9050")}
_LAUNCH_ARGS = [
    "--disable-dev-shm-usage",
    "--no-sandbox",               # Required in containers — Chrome sandbox needs kernel support absent in containers
    "--disable-setuid-sandbox",    # Companion to --no-sandbox (disables the setuid sandbox helper)
]


async def _launch_browser(pw):
    return await pw.chromium.launch(proxy=_PROXY, args=_LAUNCH_ARGS)


async def _take(browser, url: str, output_path: Path, timeout_ms: int) -> bool:
    """Navigate to url and save a viewport screenshot. Returns True on success."""
    page = await browser.new_page()
    page.set_default_timeout(timeout_ms)
    try:
        await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        await page.screenshot(path=str(output_path), full_page=False)
        return True
    except Exception as exc:
        logger.warning(f"Screenshot failed for {url}: {exc}")
        return False
    finally:
        await page.close()


class ScreenshotSession:
    """
    Context manager that keeps one Chromium browser alive for a batch of
    screenshots, avoiding per-URL browser startup cost.

    Each call to take() reuses the same browser but opens a fresh page so
    prior navigation state cannot leak between URLs. The browser is driven on
    a dedicated event loop created for the calling (worker) thread.
    """

    def __init__(self, timeout_ms: int = 15000) -> None:
        self.timeout_ms = timeout_ms
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._pw = None
        self._browser = None

    def __enter__(self) -> "ScreenshotSession":
        from playwright.async_api import async_playwright

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        async def start():
            pw = await async_playwright().start()
            browser = await _launch_browser(pw)
            return pw, browser

        try:
            self._pw, self._browser = self._loop.run_until_complete(start())
        except BaseException:
            self._loop.close()
            self._loop = None
            raise
        return self

    def __exit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        async def stop():
            if self._browser is not None:
                await self._browser.close()
            if self._pw is not None:
                await self._pw.stop()

        if self._loop is not None:
            try:
                self._loop.run_until_complete(stop())
            except Exception as exc:  # noqa: BLE001 — cleanup must not mask the original error
                logger.warning(f"Screenshot session cleanup failed: {exc}")
            finally:
                self._loop.close()
                self._loop = None

    def take(self, url: str, output_path: Path) -> bool:
        """Navigate to url and save a viewport screenshot. Returns True on success."""
        if self._browser is None or self._loop is None:
            raise RuntimeError("ScreenshotSession must be used as a context manager")
        return self._loop.run_until_complete(
            _take(self._browser, url, output_path, self.timeout_ms)
        )


def take_screenshot(url: str, output_path: Path, timeout_ms: int = 15000) -> bool:
    """
    Single-URL convenience wrapper. Launches and closes a browser for one shot.
    """
    async def run():
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await _launch_browser(pw)
            try:
                return await _take(browser, url, output_path, timeout_ms)
            finally:
                await browser.close()

    return asyncio.run(run())