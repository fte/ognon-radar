"""
Web crawler for .onion sites with BFS algorithm.
Extracts links, searches for terms, and manages crawl state.
"""
import logging
import re
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse, parse_qs, unquote, quote as url_quote
from datetime import datetime, timezone
from bs4 import BeautifulSoup

from config import settings
from core.constants import ONION_URL_REGEX
from core.tor_client import TorClient

logger = logging.getLogger(__name__)

BLACKLIST_PATHS = {
    '/register', '/signup', '/login', '/logout',
    '/register.php', '/login.php', '/signup.php'
}


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
    "xmh57jrknzkhv6y3ls3ubitzfqnkrwxhopf5aygthi7d6rplyvk3noyd.onion": "/search?query=",  # Torch
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
    and deduplicate by netloc so each unique site appears only once.
    """
    entries = []
    seen_netlocs: Set[str] = set()
    for item in soup.select('li.result'):
        a = item.select_one('h4 a')
        if not a:
            continue
        href = a.get('href', '').strip()

        # Extract the real .onion URL from the redirect query param
        parsed_href = urlparse(href)
        qs = parse_qs(parsed_href.query)
        raw_url = qs.get('redirect_url', [None])[0]
        if not raw_url:
            # Fallback: maybe the href itself is a direct .onion link
            raw_url = href
        url = unquote(raw_url)
        if not is_valid_onion_url(url):
            continue

        # Deduplicate by netloc so each unique site appears once
        netloc = urlparse(url).netloc.lower()
        if netloc in seen_netlocs:
            continue
        seen_netlocs.add(netloc)

        title = a.get_text(strip=True) or "No Title"
        desc_el = item.select_one('p')
        snippet = desc_el.get_text(strip=True) if desc_el else ""
        entries.append({'url': url, 'title': title, 'snippet': snippet})
    return entries


def _parse_torch_serp(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Extract results from Torch search engine.

    Torch renders results as <dt> (title link) + <dd> (snippet) pairs,
    or as divs with class 'result'. Links point directly to .onion URLs.
    """
    entries = []
    seen_netlocs: Set[str] = set()

    # Try <dt>/<dd> pattern first
    for dt in soup.select('dt'):
        a = dt.select_one('a[href]')
        if not a:
            continue
        url = a.get('href', '').strip()
        if not is_valid_onion_url(url):
            continue
        netloc = urlparse(url).netloc.lower()
        if netloc in seen_netlocs:
            continue
        seen_netlocs.add(netloc)
        title = a.get_text(strip=True) or "No Title"
        dd = dt.find_next_sibling('dd')
        snippet = dd.get_text(strip=True) if dd else ""
        entries.append({'url': url, 'title': title, 'snippet': snippet})

    if entries:
        return entries

    # Fallback: generic result containers
    return _parse_generic_serp(soup)


