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


async def looks_blocked(page: Any) -> bool:
    try:
        title = (await page.title() or "").lower()
        body = (await page.inner_text("body"))[:600].lower()
    except Exception:
        return True
    haystack = title + " " + body
    return any(marker in haystack for marker in CHALLENGE_MARKERS)


async def goto_clear(page: Any, url: str, label: str, attempts: int = 3) -> bool:
    """Navigate and wait out the Cloudflare interstitial. False if it never clears."""
    for attempt in range(attempts):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        except Exception as e:
            Actor.log.warning(f"{label}: navigation failed ({type(e).__name__})")
            await page.wait_for_timeout(3_000 * (attempt + 1))
            continue

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


async def open_page(pw: Any, proxy: Optional[Dict[str, Any]], headless: bool) -> Any:
    browser = await pw.chromium.launch(
        headless=headless,
        proxy=proxy,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ],
    )
    context = await browser.new_context(
        locale="en-US",
        timezone_id="Africa/Lagos",
        viewport={"width": 1440, "height": 900},
    )
    return browser, context


async def main() -> None:
    from playwright.async_api import async_playwright

    async with Actor:
        actor_input = await Actor.get_input() or {}
        queries = actor_input.get("queries") or []
        if isinstance(queries, str):
            queries = [queries]
        max_jobs = int(actor_input.get("maxJobs", 50))
        time_filter = str(actor_input.get("timeFilter", "past_week"))
        max_scrolls = int(actor_input.get("maxScrolls", 6))
        headless = bool(actor_input.get("headless", True))
        delay = float(actor_input.get("requestDelaySecs", 3.0))

        delta = TIME_FILTERS.get(time_filter, TIME_FILTERS["past_week"])
        cutoff = datetime.now(timezone.utc) - delta if delta else None

        proxy: Optional[Dict[str, Any]] = None
        try:
            explicit = actor_input.get("proxyConfiguration")
            if isinstance(explicit, dict) and explicit:
                config = await Actor.create_proxy_configuration(actor_proxy_input=explicit)
            else:
                groups = actor_input.get("proxyGroups") or DEFAULT_PROXY_GROUPS
                if isinstance(groups, str):
                    groups = [groups]
                config = await Actor.create_proxy_configuration(groups=groups)
                Actor.log.info(f"Using Apify Proxy groups: {groups}")
            if config:
                proxy_url = await config.new_url()
                scheme, rest = proxy_url.split("://", 1)
                creds, host = rest.rsplit("@", 1)
                username, _, password = creds.partition(":")
                proxy = {"server": f"{scheme}://{host}", "username": username, "password": password}
        except Exception as e:
            Actor.log.warning(f"Proxy setup failed ({e}) — running without proxy (Cloudflare will very likely block)")

        Actor.log.info(
            f"Starting HiringCafe Scraper: queries={queries or 'all'}, maxJobs={max_jobs}, "
            f"timeFilter={time_filter}, proxy={'yes' if proxy else 'no'}, headless={headless}"
        )

        harvested = 0
        blocked = 0
        seen: set = set()

        async with async_playwright() as pw:
            browser, context = await open_page(pw, proxy, headless)
            listing_page = await context.new_page()

            try:
                if not await goto_clear(listing_page, RECENT_URL, "recently-posted"):
                    Actor.log.error("Cloudflare never cleared the listing page — 0 rows this run")
                    return

                try:
                    await listing_page.wait_for_selector('a[href*="/job/"]', timeout=45_000)
                except Exception:
                    Actor.log.error("Listing loaded but no /job/ links appeared — 0 rows this run")
                    return

                cards: Dict[str, str] = {}
                query_words = [q.lower() for q in queries if q and q.strip()]

                for _ in range(max_scrolls):
                    found = await listing_page.eval_on_selector_all(
                        'a[href*="/job/"]',
                        """els => els.map(e => ({
                            href: e.href,
                            text: (e.innerText || '').trim(),
                        }))""",
                    )
                    for card in found:
                        href = card.get("href")
                        if not href:
                            continue
                        path = urlparse(href).path
                        if not _JOB_PATH_RE.match(path):
                            continue
                        if href in cards:
                            continue
                        if query_words and not any(
                            word in (card.get("text") or "").lower() for word in query_words
                        ):
                            continue
                        cards[href] = card.get("text") or ""

                    if len(cards) >= max_jobs * 2:
                        break
                    await listing_page.mouse.wheel(0, 5000)
                    await listing_page.wait_for_timeout(2_000)

                Actor.log.info(f"listing: {len(cards)} candidate postings")

                detail_page = await context.new_page()
                for url, card_text in cards.items():
                    if harvested >= max_jobs:
                        break

                    if not await goto_clear(detail_page, url, f"job {slug_id(urlparse(url).path)}"):
                        blocked += 1
                        continue

                    page_data = await detail_page.evaluate(PAGE_SCRIPT)
                    postings = page_data.get("ld_json") or []
                    if postings:
                        record = record_from_jsonld(postings[0], url)
                    else:
                        record = record_from_dom(page_data, card_text)

                    if not record.get("title"):
                        Actor.log.warning(f"skipping {url}: no title could be extracted")
                        continue
                    if not in_window(record.get("posted_at"), cutoff):
                        continue
                    if record.get("external_id") in seen:
                        continue

                    seen.add(record.get("external_id"))
                    record["description"] = (record.get("description") or "")[:40_000] or None
                    record["card_text"] = card_text or None
                    record["scraped_at"] = datetime.now(timezone.utc).isoformat()

                    await Actor.push_data(record)
                    harvested += 1
                    Actor.log.info(
                        f"[{harvested}/{max_jobs}] Saved: {record.get('title')} @ "
                        f"{record.get('company') or '?'} ({record.get('country') or '?'})"
                    )
                    await detail_page.wait_for_timeout(delay * 1000)

                Actor.log.info(
                    f"HiringCafe scrape complete! Jobs harvested: {harvested}, "
                    f"pages blocked: {blocked}"
                )
            finally:
                await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
