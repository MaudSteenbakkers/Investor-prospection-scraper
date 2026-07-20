"""
Web crawling for the InnoSer prospection scraper.

Strategy (confirmed with Maud, July 2026):
  - requests + BeautifulSoup is the default for every page. This is the
    version that ran ~1000 companies in under 6 hours previously -- proven
    and fast. A prior Playwright-only rewrite was slower (real browser
    overhead per page) and was rolled back.
  - A SMALL, TARGETED Playwright fallback is used only for pages that look
    like they should have pipeline content but returned almost no text via
    requests (a good proxy for "this page needs JS to render"). This keeps
    the bulk of the crawl fast while still catching JS-heavy pipeline pages.
"""

import re
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import numpy as np
import requests
from bs4 import BeautifulSoup

from config import EXCLUDED_URL_SEGMENTS, FOCUS_URL_SEGMENTS, PIPELINE_IMAGE_KEYWORDS, PRIORITY_WORDS

# Below this many characters of extracted text on a FOCUS-looking page,
# we suspect the content is JS-rendered and requests couldn't see it.
SPARSE_TEXT_THRESHOLD = 300

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _make_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


def normalize_url(url):
    if url is None or (isinstance(url, float) and np.isnan(url)):
        return None
    url = str(url).strip()
    if not url:
        return None
    if not urlparse(url).scheme:
        url = "https://" + url
    return url


def strip_fragment(url):
    parsed = urlparse(url)
    return parsed._replace(fragment="").geturl()


def is_excluded_url(url):
    url_lower = strip_fragment(url).lower()
    return any(segment in url_lower for segment in EXCLUDED_URL_SEGMENTS)


def is_english_url(url):
    url_lower = url.lower()
    parsed = urlparse(url_lower)
    english_url_hints = [
        "/en/", "/en-us/", "/en-gb/", "/english/", "/en_us/", "/en_gb/",
        "?lang=en", "&lang=en", "?language=en", "&language=en",
        "locale=en", "?hl=en", "&hl=en", "lang=english", "language=english",
        "/eng/", "/eng_m/", "/en.php", "/eng.php",
        "?lang=eng", "&lang=eng", "/global/", "/global-en/", "/worldwide/",
    ]
    english_subdomain_hints = ["en.", "eng.", "english.", "global."]
    if any(hint in url_lower for hint in english_url_hints):
        return True
    if any(parsed.netloc.startswith(hint) for hint in english_subdomain_hints):
        return True
    return False


def detect_page_language(soup):
    if soup is None:
        return None
    html_tag = soup.find("html")
    if html_tag and html_tag.get("lang"):
        return html_tag["lang"].lower().strip()
    meta_lang = soup.find(
        "meta", attrs={"http-equiv": lambda x: x and x.lower() == "content-language"}
    )
    if meta_lang and meta_lang.get("content"):
        return meta_lang["content"].lower().strip()
    og_locale = soup.find("meta", property="og:locale")
    if og_locale and og_locale.get("content"):
        return og_locale["content"].lower().strip()
    return None


def is_non_english_page(soup):
    non_english_lang_codes = [
        "zh", "zh-cn", "zh-tw", "ja", "ko", "th", "vi", "id", "ms",
        "zh-hans", "zh-hant",
    ]
    lang = detect_page_language(soup)
    if lang is None:
        return False
    return any(lang.startswith(code) for code in non_english_lang_codes)


def fetch_page(url, session, timeout=7):
    """Fetch a page using the shared session. Returns (text, soup)."""
    try:
        url = normalize_url(url)
        if not url:
            return "", None
        resp = session.get(url, timeout=timeout, allow_redirects=True)
        resp.raise_for_status()
        if "text/html" not in resp.headers.get("Content-Type", ""):
            return "", None
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        return text, soup
    except Exception as e:
        print(f"    \u26a0 Could not fetch {url}: {e}")
        return "", None


