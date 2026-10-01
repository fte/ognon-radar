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
from bs4 import BeautifulSoup, NavigableString, Tag
from bisect import bisect_right

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


def extract_matching_paragraphs(soup: BeautifulSoup, term: str, max_paragraphs: int = 5) -> List[str]:
    """Extract unique block-level text fragments containing the search term.

    Walks block-level containers (p, li, td, blockquote, div…) and keeps the
    text of those that contain the term (case-insensitive). A container whose
    *descendant blocks* also match is skipped — the innermost block wins, so
    <body>/<div> wrappers never shadow the actual paragraphs they contain.
    Exact duplicate texts are deduplicated ("unique paragraphs").

    Returns plain text only — the site's HTML is never passed through, so the
    client can safely wrap matches in <mark> without XSS risk.

    Linear-time design (the naive version re-ran get_text() per block AND per
    ancestor-descendant check, i.e. O(n²) on deep pages):
      1. one normalized text buffer for the whole document, built in a single
         pass over the plain NavigableStrings (comments, script/style/CDATA
         and doctype are excluded, mirroring get_text());
      2. a [start, end) span per element into that buffer, merged bottom-up —
         an element's text IS buffer[span], no per-element copies;
      3. term occurrences located once in the buffer, then marked on every
         element whose span fully contains one (with early exit when an
         ancestor is already marked) — equivalent to the old per-element
         ``term in block_text(el)`` scan;
      4. "has a matching block descendant" recomputed bottom-up from the
         children instead of walking each element's subtree.
    """
    term_lower = term.lower()
    elements = soup.find_all(True)
    if not elements:
        return []

    def is_block(el) -> bool:
        return el.name not in _INLINE_TAGS and el.name not in ('script', 'style', 'meta', 'link')

    # Tag equality in bs4 is STRUCTURAL (two identical subtrees compare equal
    # and share the same hash), so Tags are unusable as dict/set keys — every
    # lookup would fall back to an O(subtree) __eq__. All per-element state is
    # therefore keyed by id(el); els never outlive the soup within this call.
    # ── 1. Global buffer: cleaned text of every plain string, in document
    #      order, joined by single spaces — exactly what get_text()+re.sub
    #      produced per element, but computed once for the whole document.
    parts: List[str] = []
    string_start: List[int] = []   # buffer offset where each segment begins
    string_len: List[int] = []
    string_parent: List[int] = []  # id() of the owning Tag of each segment
    pos = 0
    for s in soup.descendants:
        if type(s) is not NavigableString:
            continue  # Comment/CData/Doctype/Script/Stylesheet are not "text"
        parent = s.parent
        if not isinstance(parent, Tag):
            continue  # soup-level stray text belongs to no element
        cleaned = re.sub(r'\s+', ' ', str(s)).strip()
        if not cleaned:
            continue  # get_text(strip=True) drops it; separators collapse anyway
        if parts:
            parts.append(' ')
            pos += 1
        string_start.append(pos)
        string_len.append(len(cleaned))
        string_parent.append(id(parent))
        parts.append(cleaned)
        pos += len(cleaned)
    buffer = ''.join(parts)
    del parts

    # ── 2. Per-element spans, merged bottom-up (reverse document order means
    #      every child span is final by the time its parent is visited).
    span_start: Dict[int, int] = {}
    span_end: Dict[int, int] = {}
    own: Dict[int, List[int]] = {}  # id(el) -> indices of its direct strings
    for i, p_id in enumerate(string_parent):
        own.setdefault(p_id, []).append(i)

    for el in reversed(elements):
        el_id = id(el)
        s_min = None
        e_max = None
        for i in own.get(el_id, ()):
            if s_min is None or string_start[i] < s_min:
                s_min = string_start[i]
            e = string_start[i] + string_len[i]
            if e_max is None or e > e_max:
                e_max = e
        for child in el.children:
            if isinstance(child, Tag):
                cs = span_start.get(id(child))
                if cs is None:
                    continue
                if s_min is None or cs < s_min:
                    s_min = cs
                ce = span_end[id(child)]
                if e_max is None or ce > e_max:
                    e_max = ce
        if s_min is not None:
            span_start[el_id] = s_min
            span_end[el_id] = e_max

    # ── 3. Mark every element whose span fully contains a term occurrence
    #      (equivalent to the old ``term_lower in block_text(el).lower()``).
    #      Occurrences are found once; marking walks up from the deepest
    #      covering element and stops at the first already-marked ancestor,
    #      so total work stays linear in practice.
    match_set: Set[int] = set()
    if term_lower == '':
        # Degenerate empty term: the old code matched every non-empty block.
        match_set.update(id(el) for el in elements)
    elif buffer:
        buffer_lower = buffer.lower()
        tag_by_id: Dict[int, Tag] = {id(el): el for el in elements}
        occ = buffer_lower.find(term_lower)
        while occ != -1:
            occ_end = occ + len(term_lower)
            # Deepest element whose span contains [occ, occ_end): start from
            # the segment holding `occ` and climb while the span is too short.
            seg = bisect_right(string_start, occ) - 1
            el: Optional[Tag] = tag_by_id.get(string_parent[seg]) if seg >= 0 else None
            while el is not None and span_end.get(id(el), -1) < occ_end:
                el = el.parent
            while el is not None:
                el_id = id(el)
                if el_id in match_set:
                    break  # already marked: so are its ancestors, stop early
                if el_id in span_end:
                    match_set.add(el_id)
                el = el.parent
            occ = buffer_lower.find(term_lower, occ + 1)

    # ── 4. "A matching block descendant exists": bottom-up from the children
    #      instead of re-walking each matching element's subtree.
    has_matching_block_child: Set[int] = set()
    for el in reversed(elements):
        el_id = id(el)
        for child in el.children:
            if isinstance(child, Tag):
                c_id = id(child)
                if (c_id in match_set and is_block(child)) or c_id in has_matching_block_child:
                    has_matching_block_child.add(el_id)
                    break

    # ── 5. Same selection as before: document order, innermost block wins,
    #      dedup, cap.
    matches: List[str] = []
    seen: Set[str] = set()
    for el in elements:
        el_id = id(el)
        if el_id not in match_set or el_id in has_matching_block_child:
            continue
        if not is_block(el):
            continue
        text = buffer[span_start[el_id]:span_end[el_id]]
        if not text or text in seen:
            continue
        seen.add(text)
        matches.append(text[:1000])
        if len(matches) >= max_paragraphs:
            break

    return matches


