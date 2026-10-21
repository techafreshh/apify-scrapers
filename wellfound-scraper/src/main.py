"""Wellfound (formerly AngelList) startup scraper.

Design note — why the browser is only used for *access*:
    Wellfound sits behind Cloudflare. A datacenter HTTP client gets an
    interactive "403 SECURITY CHECK" page. A headful Chromium through a
    residential proxy clears the listing page fine, but company profile pages
    still return the hard security check (see Limitations).
    Because the listing is server-rendered, we then hand the HTML to BeautifulSoup
    instead of walking the DOM in-page. That is both faster and far more robust
    than JS selectors, and it returns the full ~50 cards rather than whatever has
    rendered at the moment we look.

Extracted per company: name, slug, profile URL, headcount band (+ midpoint),
tagline, tags (industry + market stage), open roles and logo.
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from apify import Actor
from bs4 import BeautifulSoup

BASE = "https://wellfound.com"
DEFAULT_LISTINGS = ["https://wellfound.com/jobs"]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Nudge the page a little before reading HTML, so any client-side hydration of the
# listing grid has a chance to append cards.
SCROLL_SETTLE_MS = 2500

_BAND_RE = re.compile(r"(\d+)\s*-\s*(\d+)")
_OPEN_ROLES_RE = re.compile(r"(\d+)\s*open positions?", re.IGNORECASE)

# Wellfound stage labels mapped onto a normalized token.
STAGE_MAP = {
    "pre-seed": "pre_seed",
    "preseed": "pre_seed",
    "seed": "seed",
    "series a": "series_a",
    "series b": "series_b",
    "series c": "series_c",
    "series d": "series_d_plus",
    "late stage": "series_d_plus",
    "bootstrapped": "unknown",
}


def normalize_stage(raw: str) -> str:
    """Map a Wellfound funding-stage label onto a normalized token."""
    if not raw:
        return "unknown"
    s = re.sub(r"\s+", " ", raw.strip().lower().replace("-", " ").replace("_", " "))
    if s in STAGE_MAP:
        return STAGE_MAP[s]
    if "pre" in s and "seed" in s:
        return "pre_seed"
    for key, token in (
        ("series a", "series_a"),
        ("series b", "series_b"),
        ("series c", "series_c"),
        ("series d", "series_d_plus"),
        ("late", "series_d_plus"),
        ("seed", "seed"),
        ("bootstrapped", "unknown"),
    ):
        if key in s:
            return token
    return "unknown"


def team_size_estimate(raw: str) -> Optional[int]:
    """Midpoint of a headcount band like '11-50', '1-10' or '100+'."""
    if not raw:
        return None
    m = _BAND_RE.search(raw)
    if m:
        return (int(m.group(1)) + int(m.group(2))) // 2
    m = re.search(r"(\d+)\s*\+", raw)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"\s*(\d+)\s*", raw or "")
    if m:
        return int(m.group(1))
    return None


def _text(node: Any) -> Optional[str]:
    if node is None:
        return None
    t = node.get_text(" ", strip=True)
    return t or None


def parse_listing(html: str) -> List[Dict[str, Any]]:
    """Parse a Wellfound listing page into company records.

    Cards are identified by the company anchor inside an h3; each card is then
    the nearest ancestor that still contains only that one company heading.
    """
    soup = BeautifulSoup(html, "html.parser")
    out: List[Dict[str, Any]] = []
    seen: set = set()

    for a in soup.select('h3 a[href^="/company/"]'):
        href = (a.get("href") or "").split("?")[0]
        if not href or href.endswith("/jobs") or href in seen:
            continue
        seen.add(href)

        # Walk up to the tightest container holding exactly one company heading.
        node = a.find_parent("div")
        for _ in range(8):
            if node is None or node.parent is None:
                break
            if len(node.parent.select("h3")) > 1:
                break
            node = node.parent
        root = node if node is not None else a.parent

        team_raw = _text(root.select_one("span.text-xs"))
        tagline = _text(root.select_one("span.text-sm"))

        tags = [
            t
            for t in (
                _text(li.select_one(".line-clamp-1")) or _text(li)
                for li in root.select("li")
            )
            if t
        ]

        open_roles = None
        for el in root.select("*"):
            if el.name and not el.find(True):
                m = _OPEN_ROLES_RE.fullmatch(el.get_text(strip=True))
                if m:
                    open_roles = int(m.group(1))
                    break

        img = root.select_one('img[src*="photos.wellfound.com"]')

        out.append({
            "name": _text(a),
            "slug": href.replace("/company/", ""),
            "url": f"{BASE}{href}",
            "team_size_raw": team_raw,
            "tagline": tagline,
            "tags": tags,
            "open_roles": open_roles,
            "logo_url": img.get("src") if img else None,
        })

    return [c for c in out if c.get("name")]


def build_record(
    listing: Dict[str, Any], detail: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Merge listing + optional detail data into a dataset record."""
    name = (listing.get("name") or "").strip()
    if not name:
        return None

    detail = detail or {}
    team_raw = listing.get("team_size_raw") or detail.get("team_size")
    stage_raw = detail.get("funding_stage")

    open_roles = listing.get("open_roles")
    if open_roles is None and listing.get("tagline"):
        m = _OPEN_ROLES_RE.search(listing["tagline"])
        if m:
            open_roles = int(m.group(1))

    return {
        "name": name,
        "slug": listing.get("slug"),
        "url": listing.get("url"),
        "website": detail.get("website"),
        "tagline": listing.get("tagline"),
        "description": detail.get("description") or listing.get("tagline"),
        "funding_stage": stage_raw,
        "funding_stage_normalized": normalize_stage(stage_raw) if stage_raw else "unknown",
        "team_size": team_raw,
        "team_size_estimate": team_size_estimate(team_raw or ""),
        "location": detail.get("headquarters"),
        "industry": detail.get("industry"),
        "tags": listing.get("tags") or None,
        "open_roles": open_roles,
        "logo_url": listing.get("logo_url"),
        "source": "wellfound_browser",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


async def wait_for_cloudflare(page, timeout_ms: int = 45000) -> bool:
    """Poll until Cloudflare's interstitial clears. True when the page is usable."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + (timeout_ms / 1000)
    while loop.time() < deadline:
        try:
            title = ((await page.title()) or "").lower()
            if "just a moment" not in title and "attention required" not in title:
                return True
        except Exception:
            pass
        await page.wait_for_timeout(1500)
    return False


NAV_TIMEOUT_MS = 90000


async def open_listing(
    pw: Any, proxy_url: Optional[str], headless: bool, listing_url: str
) -> Tuple[Any, Any, Any, List[Dict[str, Any]]]:
    """Launch a browser and return (browser, context, page, cards) for a listing.

    Navigation waits on `commit` rather than `domcontentloaded` so that slow
    third-party subresources cannot cause a navigation timeout; the challenge
    poll below is what actually gates readiness.
    """
    browser = await pw.chromium.launch(**launch_options(proxy_url, headless))
    context = await browser.new_context(
        user_agent=UA,
        locale="en-US",
        timezone_id="Africa/Lagos",
        viewport={"width": 1440, "height": 1200},
    )
    await context.add_init_script(
        "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    )
    page = await context.new_page()
    page.set_default_timeout(NAV_TIMEOUT_MS)

    await page.goto(listing_url, wait_until="commit", timeout=NAV_TIMEOUT_MS)
    await wait_for_cloudflare(page, timeout_ms=45000)

    # The server-rendered listing holds every card, but client-side hydration
    # replaces the grid with a much smaller paginated list. Once the browser has
    # cleared the challenge, re-request the page through the same context so we
    # share its cookies and proxy but get the original server HTML.
    html = None
    try:
        api = await context.request.get(listing_url, timeout=NAV_TIMEOUT_MS)
        if api.ok:
            candidate = await api.text()
            if "just a moment" not in candidate[:2000].lower():
                html = candidate
    except Exception as e:
        Actor.log.info(f"SSR re-request failed ({type(e).__name__}: {str(e)[:80]}); "
                       "falling back to rendered DOM")

    if html is None:
        # Re-clear the challenge before trusting the rendered DOM.
        await wait_for_cloudflare(page, timeout_ms=30000)
        await page.wait_for_timeout(SCROLL_SETTLE_MS)
        for _ in range(3):
            await page.mouse.wheel(0, 2000)
            await page.wait_for_timeout(900)
        html = await page.content()

    cards = parse_listing(html)
    Actor.log.info(
        f"{listing_url} -> {len(cards)} companies parsed "
        f"(html {len(html)//1024}KB, title='{await page.title()}')"
    )
    return browser, context, page, cards


async def try_open_listing(
    pw: Any, proxy_url: Optional[str], headless: bool, listing_url: str, attempts: int = 3
) -> Tuple[Any, Any, Any, List[Dict[str, Any]]]:
    """open_listing with retries. Each retry gets a fresh browser => a fresh proxy IP."""
    last: Optional[str] = None
    for i in range(1, attempts + 1):
        browser = None
        try:
            return await open_listing(pw, proxy_url, headless, listing_url)
        except Exception as e:
            last = f"{type(e).__name__}: {str(e)[:140]}"
            Actor.log.warning(f"Listing attempt {i}/{attempts} failed ({last})")
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass
            await asyncio.sleep(3)
    raise RuntimeError(f"listing unavailable after {attempts} attempts — {last}")


def launch_options(proxy_url: Optional[str], headless: bool = False) -> Dict[str, Any]:
    """Translate a proxy URL into Playwright launch kwargs."""
    kwargs: Dict[str, Any] = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    }
    if not proxy_url:
        return kwargs
    if "@" in proxy_url and "://" in proxy_url:
        scheme, rest = proxy_url.split("://", 1)
        creds, host = rest.rsplit("@", 1)
        user, _, pwd = creds.partition(":")
        kwargs["proxy"] = {"server": f"{scheme}://{host}", "username": user, "password": pwd}
    else:
        kwargs["proxy"] = {"server": proxy_url}
    return kwargs


async def resolve_proxy(actor_input: Dict[str, Any]) -> Optional[str]:
    """Resolve a proxy URL from input, or None to run direct."""
    explicit = actor_input.get("proxyConfiguration")
    if isinstance(explicit, dict) and explicit:
        cfg = await Actor.create_proxy_configuration(actor_proxy_input=explicit)
    elif actor_input.get("useProxy", True):
        groups = actor_input.get("proxyGroups") or ["BUYPROXIES94952"]
        if isinstance(groups, str):
            groups = [groups]
        cfg = await Actor.create_proxy_configuration(groups=groups)
        Actor.log.info(f"Using Apify Proxy groups: {groups}")
    else:
        Actor.log.info("Proxy disabled by input — running direct (bot detection likely).")
        return None
    return (await cfg.new_url()) if cfg else None


async def main() -> None:
    # Lazy import keeps the parsing helpers unit-testable without Playwright.
    from playwright.async_api import async_playwright

    async with Actor:
        actor_input = await Actor.get_input() or {}
        listings = actor_input.get("listingUrls") or DEFAULT_LISTINGS
        if isinstance(listings, str):
            listings = [listings]
        max_companies = int(actor_input.get("maxCompanies", 25))
        fetch_details = bool(actor_input.get("fetchDetails", False))
        headless = bool(actor_input.get("headless", False))

        Actor.log.info(
            f"Starting Wellfound Scraper: listings={listings}, "
            f"maxCompanies={max_companies}, fetchDetails={fetch_details}, "
            f"headless={headless}"
        )

        proxy_url = await resolve_proxy(actor_input)
        harvested = 0
        seen_slugs: set = set()

        async with async_playwright() as pw:
            for listing_url in listings:
                if harvested >= max_companies:
                    break

                try:
                    browser, context, page, cards = await try_open_listing(
                        pw, proxy_url, headless, listing_url
                    )
                except Exception as e:
                    Actor.log.warning(f"Giving up on {listing_url}: {e}")
                    continue

                try:
                    for card in cards:
                        if harvested >= max_companies:
                            break
                        slug = card.get("slug")
                        if not slug or slug in seen_slugs:
                            continue
                        seen_slugs.add(slug)

                        detail = None
                        if fetch_details:
                            # Company profile pages sit behind a hard Cloudflare
                            # security check, so this is best-effort only.
                            try:
                                await page.goto(
                                    card["url"], wait_until="commit", timeout=NAV_TIMEOUT_MS
                                )
                                if await wait_for_cloudflare(page, timeout_ms=20000):
                                    await page.wait_for_timeout(2500)
                                    detail = await page.evaluate(DETAIL_JS)
                                else:
                                    Actor.log.info(
                                        f"  detail blocked by security check: {card['name']}"
                                    )
                            except Exception as e:
                                Actor.log.info(f"  detail failed for {card['name']}: {e}")

                        record = build_record(card, detail)
                        if not record:
                            continue
                        await Actor.push_data(record)
                        harvested += 1
                        Actor.log.info(
                            f"[{harvested}/{max_companies}] Saved: {record['name']}"
                            + (f" — team {record['team_size']}" if record["team_size"] else "")
                        )
                        await asyncio.sleep(0.4)
                finally:
                    try:
                        await context.close()
                        await browser.close()
                    except Exception:
                        pass

        Actor.log.info(f"Wellfound scrape complete! Companies harvested: {harvested}")


# Best-effort detail extraction. Company pages are currently behind an
# interactive Cloudflare check, so this rarely fires — kept for the case where a
# given IP/proxy is not challenged.
DETAIL_JS = """
() => {
    const body = document.body.innerText || '';
    const labelValue = (labels) => {
        for (const label of labels) {
            const re = new RegExp(label + '\\\\s*\\\\n\\\\s*([^\\\\n]{1,80})', 'i');
            const m = body.match(re);
            if (m) {
                const v = m[1].trim();
                if (v) return v;
            }
        }
        return null;
    };
    let website = null;
    for (const a of document.querySelectorAll('a[href^="http"]')) {
        if (!/wellfound\\.com|angel\\.co|linkedin\\.com|twitter\\.com|x\\.com/.test(a.href)) {
            website = a.href;
            break;
        }
    }
    const descEl = document.querySelector('meta[name="description"]');
    return {
        funding_stage: labelValue(['Funding Stage', 'Funding stage']),
        team_size: labelValue(['Team Size', 'Company Size', 'Headcount']),
        headquarters: labelValue(['Headquarters', 'Location', 'HQ']),
        industry: labelValue(['Industry', 'Category', 'Sector']),
        website,
        description: descEl ? descEl.getAttribute('content') : null,
    };
}
"""


if __name__ == "__main__":
    asyncio.run(main())
