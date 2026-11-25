"""HiringCafe job scraper.

HiringCafe aggregates ~4.5M open jobs and is the best-filtered board reviewed so
far, but every path is behind Cloudflare: plain HTTP gets 403 and server-side
fetch services get ``bot_blocked``. So the browser is used for *access*, and the
page HTML is parsed afterwards.

Discovery deliberately stays on robots-permitted paths
(https://hiringcafe.com/robots.txt):
  * allowed   — /job, /job/, /jobs/, /recently-posted-jobs/ and the sitemaps
  * avoided   — /applyclick, /api/*, /*?page=*, /b/, /company/, /org/, /req/

Flow:
  1. Open /recently-posted-jobs through Playwright + a residential proxy, wait
     out the Cloudflare interstitial, scroll the infinite list, collect
     ``/job/<slug>`` links (the card text carries title/company/location/date).
  2. For each link, open the posting and read its schema.org ``JobPosting``
     JSON-LD (title, company, description, salary, location, datePosted,
     employmentType, TELECOMMUTE flag). Fall back to h1/og:title and the main
     text when a posting has no JSON-LD.
  3. Emit one row per posting. Geography is emitted as-is — the downstream
     Nigeria-remote gate decides eligibility.

Cloudflare handling is deliberately non-fatal: a page that stays challenged
after the retry ladder is counted and skipped, and the run keeps going. A run
that collects nothing logs an error and exits cleanly instead of crashing.
"""

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from apify import Actor
from bs4 import BeautifulSoup

BASE = "https://hiringcafe.com"
RECENT_URL = f"{BASE}/recently-posted-jobs"
DEFAULT_PROXY_GROUPS = ["RESIDENTIAL"]

CHALLENGE_MARKERS = (
    "just a moment",
    "verifying you are human",
    "performing security verification",
    "checking your browser",
    "enable javascript and cookies to continue",
    # A WAF firewall block (different from the JS interstitial): the IP itself is
    # burned, so the fix is a fresh proxy session, not more waiting.
    "attention required",
    "you have been blocked",
    "unable to access",
    "access denied",
)

TIME_FILTERS = {
    "past_24h": timedelta(days=1),
    "past_week": timedelta(days=7),
    "past_month": timedelta(days=30),
    "any": None,
}

_JOB_PATH_RE = re.compile(r"^/job/([^/?#]+)")


def slug_id(path: str) -> Optional[str]:
    """HiringCafe slugs end in a short id: ``...-meridian-mississippi-dhfp5rbcjanjj2r6``."""
    match = _JOB_PATH_RE.match(path)
    if not match:
        return None
    slug = match.group(1)
    return slug.rsplit("-", 1)[-1] if "-" in slug else slug


