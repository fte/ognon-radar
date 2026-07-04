"""
Screenshot capture via Playwright + Tor SOCKS5 proxy.
Uses the synchronous API to stay compatible with the threaded job executor.
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def take_screenshot(url: str, output_path: Path, timeout_ms: int = 15000) -> bool:
    """
    Navigate to url via Tor and save a viewport screenshot to output_path.
    Returns True on success, False on any error (timeout, connection, etc.).
    """
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                proxy={"server": "socks5://tor:9050"},
                args=["--disable-dev-shm-usage"],
            )
            page = browser.new_page()
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            page.screenshot(path=str(output_path), full_page=False)
            browser.close()
        return True
    except Exception as exc:
        logger.warning(f"Screenshot failed for {url}: {exc}")
        return False
