"""
Web crawler for .onion sites with BFS algorithm.
Extracts links, searches for terms, and manages crawl state.
"""
import logging
import re
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait as futures_wait
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, parse_qs, unquote, quote as url_quote
from datetime import datetime, timezone
from bs4 import BeautifulSoup

from config import settings
from core.constants import BLACKLIST_PATHS, ONION_URL_REGEX
from core.tor_client import TorClient

logger = logging.getLogger(__name__)


def is_valid_onion_url(url: str) -> bool:
    """
    Validate if URL is a proper Tor v3 .onion address.
    
    Args:
        url: URL to validate
        
    Returns:
        True if valid .onion URL, False otherwise
    """
    return bool(ONION_URL_REGEX.match(url))


_REDIRECT_PARAMS = ('uddg', 'url', 'u', 'goto', 'redirect', 'redirect_url')


def extract_onion_links(base_url: str, soup: BeautifulSoup) -> Set[str]:
    links = set()

    for anchor in soup.find_all('a', href=True):
        href = anchor['href'].strip()
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)

        # Unwrap search-engine redirect links (e.g. DDG /l/?uddg=ENCODED_URL)
        qs = parse_qs(parsed.query)
        redirect_target = next(
            (unquote(qs[p][0]) for p in _REDIRECT_PARAMS if p in qs), None
        )

        if redirect_target:
            if is_valid_onion_url(redirect_target):
                rp = urlparse(redirect_target)
                links.add(f"{rp.scheme}://{rp.netloc}{rp.path}")
        elif is_valid_onion_url(full_url):
            links.add(f"{parsed.scheme}://{parsed.netloc}{parsed.path}")

    return links


# Search path mappings for known .onion search engines.
# When a seed URL is one of these hosts (at root, no path/query),
# effective_start_url() builds a search query URL automatically.
# NOTE: hostnames here overlap with seed_urls in config.yaml by design —
# config defines *which* seeds to use; this dict defines *how* to query them.
_SEARCH_ENGINES = {
    "juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion": "/search/?q=",      # Ahmia
    "xmh57jrknzkhv6y3ls3ubitzfqnkrwxhopf5aygthi7d6rplyvk3noyd.onion": "/cgi-bin/omega/omega?P=",  # Torch
    "tordexpmg4xy32rfp4ovnz7zq5ujoejwq2u26uxxtkscgo5u3losmeid.onion": "/search?q=",      # TorDex
    "haystak5njsmn2hqkewecpaxetahtwhsbsa64jom2k22z5afxhnpxfid.onion": "/search?q=",      # Haystak
    "notevil2ebbr5xjww6nryjta7bycbriyi2vh7an3wcuovlznvobykmad.onion": "/search?q=",      # Not Evil
    "duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion": "/html/?q=",        # DuckDuckGo
}


_AHMIA_HOST = "juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion"


def effective_start_url(start_url: str, term: str) -> str:
    """For known search engines at root, construct the search query URL."""
    parsed = urlparse(start_url)
    search_path = _SEARCH_ENGINES.get(parsed.netloc.lower())
    if search_path and not parsed.path.strip('/') and not parsed.query:
        return f"{parsed.scheme}://{parsed.netloc}{search_path}{url_quote(term)}"
    return start_url


def resolve_search_url(start_url: str, term: str, tor_client: "TorClient") -> str:
    """Like effective_start_url but handles engines that require a CSRF token.

    For Ahmia, the search results page requires a hidden form token that
    changes per session. We fetch the homepage once to extract it, then
    include it in the search URL.
    """
    parsed = urlparse(start_url)
    netloc = parsed.netloc.lower()

    if netloc != _AHMIA_HOST or parsed.path.strip('/') or parsed.query:
        return effective_start_url(start_url, term)

    base = f"{parsed.scheme}://{parsed.netloc}"
    try:
        resp = tor_client.get_with_retries(base + "/", timeout=30)
        soup = BeautifulSoup(resp.text, 'lxml')
        hidden = {i['name']: i['value'] for i in soup.select('input[type=hidden]')}
        search_path = _SEARCH_ENGINES[_AHMIA_HOST]
        extra = "".join(f"&{k}={url_quote(v)}" for k, v in hidden.items())
        return f"{base}{search_path}{url_quote(term)}{extra}"
    except Exception as e:
        logger.warning(f"Could not fetch Ahmia CSRF token: {e} — falling back to bare URL")
        return effective_start_url(start_url, term)