def money(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.replace(",", "").replace("$", "").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def country_name(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        return value.get("name") or value.get("addressCountry")
    if isinstance(value, str):
        return value
    return None


def parse_jsonld_postings(html: str) -> Optional[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw or "JobPosting" not in raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for item in as_list(data):
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
            if isinstance(item, dict) and "@graph" in item:
                for node in as_list(item["@graph"]):
                    if isinstance(node, dict) and node.get("@type") == "JobPosting":
                        return node
    return None


def record_from_jsonld(posting: Dict[str, Any], url: str) -> Dict[str, Any]:
    locations = [loc for loc in as_list(posting.get("jobLocation")) if isinstance(loc, dict)]
    countries: List[str] = []
    cities: List[str] = []
    for loc in locations:
        address = loc.get("address") if isinstance(loc.get("address"), dict) else {}
        country = country_name(address.get("addressCountry"))
        if country and country not in countries:
            countries.append(country)
        city = address.get("addressLocality")
        if city and city not in cities:
            cities.append(city)

    for req in as_list(posting.get("applicantLocationRequirements")):
        name = country_name(req)
        if name and name not in countries:
            countries.append(name)

    salary = posting.get("baseSalary") if isinstance(posting.get("baseSalary"), dict) else {}
    value = salary.get("value") if isinstance(salary.get("value"), dict) else {}
    body = posting.get("description")
    description = BeautifulSoup(body, "html.parser").get_text("\n", strip=True) if body else None

    org = posting.get("hiringOrganization")
    company = org.get("name") if isinstance(org, dict) else (org if isinstance(org, str) else None)

    employment = posting.get("employmentType")
    if isinstance(employment, list):
        employment = ", ".join(str(x) for x in employment if x)

    remote = str(posting.get("jobLocationType", "")).upper() == "TELECOMMUTE"

    return {
        "external_id": slug_id(urlparse(url).path),
        "title": posting.get("title"),
        "company": company,
        "location": ", ".join(cities[:2]) or (", ".join(countries) if countries else None),
        "cities": cities or None,
        "countries": countries or None,
        "country": countries[0] if countries else None,
        "remote": remote,
        "employment_type": employment,
        "salary_min": money(value.get("minValue")),
        "salary_max": money(value.get("maxValue")),
        "salary_currency": salary.get("currency"),
        "salary_period": value.get("unitText"),
        "posted_at": posting.get("datePosted"),
        "description": description,
        "apply_url": None,
        "source_url": url,
        "source": "hiringcafe",
    }


def record_from_dom(page_data: Dict[str, Any], card_text: Optional[str]) -> Dict[str, Any]:
    """Fallback for postings without JSON-LD: h1/og:title plus the main text."""
    url = page_data.get("url") or ""
    title = (page_data.get("h1") or "").strip() or None
    company = None

    og_title = page_data.get("og_title") or page_data.get("title") or ""
    cleaned = re.sub(r"\s*\|\s*HiringCafe\s*$", "", og_title, flags=re.IGNORECASE).strip()
    if not title:
        title = cleaned
    if " at " in cleaned:
        parts = cleaned.rsplit(" at ", 1)
        title = title or parts[0].strip() or None
        company = parts[1].strip() or None
    elif card_text:
        first_line = card_text.strip().split("\n")[0]
        title = title or first_line or None

    text = page_data.get("text") or ""
    description = text.strip() or None
    remote = bool(re.search(r"\b(fully remote|remote)\b", text, re.IGNORECASE))

    apply_url = None
    for href in page_data.get("external_links") or []:
        if re.search(r"(greenhouse|lever|workable|ashby|smartrecruiters|myworkday|jobvite|bamboohr|apply)", href, re.I):
            apply_url = href
            break

    salary_min = salary_max = None
    salary_match = re.search(r"\$\s*([\d,]{2,})\s*(?:-|to)\s*\$?\s*([\d,]{2,})", text)
    if salary_match:
        salary_min = money(salary_match.group(1))
        salary_max = money(salary_match.group(2))

    return {
        "external_id": slug_id(urlparse(url).path),
        "title": title,
        "company": company,
        "location": None,
        "cities": None,
        "countries": None,
        "country": None,
        "remote": remote,
        "employment_type": None,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "salary_currency": "USD" if salary_min else None,
        "salary_period": None,
        "posted_at": None,
        "description": description,
        "apply_url": apply_url,
        "source_url": url,
        "source": "hiringcafe",
    }


def card_record(url: str, card: Any) -> Optional[Dict[str, Any]]:
    """Build a record from a listing card.

    HiringCafe cards render as three lines — title, company, "<city, region,
    country> · <Mon DD>" — with no separators, so the fields are positional.
    When the layout shifts and a line is missing, the remaining fields are left
    null rather than guessed.
    """
    if isinstance(card, dict):
        text = (card.get("text") or "").strip()
        leaves = [str(x).strip() for x in (card.get("leaves") or []) if str(x).strip()]
    else:
        text = (card or "").strip()
        leaves = []
    if not text and not leaves:
        return None
    # Prefer the leaf split; fall back to innerText lines.
    lines = leaves or [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None

    title = lines[0]
    company = lines[1] if len(lines) >= 3 else None
    tail = lines[-1] if len(lines) > 1 else ""

    location = None
    posted = None
    if "·" in tail:
        location_part, _, date_part = tail.partition("·")
        location = location_part.strip() or None
        date_part = date_part.strip()
        try:
            stamp = datetime.strptime(date_part, "%b %d").replace(year=datetime.now(timezone.utc).year)
            posted = stamp.replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            posted = None
    elif len(lines) >= 3:
        location = tail or None

    return {
        "external_id": slug_id(urlparse(url).path),
        "title": title,
        "company": company,
        "location": location,
        "cities": None,
        "countries": None,
        "country": None,
        "remote": "remote" in text.lower(),
        "employment_type": None,
        "salary_min": None,
        "salary_max": None,
        "salary_currency": None,
        "salary_period": None,
        "posted_at": posted,
        "description": None,
        "apply_url": None,
        "source_url": url,
        "source": "hiringcafe",
    }


def in_window(posted_at: Optional[str], cutoff: Optional[datetime]) -> bool:
    if cutoff is None:
        return True
    if not posted_at:
        return True
    try:
        stamp = datetime.fromisoformat(str(posted_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp >= cutoff


async def dump_debug(page: Any, label: str) -> None:
    """Persist what the browser actually saw, so a failed run is diagnosable."""
    try:
        html = await page.content()
        text = (await page.inner_text("body"))[:800] if await page.query_selector("body") else ""
        Actor.log.warning(f"{label} page title: {(await page.title())[:120]}")
        Actor.log.warning(f"{label} page body: {text[:400]!r}")
        await Actor.set_value(f"debug-{label}.html", html, content_type="text/html")
        png = await page.screenshot(full_page=False)
        await Actor.set_value(f"debug-{label}.png", png, content_type="image/png")
    except Exception as e:
        Actor.log.warning(f"{label} debug dump failed: {type(e).__name__}: {e}")


async def looks_blocked(page: Any) -> bool:
    try:
        title = (await page.title() or "").lower()
        body = (await page.inner_text("body"))[:600].lower()
    except Exception:
        return True
    haystack = title + " " + body
    return any(marker in haystack for marker in CHALLENGE_MARKERS)


HARD_BLOCK_MARKERS = ("you have been blocked", "attention required", "unable to access")


async def hard_blocked(page: Any) -> bool:
    """True for a WAF firewall block — waiting cannot fix it, only a new IP can."""
    try:
        title = (await page.title() or "").lower()
        body = (await page.inner_text("body"))[:800].lower() if await page.query_selector("body") else ""
    except Exception:
        return False
    haystack = title + " " + body
    return any(marker in haystack for marker in HARD_BLOCK_MARKERS)


async def goto_clear(page: Any, url: str, label: str, attempts: int = 3) -> bool:
    """Navigate and wait out the Cloudflare interstitial. False if it never clears."""
    for attempt in range(attempts):
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
            Actor.log.info(f"{label}: HTTP {response.status if response else '?'} on attempt {attempt + 1}")
        except Exception as e:
            Actor.log.warning(f"{label}: navigation failed ({type(e).__name__})")
            await page.wait_for_timeout(3_000 * (attempt + 1))
            continue

        if await hard_blocked(page):
            Actor.log.warning(f"{label}: hard Cloudflare block on this IP — giving up on it")
            return False

        for _ in range(12):
            await page.wait_for_timeout(2_500)
            if not await looks_blocked(page):
                return True

        Actor.log.warning(f"{label}: still challenged by Cloudflare (attempt {attempt + 1}/{attempts})")
        await page.wait_for_timeout(4_000 * (attempt + 1))
    return False


PAGE_SCRIPT = """
() => {
  const ld = [...document.querySelectorAll('script[type="application/ld+json"]')]
    .map(s => { try { return JSON.parse(s.textContent); } catch (e) { return null; } })
    .filter(Boolean);
  const flatten = (v) => Array.isArray(v) ? v.flatMap(flatten) : [v];
  const nodes = ld.flatMap(flatten);
  const og = document.querySelector('meta[property="og:title"]');
  const main = document.querySelector('main') || document.body;
  return {
    url: location.href,
    title: document.title,
    og_title: og ? og.getAttribute('content') : null,
    h1: document.querySelector('h1') ? document.querySelector('h1').innerText : null,
    text: (main.innerText || '').slice(0, 30000),
    external_links: [...document.querySelectorAll('a')]
      .map(a => a.href)
      .filter(h => h && !h.includes('hiringcafe.com'))
      .slice(0, 40),
    ld_json: nodes.filter(n => n && (n['@type'] === 'JobPosting' ||
      (Array.isArray(n['@type']) && n['@type'].includes('JobPosting')))),
  };
}
"""


REAL_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

STEALTH_INIT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = window.chrome || { runtime: {} };
"""


async def open_page(pw: Any, proxy: Optional[Dict[str, Any]], headless: bool) -> Any:
    # `channel="chrome"` uses the real Chrome build shipped in the base image:
    # Playwright's bundled Chromium is fingerprinted and hard-blocked by
    # Cloudflare (403 "Sorry, you have been blocked"). The image runs under
    # xvfb, so headful is available even with no display — headful is the
    # variant Cloudflare actually lets through.
    browser = await pw.chromium.launch(
        channel="chrome",
        headless=headless,
        proxy=proxy,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1440,900",
        ],
    )
    context = await browser.new_context(
        locale="en-US",
        timezone_id="Africa/Lagos",
        viewport={"width": 1440, "height": 900},
        user_agent=REAL_CHROME_UA,
        extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
    )
    await context.add_init_script(STEALTH_INIT)
    return browser, context


async def proxy_inputs(actor_input: Dict[str, Any]) -> Any:
    """Build the Apify proxy configuration once; each new_url() call gets a fresh IP."""
    explicit = actor_input.get("proxyConfiguration")
    if isinstance(explicit, dict) and explicit:
        return await Actor.create_proxy_configuration(actor_proxy_input=explicit)
    groups = actor_input.get("proxyGroups") or DEFAULT_PROXY_GROUPS
    if isinstance(groups, str):
        groups = [groups]
    Actor.log.info(f"Using Apify Proxy groups: {groups}")
    return await Actor.create_proxy_configuration(groups=groups)


async def proxy_kwargs(config: Any) -> Optional[Dict[str, Any]]:
    """Resolve one proxy session into Playwright launch kwargs."""
    if not config:
        return None
    proxy_url = await config.new_url()
    scheme, rest = proxy_url.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    username, _, password = creds.partition(":")
    return {"server": f"{scheme}://{host}", "username": username, "password": password}


async def collect_cards(
    listing_page: Any,
    max_scrolls: int,
    query_words: List[str],
    max_jobs: int,
) -> Dict[str, Dict[str, Any]]:
    """Scroll the recently-posted list and collect /job/ links with their card data."""
    cards: Dict[str, Dict[str, Any]] = {}
    for _ in range(max_scrolls + 1):
        found = await listing_page.eval_on_selector_all(
            'a[href*="/job/"]',
            """els => els.map(e => ({
                href: e.href,
                text: (e.innerText || '').trim(),
                // innerText collapses the card's stacked labels into one run-on
                // string; the leaf elements keep title / company / location apart.
                leaves: [...e.querySelectorAll('*')]
                    .filter(x => x.children.length === 0)
                    .map(x => (x.innerText || '').trim())
                    .filter(Boolean)
                    .slice(0, 8),
            }))""",
        )
        for card in found:
            href = card.get("href")
            if not href:
                continue
            if not _JOB_PATH_RE.match(urlparse(href).path):
                continue
            if href in cards:
                continue
            if query_words and not any(
                word in (card.get("text") or "").lower() for word in query_words
            ):
                continue
            cards[href] = {"text": card.get("text") or "", "leaves": card.get("leaves") or []}

        if len(cards) >= max_jobs * 2:
            break
        await listing_page.mouse.wheel(0, 5000)
        await listing_page.wait_for_timeout(2_000)
    return cards


async def main() -> None:
    # Patchright is a patched Playwright build that removes the CDP/automation
    # leaks Cloudflare's managed challenge fingerprints. It is a drop-in import;
    # plain Playwright stays as the fallback so the actor still builds without it.
    try:
        from patchright.async_api import async_playwright
        Actor.log.info("Using Patchright (stealth Playwright build)")
    except ImportError:
        from playwright.async_api import async_playwright
        Actor.log.info("Using plain Playwright (Patchright unavailable)")

    async with Actor:
        actor_input = await Actor.get_input() or {}
        queries = actor_input.get("queries") or []
        if isinstance(queries, str):
            queries = [queries]
        max_jobs = int(actor_input.get("maxJobs", 50))
        time_filter = str(actor_input.get("timeFilter", "past_week"))
        max_scrolls = int(actor_input.get("maxScrolls", 6))
        headless = bool(actor_input.get("headless", False))
        delay = float(actor_input.get("requestDelaySecs", 3.0))
        fetch_details = bool(actor_input.get("fetchDetails", False))

        delta = TIME_FILTERS.get(time_filter, TIME_FILTERS["past_week"])
        cutoff = datetime.now(timezone.utc) - delta if delta else None

        listing_attempts = int(actor_input.get("listingAttempts", 3))
        try:
            config = await proxy_inputs(actor_input)
        except Exception as e:
            Actor.log.warning(f"Proxy setup failed ({e}) — running without proxy (Cloudflare will very likely block)")
            config = None

        Actor.log.info(
            f"Starting HiringCafe Scraper: queries={queries or 'all'}, maxJobs={max_jobs}, "
            f"timeFilter={time_filter}, proxy={'yes' if config else 'no'}, "
            f"headless={headless}, listingAttempts={listing_attempts}"
        )

        # Diagnostics mode: report HTTP status/title/snippet per URL and stop.
        # Useful when Cloudflare changes which paths it blocks.
        diagnostic_urls = actor_input.get("diagnostics") or []
        if isinstance(diagnostic_urls, str):
            diagnostic_urls = [diagnostic_urls]
        if diagnostic_urls:
            async with async_playwright() as pw:
                proxy = await proxy_kwargs(config)
                browser, context = await open_page(pw, proxy, headless)
                page = await context.new_page()
                for index, url in enumerate(diagnostic_urls):
                    try:
                        response = await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                        await page.wait_for_timeout(4_000)
                        title = await page.title()
                        body = (await page.inner_text("body"))[:200] if await page.query_selector("body") else ""
                        Actor.log.info(
                            f"DIAG {url} -> HTTP {response.status if response else '?'} | "
                            f"title={title[:80]!r} | body={body[:120]!r}"
                        )
                        await dump_debug(page, f"diag-{index}")
                    except Exception as e:
                        Actor.log.warning(f"DIAG {url} failed: {type(e).__name__}: {e}")
                await browser.close()
            return

        harvested = 0
        blocked = 0
        seen: set = set()
        query_words = [q.lower() for q in queries if q and q.strip()]

        async with async_playwright() as pw:
            cards: Dict[str, str] = {}
            browser = None
            context = None
            listing_page = None

            # A Cloudflare WAF block burns the IP, so each attempt gets a fresh
            # proxy session and a fresh browser: waiting on a blocked IP is futile.
            for attempt in range(1, listing_attempts + 1):
                proxy = await proxy_kwargs(config)
                if browser is not None:
                    await browser.close()
                browser, context = await open_page(pw, proxy, headless)
                listing_page = await context.new_page()

                if not await goto_clear(listing_page, RECENT_URL, f"recently-posted#{attempt}"):
                    await dump_debug(listing_page, f"listing-attempt{attempt}")
                    continue

                try:
                    await listing_page.wait_for_selector('a[href*="/job/"]', timeout=45_000)
                except Exception:
                    await dump_debug(listing_page, f"listing-attempt{attempt}")
                    continue

                cards = await collect_cards(listing_page, max_scrolls, query_words, max_jobs)
                if cards:
                    Actor.log.info(f"listing: {len(cards)} candidate postings (attempt {attempt})")
                    break

                Actor.log.warning(f"attempt {attempt}: page loaded but produced no cards")
                await dump_debug(listing_page, f"listing-attempt{attempt}")

            if not cards:
                Actor.log.error(
                    f"Could not obtain any postings after {listing_attempts} proxy sessions — "
                    "try proxyGroups ['RESIDENTIAL'], ['UNBLOCKER'] or ['BUYPROXIES94952']"
                )
                if browser is not None:
                    await browser.close()
                return

            try:
                detail_page = await context.new_page() if fetch_details else None
                for url, card in cards.items():
                    if harvested >= max_jobs:
                        break

                    # Cloudflare currently hard-blocks /job/* and /jobs/* even for
                    # a real Chrome + residential proxy (verified 2026-11-25), so
                    # detail fetching is opt-in: the listing alone gives title,
                    # company, location, date and URL, which is honest and cheap.
                    detail_ok = False
                    if fetch_details and detail_page is not None:
                        detail_ok = await goto_clear(
                            detail_page, url, f"job {slug_id(urlparse(url).path)}", attempts=1
                        )
                    if not detail_ok:
                        if fetch_details:
                            blocked += 1
                            if blocked == 1:
                                await dump_debug(detail_page, "detail-blocked")
                        record = card_record(url, card)
                        if not record:
                            continue
                        record["detail_blocked"] = bool(fetch_details)
                    else:
                        page_data = await detail_page.evaluate(PAGE_SCRIPT)
                        postings = page_data.get("ld_json") or []
                        if postings:
                            record = record_from_jsonld(postings[0], url)
                        else:
                            record = record_from_dom(page_data, (card or {}).get("text") if isinstance(card, dict) else card)
                        record["detail_blocked"] = False

                    if not record.get("title"):
                        Actor.log.warning(f"skipping {url}: no title could be extracted")
                        continue
                    if not in_window(record.get("posted_at"), cutoff):
                        continue
                    if record.get("external_id") in seen:
                        continue

                    seen.add(record.get("external_id"))
                    record["description"] = (record.get("description") or "")[:40_000] or None
                    record["card_text"] = (card or {}).get("text") if isinstance(card, dict) else (card or None)
                    record["scraped_at"] = datetime.now(timezone.utc).isoformat()

                    await Actor.push_data(record)
                    harvested += 1
                    Actor.log.info(
                        f"[{harvested}/{max_jobs}] Saved: {record.get('title')} @ "
                        f"{record.get('company') or '?'} "
                        f"({record.get('location') or record.get('country') or '?'})"
                    )
                    if detail_page is not None:
                        await detail_page.wait_for_timeout(delay * 1000)

                Actor.log.info(
                    f"HiringCafe scrape complete! Jobs harvested: {harvested}, "
                    f"pages blocked: {blocked}"
                )
            finally:
                await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