# Inline elements are never treated as "paragraphs"; their text is covered by
# the nearest block-level ancestor, so skipping them avoids duplicates.
_INLINE_TAGS = frozenset({
    'a', 'abbr', 'b', 'bdi', 'bdo', 'big', 'br', 'button', 'cite', 'code',
    'data', 'dfn', 'em', 'font', 'i', 'img', 'input', 'kbd', 'label', 'map',
    'mark', 'output', 'q', 'samp', 'select', 'small', 'span', 'strong',
    'sub', 'sup', 'textarea', 'time', 'tt', 'u', 'var', 'wbr',
})


def search_term_in_text(text: str, term: str) -> Tuple[int, str]:
    """Return (occurrence_count, snippet). Count=0 means not found.

    The snippet is a ±100-character window around the first occurrence,
    with both edges aligned on word boundaries (a word = a maximal run of
    non-whitespace): a cut neighbour word is retracted out of the window,
    and a word that contains the term ("secret" inside "secretly") is
    shown in full rather than amputated. The term itself is never cut.
    """
    term_lower = term.lower()
    text_lower = text.lower()
    count = text_lower.count(term_lower)
    if count == 0:
        return 0, ""
    index = text_lower.find(term_lower)
    match_end = index + len(term)
    start = max(0, index - 100)
    end = min(len(text), match_end + 100)

    # Bornes du mot porteur du terme (utile quand le terme n'en est qu'un
    # fragment : "secret" dans "secretly", ou terme multi-mots).
    word_start = index
    while word_start > 0 and not text[word_start - 1].isspace():
        word_start -= 1
    word_end = match_end
    while word_end < len(text) and not text[word_end].isspace():
        word_end += 1

    # Bord gauche : si la fenêtre coupe un mot, soit c'est le mot du terme
    # (→ l'étendre jusqu'à son début), soit un voisin (→ le retirer).
    if start > 0 and not text[start - 1].isspace():
        if start > word_start:
            start = word_start
        else:
            while start < index and not text[start].isspace():
                start += 1

    # Bord droit : symétrique.
    if end < len(text) and not text[end].isspace():
        if end < word_end:
            end = word_end
        else:
            while end > match_end and not text[end - 1].isspace():
                end -= 1

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
        needed: int = 0,
    ) -> Tuple[Dict[int, bool], List[int]]:
        """Probe SERP entries for reachability under a strict wall-clock budget.

        Bounded producer/consumer: at most ``max_workers`` probes are in flight
        at any one time and new probes are submitted only while more reachable
        results are still needed. When ``needed`` reachable verdicts are in,
        submission stops entirely — the remaining entries are never scheduled,
        so no expensive Tor call is launched for work the caller no longer
        needs (the "stop as soon as max_results are found" promise).

        Two exit paths:
          * budget expiry: queued (not-yet-started) probes are cancelled and
            in-flight ones abandoned (pool.shutdown(wait=False) — the worker
            threads finish on their own in the background). Every entry without
            a verdict is returned as unprobed so the caller can surface it
            unfiltered rather than silently empty the SERP when Tor is slow.
          * early stop (``needed`` reachable found): queued probes are
            cancelled, nothing else is scheduled, and nothing is returned
            as unprobed — the caller already has enough results to fill its cap.

        "skipped" must only ever count probes that actually ran and came back
        unreachable — cancelled, abandoned, or never-scheduled entries are
        reported separately as unprobed, otherwise the metric overstates real
        measurements.

        Returns:
            (verdicts, unprobed_indices) where:
              verdicts:         {entry_index: reachable_bool} for every entry
                                whose probe actually completed;
              unprobed_indices: sorted indices never probed (only returned on
                                budget expiry). Callers decide whether to
                                surface or drop them.
        """
        if not entries or needed <= 0:
            return {}, []

        budget_s = max(0.1, float(getattr(settings, 'serp_probe_budget', 6.0)))
        connect_timeout = max(
            1.0, float(getattr(settings, 'serp_probe_connect_timeout', 5.0))
        )
        max_workers = max(1, int(getattr(settings, 'serp_probe_max_workers', 5)))
        # Never spin up more probes/threads than we could possibly need.
        max_workers = min(max_workers, len(entries), needed)

        deadline = time.monotonic() + budget_s
        verdicts: Dict[int, bool] = {}
        reachable_count = 0

        pool = ThreadPoolExecutor(max_workers=max_workers)
        futures: Dict[Any, int] = {}  # future -> entry index
        pending: Set[Any] = set()
        next_idx = 0

        def _submit_more() -> None:
            """Schedule probes up to the in-flight window while still needed."""
            nonlocal next_idx
            while (
                next_idx < len(entries)
                and len(pending) < max_workers
                and reachable_count < needed
            ):
                idx = next_idx
                next_idx += 1
                fut = pool.submit(
                    self.tor_client.check_reachable,
                    entries[idx]['url'],
                    connect_timeout=connect_timeout,
                )
                futures[fut] = idx
                pending.add(fut)

        _submit_more()

        budget_expired = False
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                budget_expired = True
                break
            done, pending = futures_wait(
                pending,
                timeout=remaining,
                return_when=FIRST_COMPLETED,
            )
            for fut in done:
                idx = futures.pop(fut)
                try:
                    verdicts[idx] = bool(fut.result())
                except Exception:
                    verdicts[idx] = False  # check_reachable swallows its own errors; belt & braces
                if verdicts[idx]:
                    reachable_count += 1
            if reachable_count >= needed:
                break  # enough reachable results — stop scheduling new probes
            _submit_more()

        unprobed_indices: List[int] = []
        if budget_expired:
            # Cancel what never started; in-flight probes are abandoned (the
            # workers finish on their own in the background). Entries without a
            # verdict — including those never scheduled past next_idx — are
            # reported as unprobed and surfaced unfiltered, so a slow Tor never
            # silently empties the SERP.
            cancelled = sum(1 for f in pending if f.cancel())
            never_scheduled = len(entries) - next_idx
            probed_no_verdict = sorted(futures[f] for f in pending)
            unprobed_indices = sorted(
                set(probed_no_verdict) | set(range(next_idx, len(entries)))
            )
            logger.warning(
                f"SERP probe budget ({budget_s:.1f}s) expired: "
                f"{len(unprobed_indices)}/{len(entries)} reachability checks "
                f"without verdict ({len(pending) - cancelled} abandoned in flight, "
                f"{cancelled} cancelled, {never_scheduled} never scheduled)"
            )
        elif pending:
            # Early stop: enough reachable results already found, so release
            # anything still queued and drop the rest (none of it was probed).
            cancelled = sum(1 for f in pending if f.cancel())
            logger.info(
                f"SERP: probe target reached ({reachable_count}/{needed}); "
                f"{cancelled} queued probe(s) cancelled, "
                f"{len(entries) - next_idx} entries never probed"
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
        # URLs already present in `results` — one row per URL, ever. A SERP can
        # list the same target repeatedly (and the BFS then follows it too);
        # without this set the same page surfaces as duplicate rows.
        surfaced_urls: Set[str] = set()
        # url -> (term_count, paragraphs) for pages already matched during the
        # BFS — lets the SERP-enrichment pass reuse them without a second
        # Tor fetch when a SERP entry points at an already-crawled page. The
        # enrichment pass writes back into it, so repeated targets are only
        # ever fetched once for paragraphs.
        match_cache: Dict[str, Tuple[int, List[str]]] = {}
        # URLs crawled during the BFS that do NOT contain the term. The
        # enrichment pass must not re-fetch them through Tor: the outcome is
        # already known (no match → no paragraphs).
        no_match_urls: Set[str] = set()

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

                    # How many more results this page must contribute. Probing
                    # stops as soon as enough reachable entries are found, so
                    # slow/unneeded entries are never probed at all.
                    needed = max_results - len(results)
                    if needed <= 0:
                        break
                    # Dedupe BEFORE probing: the same target can appear many
                    # times in one SERP (or already be a result row from an
                    # earlier page). Probing or surfacing it twice would burn
                    # Tor fetches and create duplicate rows.
                    unique_entries: List[Dict[str, str]] = []
                    seen_here: Set[str] = set()
                    for entry in entries:
                        url = entry['url']
                        if url in surfaced_urls or url in seen_here:
                            continue
                        seen_here.add(url)
                        unique_entries.append(entry)
                    entries = unique_entries

                    verdicts, unprobed_idx = self._probe_serp_reachability(entries, needed)
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
                        surfaced_urls.add(entry['url'])
                        results.append({
                            'url': entry['url'],
                            'title': entry['title'],
                            'snippet': entry['snippet'],
                            'timestamp': ts,
                            'seed': start_url,
                            'depth': depth + 1,
                            'term_count': 1,
                            'paragraphs': None,  # SERP entries: page not crawled
                        })
                        if len(results) >= max_results:
                            break
                    logger.info(f"SERP: extracted {len(results)} results from {current_url}")
                else:
                    # Normal page: search for term in text
                    count, snippet = search_term_in_text(text, search_term)
                    if not count:
                        no_match_urls.add(current_url)
                    if count:
                        paragraphs = extract_matching_paragraphs(soup, search_term)
                        match_cache[current_url] = (count, paragraphs)
                        if current_url in surfaced_urls:
                            # Already a result row (e.g. surfaced from a SERP):
                            # refresh the cache only — the enrichment pass will
                            # attach these paragraphs to the existing row.
                            # Never a duplicate row, never a refetch later.
                            logger.info(
                                f"Already surfaced {current_url} — "
                                f"match cache refreshed ({count} times)"
                            )
                        else:
                            surfaced_urls.add(current_url)
                            results.append({
                                'url': current_url,
                                'title': title,
                                'snippet': snippet,
                                'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                                'seed': start_url,
                                'depth': depth,
                                'term_count': count,
                                'paragraphs': paragraphs,
                            })
                            logger.info(f"Found '{search_term}' in {current_url} ({count} times, {len(paragraphs)} paragraphs)")

                if depth < max_depth:
                    links = extract_onion_links(current_url, soup)
                    for link in links:
                        if link not in crawled_urls:
                            queue.append((link, depth + 1))

                logger.info(f"Crawled: {len(crawled_urls)} pages | Found: {len(results)} results")
                if progress_cb:
                    progress_cb(len(crawled_urls), len(results))
                time.sleep(settings.crawl_delay)

        # Enrich SERP-derived results (paragraphs=None): the SERP page only
        # proves the entry exists — the paragraph text lives on the target
        # page. Each target is fetched at most once through Tor: cache hits
        # (BFS match or an earlier enrichment of the same URL) are reused, and
        # fresh fetches are written back into match_cache so a URL listed
        # twice never costs a second fetch.
        for result in results:
            if result.get('paragraphs') is not None:
                continue
            if result['url'] in no_match_urls:
                continue  # crawled during BFS: no term match, nothing to add
            cached = match_cache.get(result['url'])
            if cached is not None:
                result['term_count'], result['paragraphs'] = cached
                continue
            scraped = self.scrape_page(result['url'], timeout)
            if not scraped:
                continue
            _, text, soup = scraped
            count, _ = search_term_in_text(text, search_term)
            if count:
                paragraphs = extract_matching_paragraphs(soup, search_term)
                match_cache[result['url']] = (count, paragraphs)
                result['term_count'] = count
                result['paragraphs'] = paragraphs
            time.sleep(settings.crawl_delay)

        return results, len(crawled_urls)