def _looks_like_sparse_focus_page(url, text):
    """True if this looks like a pipeline/science page that returned
    suspiciously little text -- a proxy for 'this needs JS rendering'."""
    url_lower = url.lower()
    is_focus_url = any(seg in url_lower for seg in FOCUS_URL_SEGMENTS)
    return is_focus_url and len(text or "") < SPARSE_TEXT_THRESHOLD


def render_with_playwright(url, timeout_ms=15000):
    """
    Fallback fetch for pages suspected of being JS-rendered.
    Only called for a small subset of pages (see _looks_like_sparse_focus_page),
    so the overhead of spinning up a browser is limited to where it matters.
    Returns (text, soup) same shape as fetch_page(), or ("", None) on failure.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("    \u26a0 Playwright not installed -- skipping JS-render fallback")
        return "", None

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(user_agent=USER_AGENT)
            page.goto(url, timeout=timeout_ms, wait_until="networkidle")
            html = page.content()
            browser.close()
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())
        return text, soup
    except Exception as e:
        print(f"    \u26a0 Playwright fallback failed for {url}: {e}")
        return "", None


def find_english_version(base_url, domain, soup, session):
    parsed_base = urlparse(base_url)
    scheme = parsed_base.scheme
    english_link_text_hints = [
        "english", "en", "view in english", "english version",
        "global", "international", "worldwide",
    ]

    for link_tag in soup.find_all("link", rel="alternate"):
        hreflang = link_tag.get("hreflang", "").lower()
        if hreflang in ["en", "en-us", "en-gb", "en-au", "en-ca"]:
            href = link_tag.get("href")
            if href:
                abs_href = urljoin(base_url, href)
                parsed_href = urlparse(abs_href)
                if parsed_href.netloc in [domain, f"en.{domain}", f"eng.{domain}"]:
                    return abs_href

    if is_non_english_page(soup):
        for prefix in ["en", "eng", "english", "global", "www-en"]:
            candidate = f"{scheme}://{prefix}.{domain}"
            try:
                resp = session.head(candidate, timeout=5, allow_redirects=True)
                if resp.status_code < 400:
                    return candidate
            except Exception:
                pass

        common_english_paths = [
            "/en/", "/eng/", "/english/", "/en-us/", "/en_us/",
            "/global/", "/international/", "/worldwide/",
            "/en/home", "/eng/home",
        ]
        root = f"{scheme}://{domain}"
        for path in common_english_paths:
            candidate = root + path
            try:
                resp = session.head(candidate, timeout=5, allow_redirects=True)
                if resp.status_code < 400:
                    return candidate
            except Exception:
                pass

    for a in soup.find_all("a", href=True):
        candidate = urljoin(base_url, a["href"].strip())
        candidate_parsed = urlparse(candidate)
        if candidate_parsed.netloc in [domain, f"en.{domain}", f"eng.{domain}"]:
            if is_english_url(candidate):
                return candidate

    for a in soup.find_all("a", href=True):
        link_text = a.get_text(strip=True).lower()
        if link_text in english_link_text_hints:
            candidate = urljoin(base_url, a["href"].strip())
            candidate_parsed = urlparse(candidate)
            if candidate_parsed.netloc in [domain, f"en.{domain}", f"eng.{domain}"]:
                return candidate

    return None


def _discover_links(soup, base_url, domain, visited, in_queue):
    links = []
    for a in soup.find_all("a", href=True):
        candidate = strip_fragment(urljoin(base_url, a["href"].strip()))
        parsed = urlparse(candidate)
        if (
            parsed.scheme in ("http", "https")
            and parsed.netloc == domain
            and candidate not in visited
            and candidate not in in_queue
            and not is_excluded_url(candidate)
        ):
            links.append(candidate)
    return links


def has_pipeline_images(soup, url):
    url_lower = url.lower()
    is_pipeline_page = any(
        seg in url_lower for seg in [
            "pipeline", "programs", "portfolio",
            "platform", "science", "indications",
        ]
    )
    if not is_pipeline_page:
        return False

    images = soup.find_all("img", src=True)
    for img in images:
        src = img.get("src", "").lower()
        if any(kw in src for kw in PIPELINE_IMAGE_KEYWORDS):
            return True
    return False


def crawl_website(base_url, max_pages=10, use_playwright_fallback=True):
    """
    Crawl up to max_pages pages for one company.
    Page fetches within this crawl are parallelized (4 workers).
    Pages that look like sparse/JS-rendered pipeline pages get a
    one-off Playwright re-fetch if use_playwright_fallback is True.
    """
    base_url = normalize_url(base_url)
    if not base_url:
        return {}

    parsed_base = urlparse(base_url)
    domain = parsed_base.netloc
    visited = set()
    soups_by_url = {}

    session = _make_session()

    initial_text, initial_soup = fetch_page(base_url, session)
    effective_start = base_url

    needs_english_search = (
        not is_english_url(base_url) or is_non_english_page(initial_soup)
    )

    if initial_soup and needs_english_search:
        english_url = find_english_version(base_url, domain, initial_soup, session)
        if english_url and english_url != base_url:
            print(f"  \u2192 Found English version: {english_url}")
            alt_text, alt_soup = fetch_page(english_url, session)
            if alt_text and alt_soup:
                if not is_non_english_page(alt_soup):
                    effective_start = english_url
                    initial_text, initial_soup = alt_text, alt_soup
                    domain = urlparse(english_url).netloc
                    print(f"  \u2713 Switched to English version: {effective_start}")

    to_visit = deque([effective_start])
    prefetched = {effective_start: (initial_text, initial_soup)}
    fetch_queue = []

    while to_visit and (len(soups_by_url) + len(fetch_queue)) < max_pages:
        url = to_visit.popleft()
        if url in visited:
            continue
        visited.add(url)

        if is_excluded_url(url):
            print(f"  \u2298 Skipped (excluded path): {url}")
            continue

        if url in prefetched:
            text, soup = prefetched.pop(url)
            if not text or soup is None:
                continue
            if is_non_english_page(soup):
                print(f"  \u2298 Skipped (non-English page): {url}")
                continue
            soups_by_url[url] = soup
            print(f"  \u21b3 Crawling (cached): {url}")

            new_links = _discover_links(soup, url, domain, visited, set(to_visit))
            priority = [l for l in new_links if any(w in l.lower() for w in PRIORITY_WORDS)]
            en_normal = [l for l in new_links if l not in priority and is_english_url(l)]
            other = [l for l in new_links if l not in priority and l not in en_normal]

            new_queue = deque(priority + en_normal)
            new_queue.extend(to_visit)
            new_queue.extend(other)
            to_visit = new_queue
        else:
            fetch_queue.append(url)

    remaining_slots = max_pages - len(soups_by_url)
    urls_to_fetch = fetch_queue[:remaining_slots]

    if urls_to_fetch:
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_url = {
                executor.submit(fetch_page, url, session): url
                for url in urls_to_fetch
            }
            for future in as_completed(future_to_url):
                url = future_to_url[future]
                try:
                    text, soup = future.result()
                    if not text or soup is None:
                        continue
                    if is_non_english_page(soup):
                        print(f"  \u2298 Skipped (non-English page): {url}")
                        continue
                    print(f"  \u21b3 Crawling: {url}")
                    soups_by_url[url] = soup
                except Exception as e:
                    print(f"  \u26a0 Error fetching {url}: {e}")

    session.close()

    # Targeted Playwright fallback: only for pages that look sparse/JS-rendered
    if use_playwright_fallback:
        for url in list(soups_by_url.keys()):
            soup = soups_by_url[url]
            text = " ".join(soup.get_text(separator=" ").split())
            if _looks_like_sparse_focus_page(url, text):
                print(f"  \u21bb Sparse pipeline page detected, retrying with Playwright: {url}")
                pw_text, pw_soup = render_with_playwright(url)
                if pw_soup is not None and len(pw_text) > len(text):
                    soups_by_url[url] = pw_soup
                    print(f"  \u2713 Playwright fetched more content ({len(pw_text)} vs {len(text)} chars)")

    return soups_by_url
