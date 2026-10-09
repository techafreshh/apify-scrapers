"""Google Dorks SERP scraper — Playwright + BeautifulSoup.

Runs raw Google dork queries (site:, inurl:, filetype:, intitle:, etc.)
through headless Chromium and extracts ranked organic results.

Use for OSINT / SEO research / authorized security testing only.
Google aggressively rate-limits scraping: use Apify residential proxies
and keep maxPagesPerQuery low (3-5) to avoid 'unusual traffic' blocks.
"""

import asyncio
import random
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Set

from apify import Actor
from bs4 import BeautifulSoup
from playwright.async_api import Page


BLOCK_MARKERS = [
    "our systems have detected unusual traffic",
    "unusual traffic from your computer network",
    "to continue, please type the characters",
    "recaptcha",
    "/sorry/",
    "dissolve the captcha",
]

GOOGLE_JUNK_PREFIXES = (
    "https://www.google.",
    "https://maps.google.",
    "https://support.google.",
    "https://accounts.google.",
    "/search?",
    "/aclk",
    "#",
)


def is_blocked_page(url: str, html: str) -> bool:
    lowered = html.lower()
    if "/sorry/" in url.lower():
        return True
    return any(m in lowered for m in BLOCK_MARKERS)


def clean_result_url(href: str) -> Optional[str]:
    """Resolve Google redirect wrappers (/url?q=...) to the real target URL."""
    if not href:
        return None
    href = href.strip()
    if href.startswith("/url?"):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        real = qs.get("q", [None])[0]
        if not real:
            return None
        href = real
    if href.startswith("/search") or href.startswith("#"):
        return None
    for junk in GOOGLE_JUNK_PREFIXES:
        if href.startswith(junk):
            return None
    if href.startswith("/") and not href.startswith("//"):
        return None
    if href.startswith("//"):
        href = "https:" + href
    if not href.startswith("http://") and not href.startswith("https://"):
        return None
    return href


def dedup_key(url: str) -> str:
    try:
        p = urllib.parse.urlparse(url)
        # Strip tracking params for dedup purposes
        qs = urllib.parse.parse_qsl(p.query, keep_blank_values=True)
        kept = [(k, v) for k, v in qs if not k.lower().startswith(("utm_", "gclid", "fbclid"))]
        clean_qs = urllib.parse.urlencode(sorted(kept))
        netloc = p.netloc.lower().removeprefix("www.")
        path = p.path.rstrip("/") or "/"
        return f"{p.scheme}://{netloc}{path}?{clean_qs}".lower()
    except Exception:
        return url.lower().rstrip("/")


def parse_serp(html: str) -> List[Dict[str, str]]:
    """Parse a Google SERP page into [{title, url, display_url, snippet}]."""
    soup = BeautifulSoup(html, "html.parser")
    out: List[Dict[str, str]] = []

    # Strategy 1 (most reliable 2024+): anchors with jsname=UWckNb carry the real URL.
    anchors = soup.select('a[jsname="UWckNb"]')
    # Strategy 2 fallback: classic result blocks.
    if not anchors:
        anchors = []
        for block in soup.select("div.g, div.MjjYud, div.tF2Cxc, div.dURPMd"):
            a = block.select_one("div.yuRUbf > a, a:has(h3)")
            if a:
                anchors.append(a)

    for a in anchors:
        href = clean_result_url(a.get("href", ""))
        if not href:
            continue

        # Title: h3 inside anchor, or aria-label fallback
        h3 = a.select_one("h3")
        title = h3.get_text(strip=True) if h3 else (a.get("aria-label", "") or "").strip()
        if not title:
            continue

        # Walk up to the result container for snippet + display URL
        container = a
        for _ in range(5):
            parent = container.parent
            if parent is None or parent.name in ("body", "html", "[document]"):
                break
            container = parent
            if container.select_one("div.VwiC3b, div.s3v9rd, div.IsZvec, span.aCOpRe"):
                break

        snippet = ""
        for sel in ("div.VwiC3b", "div.s3v9rd span.aCOpRe", "span.aCOpRe", "div.IsZvec"):
            el = container.select_one(sel)
            if el:
                snippet = el.get_text(separator=" ", strip=True)
                if snippet:
                    break

        display_url = ""
        for sel in ("cite", "span.qXLe6d", "div.TbwUpd cite", "div.UPmit"):
            el = container.select_one(sel)
            if el:
                display_url = el.get_text(separator=" ", strip=True)
                if display_url:
                    break
        if not display_url:
            try:
                display_url = urllib.parse.urlparse(href).netloc.removeprefix("www.")
            except Exception:
                display_url = ""

        out.append({
            "title": title,
            "url": href,
            "display_url": display_url,
            "snippet": snippet,
        })

    return out


async def accept_consent(page: Page) -> None:
    """Dismiss Google cookie/consent modal if present (mostly EU IPs)."""
    try:
        selector = (
            "form[action*='consent'] button, "
            "button[aria-label*='Accept all'], "
            "button:has-text('Accept all'), "
            "button:has-text('I agree'), "
            "button:has-text('Accept')"
        )
        btn = page.locator(selector).first
        if await btn.is_visible(timeout=2500):
            await btn.click()
            await page.wait_for_timeout(1000)
    except Exception:
        pass


