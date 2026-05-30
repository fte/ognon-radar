"""
WARC capture provider — writes full site archives to local .warc.gz files.

Each HTTP response (pages + assets) is recorded as a WARC 'response' record.
Swap this provider for an R2/B2 one without changing the API layer.
"""
import logging
import os
import time
from collections import deque
from pathlib import Path
from typing import Set
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from warcio.warcwriter import WARCWriter
from warcio.statusandheaders import StatusAndHeaders

from config import settings
from core.capture.base import CaptureProvider, CaptureResult
from core.crawler import is_valid_onion_url, BLACKLIST_PATHS
from core.tor_client import TorClient

logger = logging.getLogger(__name__)

_ASSET_SELECTORS = [
    ("img", "src"),
    ("link", "href"),
    ("script", "src"),
    ("source", "src"),
]


class WARCCaptureProvider(CaptureProvider):
    """Captures .onion sites to a local .warc.gz via the Tor proxy."""

    def __init__(self, tor_client: TorClient, output_dir: str):
        self._tor = tor_client
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)

    # ── public interface ────────────────────────────────────────────

    def capture(
        self,
        job_id: str,
        start_url: str,
        max_pages: int,
        max_depth: int,
        timeout: int,
    ) -> CaptureResult:
        dest = self._output_dir / f"{job_id}.warc.gz"
        pages = 0
        assets = 0

        with open(dest, "wb") as fh:
            writer = WARCWriter(fh, gzip=True)
            visited: Set[str] = set()
            queue: deque = deque([(start_url, 0)])

            while queue and pages < max_pages:
                url, depth = queue.popleft()
                if url in visited or depth > max_depth:
                    continue

                parsed = urlparse(url)
                if any(parsed.path.startswith(p) for p in BLACKLIST_PATHS):
                    continue

                try:
                    response = self._tor.get_with_retries(url, timeout=timeout)
                except Exception as exc:
                    logger.warning(f"Capture: failed to fetch {url}: {exc}")
                    continue

                visited.add(url)
                self._write_record(writer, url, response)
                pages += 1
                logger.info(f"Capture: page {pages}/{max_pages} — {url}")

                if "text/html" in response.headers.get("Content-Type", ""):
                    soup = BeautifulSoup(response.text, "lxml")
                    assets += self._capture_assets(writer, url, soup, timeout)

                    if depth < max_depth:
                        for link in self._extract_links(url, soup):
                            if link not in visited:
                                queue.append((link, depth + 1))

                time.sleep(settings.crawl_delay)

        size = dest.stat().st_size
        return CaptureResult(
            job_id=job_id,
            url=start_url,
            pages_captured=pages,
            assets_captured=assets,
            size_bytes=size,
            storage_key=str(dest),
        )

    def get_download_url(self, storage_key: str) -> str:
        return storage_key  # local path; served by the API via FileResponse

    def delete(self, storage_key: str) -> None:
        path = Path(storage_key)
        if path.exists():
            path.unlink()

    # ── private helpers ─────────────────────────────────────────────

    def _write_record(self, writer: WARCWriter, url: str, response) -> None:
        status_line = f"HTTP/1.1 {response.status_code} {response.reason}"
        headers = [(k, v) for k, v in response.headers.items()]
        http_headers = StatusAndHeaders(status_line, headers, protocol="HTTP/1.1")
        payload = response.content
        record = writer.create_warc_record(
            url,
            "response",
            payload=__import__("io").BytesIO(payload),
            length=len(payload),
            http_headers=http_headers,
        )
        writer.write_record(record)

    def _capture_assets(
        self, writer: WARCWriter, base_url: str, soup: BeautifulSoup, timeout: int
    ) -> int:
        captured = 0
        seen: Set[str] = set()
        for tag, attr in _ASSET_SELECTORS:
            for el in soup.find_all(tag, **{attr: True}):
                raw = el[attr].strip()
                if not raw or raw.startswith("data:"):
                    continue
                asset_url = urljoin(base_url, raw)
                if asset_url in seen:
                    continue
                seen.add(asset_url)
                parsed = urlparse(asset_url)
                if parsed.scheme not in ("http", "https"):
                    continue
                # Only fetch assets on the same .onion host
                if not asset_url.endswith(".onion") and ".onion" not in parsed.netloc:
                    continue
                try:
                    resp = self._tor.get_with_retries(asset_url, timeout=timeout)
                    self._write_record(writer, asset_url, resp)
                    captured += 1
                except Exception as exc:
                    logger.debug(f"Capture: asset skip {asset_url}: {exc}")
        return captured

    def _extract_links(self, base_url: str, soup: BeautifulSoup) -> Set[str]:
        links: Set[str] = set()
        for a in soup.find_all("a", href=True):
            full = urljoin(base_url, a["href"].strip())
            p = urlparse(full)
            if is_valid_onion_url(full):
                links.add(f"{p.scheme}://{p.netloc}{p.path}")
        return links