def _parse_tordex_serp(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Extract results from TorDex search engine.

    TorDex uses <div class="result-content"> with <a class="title"> and
    <div class="description">.
    """
    entries = []
    seen_netlocs: Set[str] = set()

    for item in soup.select('.result-content, .result, .search-result'):
        a = item.select_one('a.title, h4 a, h3 a, a[href]')
        if not a:
            continue
        url = _unwrap_redirect_href(a.get('href', '').strip())
        if not is_valid_onion_url(url):
            continue
        netloc = urlparse(url).netloc.lower()
        if netloc in seen_netlocs:
            continue
        seen_netlocs.add(netloc)
        title = a.get_text(strip=True) or "No Title"
        desc_el = item.select_one('.description, p, .snippet')
        snippet = desc_el.get_text(strip=True) if desc_el else ""
        entries.append({'url': url, 'title': title, 'snippet': snippet})

    if entries:
        return entries
    return _parse_generic_serp(soup)


def _parse_haystak_serp(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Extract results from Haystak search engine.

    Haystak renders results as <div class="result"> blocks with
    a title link and description paragraph.
    """
    entries = []
    seen_netlocs: Set[str] = set()

    for item in soup.select('.result, .search-result, article'):
        a = item.select_one('a[href]')
        if not a:
            continue
        url = _unwrap_redirect_href(a.get('href', '').strip())
        if not is_valid_onion_url(url):
            continue
        netloc = urlparse(url).netloc.lower()
        if netloc in seen_netlocs:
            continue
        seen_netlocs.add(netloc)
        title = a.get_text(strip=True) or "No Title"
        desc_el = item.select_one('p, .description, .snippet')
        snippet = desc_el.get_text(strip=True) if desc_el else ""
        entries.append({'url': url, 'title': title, 'snippet': snippet})

    if entries:
        return entries
    return _parse_generic_serp(soup)


def _parse_notevil_serp(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Extract results from Not Evil search engine.

    Not Evil uses a simple list of results with direct .onion links.
    """
    entries = []
    seen_netlocs: Set[str] = set()

    for item in soup.select('.result, li, div.search-result'):
        a = item.select_one('a[href]')
        if not a:
            continue
        url = _unwrap_redirect_href(a.get('href', '').strip())
        if not is_valid_onion_url(url):
            continue
        netloc = urlparse(url).netloc.lower()
        if netloc in seen_netlocs:
            continue
        seen_netlocs.add(netloc)
        title = a.get_text(strip=True) or "No Title"
        # Try to find a sibling or child snippet
        desc_el = item.select_one('p, .description, .snippet, span')
        snippet = desc_el.get_text(strip=True) if desc_el else ""
        entries.append({'url': url, 'title': title, 'snippet': snippet})

    return entries


def _parse_ddg_serp(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Extract results from DuckDuckGo HTML-only endpoint (/html/?q=).

    DDG wraps result links in redirect URLs with ?uddg= parameter.
    Results are in <div class="result"> with <a class="result__a">.
    """
    entries = []
    seen_netlocs: Set[str] = set()

    for item in soup.select('.result, .results_links'):
        a = item.select_one('a.result__a, a.result__url, h2 a, a[href]')
        if not a:
            continue
        href = a.get('href', '').strip()
        url = _unwrap_redirect_href(href)
        if not is_valid_onion_url(url):
            continue
        netloc = urlparse(url).netloc.lower()
        if netloc in seen_netlocs:
            continue
        seen_netlocs.add(netloc)
        title = a.get_text(strip=True) or "No Title"
        desc_el = item.select_one('.result__snippet, .snippet, p')
        snippet = desc_el.get_text(strip=True) if desc_el else ""
        entries.append({'url': url, 'title': title, 'snippet': snippet})

    if entries:
        return entries
    return _parse_generic_serp(soup)


def _unwrap_redirect_href(href: str) -> str:
    """Extract real .onion URL from a search engine redirect href."""
    parsed = urlparse(href)
    qs = parse_qs(parsed.query)
    for param in _REDIRECT_PARAMS:
        if param in qs:
            return unquote(qs[param][0])
    return href


def _parse_generic_serp(soup: BeautifulSoup) -> List[Dict[str, str]]:
    """Fallback: extract all unique .onion links from the page as results."""
    entries = []
    seen_netlocs: Set[str] = set()
    for a in soup.select('a[href]'):
        href = a.get('href', '').strip()
        url = _unwrap_redirect_href(href)
        if not is_valid_onion_url(url):
            continue
        netloc = urlparse(url).netloc.lower()
        if netloc in seen_netlocs:
            continue
        seen_netlocs.add(netloc)
        title = a.get_text(strip=True) or "No Title"
        entries.append({'url': url, 'title': title, 'snippet': ''})
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
    
    def crawl_and_search(
        self,
        start_url: str,
        search_term: str,
        max_depth: int,
        max_pages: int,
        max_results: int,
        timeout: int
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
                    for entry in serp_parser(soup):
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
                time.sleep(settings.crawl_delay)

        return results, len(crawled_urls)