async def scrape_one_dork(
    page: Page,
    dork: str,
    max_pages: int,
    hl: str,
    gl: str,
    delay_sec: float,
    seen: Set[str],
    start_rank: int = 1,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    rank = start_rank

    for page_idx in range(max_pages):
        start = page_idx * 10
        params = {
            "q": dork,
            "num": "10",
            "start": str(start),
            "hl": hl,
            "gl": gl,
            "filter": "0",   # don't omit similar results — dorkers want everything
            "pws": "0",      # disable personalization
            "udm": "14",     # opt out of AI overview / web-only results where supported
        }
        # udm=14 is ignored on some locales; harmless if so.
        target = "https://www.google.com/search?" + urllib.parse.urlencode(params)
        Actor.log.info(f"[dork] '{dork}' — page {page_idx + 1}/{max_pages}: {target}")

        try:
            await page.goto(target, wait_until="domcontentloaded", timeout=45000)
            await accept_consent(page)
            try:
                await page.wait_for_selector("div#search, div#rso", timeout=12000)
            except Exception:
                pass
            await page.wait_for_timeout(1200)
            html = await page.content()

            if is_blocked_page(page.url, html):
                Actor.log.warning(
                    f"[dork] '{dork}' hit Google anti-bot ('unusual traffic'). "
                    "Stopping this query — enable residential proxies and raise delaySec."
                )
                await Actor.push_data({
                    "dork_query": dork,
                    "status": "blocked",
                    "page": page_idx + 1,
                    "source": "google_dorks",
                })
                break

            parsed = parse_serp(html)
            if not parsed:
                # Empty page = end of results or layout change
                Actor.log.info(f"[dork] '{dork}' page {page_idx + 1}: no organic results — ending pagination.")
                break

            new_on_page = 0
            for r in parsed:
                key = dedup_key(r["url"])
                if key in seen:
                    continue
                seen.add(key)
                record = {
                    "dork_query": dork,
                    "rank": rank,
                    "page": page_idx + 1,
                    "title": r["title"],
                    "url": r["url"],
                    "display_url": r["display_url"],
                    "snippet": r["snippet"],
                    "serp_url": target,
                    "source": "google_dorks",
                }
                records.append(record)
                await Actor.push_data(record)
                rank += 1
                new_on_page += 1

            Actor.log.info(f"[dork] '{dork}' page {page_idx + 1}: +{new_on_page} new (total this dork: {len(records)})")

            # Last page rarely full — stop early if Google returned <10
            if len(parsed) < 10:
                break

            jitter = random.uniform(0.5, 1.5)
            await asyncio.sleep(delay_sec + jitter)

        except Exception as e:
            Actor.log.warning(f"[dork] '{dork}' page {page_idx + 1} navigation error: {e}")
            break

    return records


async def main() -> None:
    async with Actor:
        from playwright.async_api import async_playwright

        actor_input = await Actor.get_input() or {}
        raw_queries = actor_input.get("dorkQueries", [])
        if isinstance(raw_queries, str):
            # Allow pasting a newline-separated block into a single field
            raw_queries = [q.strip() for q in re.split(r"[\r\n]+", raw_queries) if q.strip()]
        dork_queries: List[str] = [q.strip() for q in raw_queries if isinstance(q, str) and q.strip()]

        max_pages = int(actor_input.get("maxPagesPerQuery", 3))
        max_results = int(actor_input.get("maxResults", 60))
        hl = str(actor_input.get("hl", "en") or "en")
        gl = str(actor_input.get("gl", "us") or "us")
        delay_sec = float(actor_input.get("delaySec", 2.5))

        if not dork_queries:
            Actor.log.warning("No dorkQueries provided. Exiting.")
            return

        Actor.log.info(
            f"Starting Google Dorks scraper: {len(dork_queries)} dork(s), "
            f"{max_pages} page(s) each, cap {max_results} total."
        )

        proxy_url: Optional[str] = None
        proxy_input = actor_input.get("proxyConfiguration")
        if proxy_input and isinstance(proxy_input, dict) and proxy_input.get("useApifyProxy"):
            try:
                proxy_config = await Actor.create_proxy_configuration(actor_proxy_input=proxy_input)
                if proxy_config:
                    proxy_url = await proxy_config.new_url()
            except Exception as e:
                Actor.log.warning(f"Could not initialize proxy: {e}. Running without proxy.")
        if not proxy_url:
            Actor.log.warning(
                "No residential proxy configured — Google will likely block after a few requests. "
                "Set proxyConfiguration to Apify Proxy (residential) for production runs."
            )

        launch_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
        ]

        total = 0
        seen: Set[str] = set()

        async with async_playwright() as p:
            proxy_opt = {"server": proxy_url} if proxy_url else None
            browser = await p.chromium.launch(headless=True, args=launch_args, proxy=proxy_opt)
            context = await browser.new_context(
                viewport={"width": 1366, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = await context.new_page()

            for dork in dork_queries:
                if total >= max_results:
                    Actor.log.info(f"Global cap {max_results} reached — stopping.")
                    break
                remaining = max_results - total
                # ~10 results/page, cap pages so we don't overshoot wildly
                pages_for_dork = min(max_pages, max(1, (remaining + 9) // 10))
                batch = await scrape_one_dork(page, dork, pages_for_dork, hl, gl, delay_sec, seen)
                total += len(batch)
                Actor.log.info(f"Finished dork '{dork}': {len(batch)} results (run total: {total})")
                await asyncio.sleep(1.0)

            await browser.close()

        Actor.log.info(f"Google Dorks scrape completed! Total unique results: {total}")


if __name__ == "__main__":
    asyncio.run(main())
