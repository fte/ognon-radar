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

_LAUNCH_ARGS = [
    "--disable-dev-shm-usage",
    "--no-sandbox",               # Required in containers — Chrome sandbox needs kernel support absent in containers
    "--disable-setuid-sandbox",    # Companion to --no-sandbox (disables the setuid sandbox helper)
]


def _resolve_proxy_url() -> str:
    """Proxy URL Chromium should use, resolved at launch time.

    Priority:
      1. ``TOR_PROXY`` env var — set by scripts/container to tor's real IP
         (Apple's vmnet network does not resolve container names).
      2. ``settings.tor_proxy`` from the active config — ``socks5h://tor:9050``
         under Docker/dev, ``socks5h://127.0.0.1:9050`` on the native VPS
         deployment (config.live.yaml).

    Chromium only understands the ``socks5://`` scheme, so ``socks5h://`` is
    normalized — same SOCKSv5 protocol, and Chrome always resolves names on
    the proxy side anyway.  Resolution happens at call time (not import
    time) so a patched ``config.settings`` is honoured in tests.

    History: this used to be a module-level constant defaulting to
    ``socks5://tor:9050``.  On the native VPS deployment nothing sets
    ``TOR_PROXY`` and the ``tor`` hostname does not resolve, so every
    screenshot job died at ``BrowserType.launch`` with a navigation failure
    while the reachability probe (httpx via settings.tor_proxy) succeeded.
    """
    url = os.getenv("TOR_PROXY", "")
    if not url:
        from config import settings  # local import: tests monkeypatch it

        url = settings.tor_proxy
    if url.startswith("socks5h://"):
        url = "socks5://" + url[len("socks5h://"):]
    return url


async def _launch_browser(pw):
    return await pw.chromium.launch(proxy={"server": _resolve_proxy_url()}, args=_LAUNCH_ARGS)


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

    try:
        return asyncio.run(run())
    except Exception as exc:
        # _take swallows navigation errors (returns False); anything raising
        # here is a launch/cleanup failure.  Re-raise with the underlying
        # message so the job error shows the real cause (e.g. missing system
        # library, unreachable proxy) instead of a bare "Screenshot failed".
        logger.warning(f"Screenshot failed for {url}: {exc}")
        raise RuntimeError(f"Screenshot failed for {url}: {exc}") from exc