def _parse_ahmia_serp(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Extract result entries from Ahmia's search results page.

    Ahmia wraps every result link in a redirect:
      /search/redirect?search_term=...&redirect_url=http://xxx.onion/...
    We extract the real .onion URL from the redirect_url query param
    """
    entries = []
    for anchor in soup.select('h4 > a, li.result h4 > a'):
        href = anchor.get('href', '')
        if 'redirect_url=' not in href:
            continue
        qs = parse_qs(urlparse(href).query)
        target = qs.get('redirect_url', [None])[0]
        if target and is_valid_onion_url(target):
            # Snippet is in the sibling <p> of the enclosing <li>
            li = anchor.find_parent('li')
            snippet = li.get_text(' ', strip=True) if li else ''
            entries.append({
                'url': target,
                'title': anchor.get_text(strip=True) or target,
                'snippet': snippet[:300],
            })
    return entries


def _parse_torch_serp(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Extract results from Torch's results page."""
    entries = []
    for anchor in soup.select('a'):
        href = anchor.get('href', '')
        if is_valid_onion_url(href):
            entries.append({
                'url': href,
                'title': anchor.get_text(strip=True) or href,
                'snippet': '',
            })
    return entries


def _parse_tordex_serp(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Extract results from TorDex's results page."""
    entries = []
    for anchor in soup.select('a'):
        href = anchor.get('href', '')
        if is_valid_onion_url(href):
            entries.append({
                'url': href,
                'title': anchor.get_text(strip=True) or href,
                'snippet': '',
            })
    return entries


def _parse_haystak_serp(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Extract results from Haystak's results page."""
    entries = []
    for anchor in soup.select('a'):
        href = anchor.get('href', '')
        if is_valid_onion_url(href):
            entries.append({
                'url': href,
                'title': anchor.get_text(strip=True) or href,
                'snippet': '',
            })
    return entries


def _parse_notevil_serp(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Extract results from Not Evil's results page."""
    entries = []
    for anchor in soup.select('a'):
        href = anchor.get('href', '')
        if is_valid_onion_url(href):
            entries.append({
                'url': href,
                'title': anchor.get_text(strip=True) or href,
                'snippet': '',
            })
    return entries


def _parse_ddg_serp(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Extract results from DuckDuckGo's .onion HTML endpoint."""
    entries = []
    for anchor in soup.select('a.result__a'):
        href = anchor.get('href', '')
        qs = parse_qs(urlparse(href).query)
        target = qs.get('uddg', [None])[0]
        if target and is_valid_onion_url(target):
            snippet_el = anchor.find_next('a', class_='result__snippet')
            entries.append({
                'url': target,
                'title': anchor.get_text(strip=True) or target,
                'snippet': snippet_el.get_text(' ', strip=True) if snippet_el else '',
            })
    return entries


_SERP_PARSERS: Dict[str, Callable[[BeautifulSoup], List[Dict[str, str]]]] = {
    "juhanurmihxlp77nkq76byazcldy2hlmovfu2epvl5ankdibsot4csyd.onion": _parse_ahmia_serp,
    "xmh57jrknzkhv6y3ls3ubitzfqnkrwxhopf5aygthi7d6rplyvk3noyd.onion": _parse_torch_serp,
    "tordexpmg4xy32rfp4ovnz7zq5ujoejwq2u26uxxtkscgo5u3losmeid.onion": _parse_tordex_serp,
    "haystak5njsmn2hqkewecpaxetahtwhsbsa64jom2k22z5afxhnpxfid.onion": _parse_haystak_serp,
    "notevil2ebbr5xjww6nryjta7bycbriyi2vh7an3wcuovlznvobykmad.onion": _parse_notevil_serp,
    "duckduckgogg42xjoc72x3sjasowoarfbgcmvfimaftt6twagswzczad.onion": _parse_ddg_serp,
}


def extract_text_content(soup: BeautifulSoup) -> str:
    for element in soup(['script', 'style', 'meta', 'link']):
        element.decompose()
    return re.sub(r'\s+', ' ', soup.get_text(separator=' ', strip=True))


def search_term_in_text(text: str, term: str) -> Tuple[int, str]:
    """Return (occurrence_count, snippet). Count=0 means not found."""
    text_lower = text.lower()
    count = text_lower.count(term.lower())
    if count == 0:
        return 0, ""
    index = text_lower.find(term.lower())
    start = max(0, index - 100)
    end = min(len(text), index + len(term) + 100)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return count, snippet


class OnionCrawler:
    """BFS crawler for .onion sites with search functionality."""
    
    def __init__(self, tor_client: TorClient):
        """
        Initialize crawler with Tor client.

        Args:
            tor_client: Configured TorClient instance
        """
        self.tor_client = tor_client
    
    def scrape_page(self, url: str, timeout: int) -> Optional[Tuple[str, str, BeautifulSoup]]:
        """
        Scrape a single .onion page.
        
        Args:
            url: URL to scrape
            timeout: Request timeout in seconds
            
        Returns:
            Tuple of (title, text, soup) or None if failed
        """
        # Check blacklist
        parsed = urlparse(url)
        if any(parsed.path.startswith(path) for path in BLACKLIST_PATHS):
            logger.info(f"Skipping blacklisted path: {url}")
            return None
        
        try:
            logger.info(f"Fetching: {url}")
            response = self.tor_client.get_with_retries(url, timeout=timeout)
            
            soup = BeautifulSoup(response.text, 'lxml')
            
            # Extract title
            title = soup.title.string.strip() if soup.title and soup.title.string else "No Title"
            
            # Extract text
            text = extract_text_content(soup)
            
            return title, text, soup
            
        except Exception as e:
            logger.error(f"Failed to scrape {url}: {e}")
            return None

    def _probe_serp_reachability(
        self,
        entries: List[Dict[str, str]],
    ) -> Tuple[Dict[int, bool], List[int]]:
        """Probe SERP entries for reachability under a strict wall-clock budget.

        A ThreadPoolExecutor fans out check_reachable probes; results are
        consumed as they complete via FIRST_COMPLETED so we never wait on the
        slowest probe once the budget is gone.

        When the budget expires:
          - queued (not-yet-started) probes are cancelled;
          - in-flight probes cannot be interrupted from the caller side, so we
            abandon them and move on (pool.shutdown(wait=False) — the worker
            threads finish on their own in the background).

        "skipped" must only ever count probes that actually ran and came back
        unreachable — cancelled or abandoned futures are reported separately
        as unprobed, otherwise the metric overstates real measurements.

        Returns:
            (verdicts, unprobed_indices) where:
              verdicts:        {entry_index: reachable_bool} for every entry
                               whose probe actually completed;
              unprobed_indices: sorted indices never probed (budget expiry).
                               Callers decide whether to surface or drop them.
        """
        if not entries:
            return {}, []

        budget_s = max(0.1, float(getattr(settings, 'serp_probe_budget', 6.0)))
        connect_timeout = max(
            1.0, float(getattr(settings, 'serp_probe_connect_timeout', 5.0))
        )
        max_workers = max(1, int(getattr(settings, 'serp_probe_max_workers', 5)))

        deadline = time.monotonic() + budget_s
        verdicts: Dict[int, bool] = {}

        pool = ThreadPoolExecutor(max_workers=min(max_workers, len(entries)))
        futures = {
            pool.submit(
                self.tor_client.check_reachable,
                entry['url'],
                connect_timeout=connect_timeout,
            ): idx
            for idx, entry in enumerate(entries)
        }

        pending = set(futures)
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, pending = futures_wait(
                pending,
                timeout=remaining,
                return_when=FIRST_COMPLETED,
            )
            for fut in done:
                idx = futures[fut]
                try:
                    verdicts[idx] = fut.result()
                except Exception:
                    verdicts[idx] = False  # check_reachable swallows its own errors; belt & braces

        unprobed_indices: List[int] = []
        if pending:
            # Cancel what never started; in-flight probes are abandoned (the
            # workers finish on their own in the background). Neither category
            # produced a verdict, so BOTH count as unprobed and are surfaced
            # unfiltered — dropping them would silently empty the SERP when
            # Tor is slow.
            cancelled = sum(1 for f in pending if f.cancel())
            unprobed_indices = sorted(futures[f] for f in pending)
            logger.warning(
                f"SERP probe budget ({budget_s:.1f}s) expired: "
                f"{len(unprobed_indices)}/{len(futures)} reachability checks "
                f"without verdict ({len(pending) - cancelled} abandoned "
                f"in flight, {cancelled} cancelled before start)"
            )

        pool.shutdown(wait=False)
        return verdicts, unprobed_indices

    def crawl_and_search(
        self,
        start_url: str,
        search_term: str,
        max_depth: int,
        max_pages: int,
        max_results: int,
        timeout: int,
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> Tuple[List[dict], int]:
        """
        Crawl .onion sites using BFS and search for term.

        Returns:
            Tuple of (results_list, total_crawled_pages)
        """
        crawled_urls: Set[str] = set()
        results: List[dict] = []

        queue: deque = deque([(start_url, 0)])

        while queue and len(crawled_urls) < max_pages and len(results) < max_results:
            current_url, depth = queue.popleft()

            if current_url in crawled_urls or depth > max_depth:
                continue

            scraped = self.scrape_page(current_url, timeout)

            if scraped:
                title, text, soup = scraped
                crawled_urls.add(current_url)

                netloc = urlparse(current_url).netloc.lower()
                serp_parser = _SERP_PARSERS.get(netloc)

                if serp_parser:
                    # On a search engine page: extract SERP entries directly
                    ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                    entries = serp_parser(soup)

                    # Filter out unreachable .onion sites under a per-page budget
                    verdicts, unprobed_idx = self._probe_serp_reachability(entries)
                    probed = len(verdicts)
                    skipped = sum(1 for up in verdicts.values() if not up)

                    if skipped or unprobed_idx:
                        logger.info(
                            f"SERP: skipped {skipped}/{probed} probed-unreachable, "
                            f"{len(unprobed_idx)} unprobed (surfaced unfiltered) "
                            f"from {current_url}"
                        )

                    # Surface order: probed-and-reachable first (SERP order),
                    # then unprobed ones. Unprobed entries are kept (not
                    # dropped): an expired budget says nothing about them, and
                    # dropping would silently empty results when Tor is slow.
                    surfaced = [
                        entries[idx] for idx in sorted(verdicts) if verdicts[idx]
                    ]
                    surfaced += [entries[idx] for idx in unprobed_idx]

                    for entry in surfaced:
                        results.append({
                            'url': entry['url'],
                            'title': entry['title'],
                            'snippet': entry['snippet'],
                            'timestamp': ts,
                            'seed': start_url,
                            'depth': depth + 1,
                            'term_count': 1,
                        })
                        if len(results) >= max_results:
                            break
                    logger.info(f"SERP: extracted {len(results)} results from {current_url}")
                else:
                    # Normal page: search for term in text
                    count, snippet = search_term_in_text(text, search_term)
                    if count:
                        results.append({
                            'url': current_url,
                            'title': title,
                            'snippet': snippet,
                            'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                            'seed': start_url,
                            'depth': depth,
                            'term_count': count,
                        })
                        logger.info(f"Found '{search_term}' in {current_url} ({count} times)")

                if depth < max_depth:
                    links = extract_onion_links(current_url, soup)
                    for link in links:
                        if link not in crawled_urls:
                            queue.append((link, depth + 1))

                logger.info(f"Crawled: {len(crawled_urls)} pages | Found: {len(results)} results")
                if progress_cb:
                    progress_cb(len(crawled_urls), len(results))
                time.sleep(settings.crawl_delay)

        return results, len(crawled_urls)
