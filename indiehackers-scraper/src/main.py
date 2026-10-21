"""Indie Hackers product scraper.

Indie Hackers sits behind Cloudflare bot management: plain HTTP clients get a
"Just a moment..." interstitial (403) while a real browser passes. This actor
therefore drives Playwright/Chromium and optionally routes through an Apify
residential proxy.

Two phases:
  1. Directory pass  — read /products?sorting=<x> and harvest /product/<slug> links.
  2. Detail pass     — open each product page and extract name, tagline,
                       website, monthly revenue and the metrics strip.
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from apify import Actor

BASE = "https://www.indiehackers.com"
DEFAULT_SORTINGS = ["recently-added", "highest-revenue", "recently-updated"]

# "$5K / mo", "$12,000", "€4.2k/mo" -> normalized USD.
_REVENUE_RE = re.compile(
    r"(?P<cur>[$€£])\s?(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<mult>k|m|million|thousand|b)?",
    re.IGNORECASE,
)
_CURRENCY_USD = {"$": 1.0, "€": 1.08, "£": 1.27}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Runs inside the page context.
HARVEST_JS = """
() => {
    const out = [];
    for (const a of document.querySelectorAll('a[href^="/product/"]')) {
        const href = a.getAttribute('href').split('?')[0].split('#')[0];
        if (href && href !== '/product') out.push(href);
    }
    return Array.from(new Set(out));
}
"""

EXTRACT_JS = """
() => {
    const txt = (el) => (el ? el.textContent.replace(/\\s+/g, ' ').trim() : null);
    const pick = (sel) => document.querySelector(sel);

    const nameEl = pick('h1.product-header__title') || document.querySelector('h1');
    const taglineEl = pick('p.product-header__tagline')
        || pick('span.product-metrics__product-tagline');
    const siteEl = pick('a.product-header__website-link')
        || pick('a.product-metrics__stat--website');
    const revEl = pick('a.product-metrics__stat--revenue')
        || pick('span.product-metrics__stat--revenue');
    const descEl = document.querySelector('meta[name="description"]');

    const metrics = {};
    for (const stat of document.querySelectorAll('div.product-metrics__stat')) {
        const label = stat.querySelector('span.product-metrics__stat-label');
        const value = stat.querySelector('div.product-metrics__stat-value')
            || stat.querySelector('span.product-metrics__stat-number');
        if (label && value) {
            metrics[label.textContent.trim().toLowerCase().replace(/\\s+/g, '_')] =
                value.textContent.replace(/\\s+/g, ' ').trim();
        }
    }

    return {
        name: txt(nameEl),
        tagline: txt(taglineEl),
        website: siteEl ? siteEl.getAttribute('href') : null,
        monthly_revenue: txt(revEl),
        description: descEl ? descEl.getAttribute('content') : null,
        metrics,
        title: document.title,
    };
}
"""


def parse_revenue(text: str) -> Optional[float]:
    """Turn a raw revenue string into a USD number, or None."""
    if not text:
        return None
    m = _REVENUE_RE.search(text)
    if not m:
        return None
    try:
        value = float(m.group("num").replace(",", ""))
    except ValueError:
        return None
    mult = (m.group("mult") or "").lower()
    if mult in ("k", "thousand"):
        value *= 1_000
    elif mult in ("m", "million"):
        value *= 1_000_000
    elif mult == "b":
        value *= 1_000_000_000
    return round(value * _CURRENCY_USD.get(m.group("cur"), 1.0), 2)


def build_record(raw: Dict[str, Any], url: str, sorting: str) -> Optional[Dict[str, Any]]:
    """Normalize extracted DOM data into a dataset record."""
    name = (raw.get("name") or "").strip()
    if not name:
        return None

    revenue_raw = (raw.get("monthly_revenue") or "").strip()
    if revenue_raw.lower().startswith("revenue"):
        revenue_raw = revenue_raw[len("revenue"):].strip()

    website = (raw.get("website") or "").strip()
    if website.startswith("/"):
        website = urljoin(BASE, website)

    return {
        "name": name,
        "slug": url.rstrip("/").rsplit("/", 1)[-1],
        "url": url,
        "website": website or None,
        "tagline": (raw.get("tagline") or None),
        "description": (raw.get("description") or None),
        "monthly_revenue": revenue_raw or None,
        "monthly_revenue_usd": parse_revenue(revenue_raw),
        "metrics": raw.get("metrics") or None,
        "sorting": sorting,
        "source": "indiehackers_browser",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def make_proxy(actor_input: Dict[str, Any]) -> Optional[str]:
    """Resolve a proxy URL from input, or None to run direct."""
    explicit = actor_input.get("proxyConfiguration")
    if isinstance(explicit, dict) and explicit:
        return "@@DEFERRED@@"
    if not actor_input.get("useProxy", True):
        Actor.log.info("Proxy disabled by input — running direct (Cloudflare may block).")
        return None
    groups = actor_input.get("proxyGroups") or ["BUYPROXIES94952"]
    if isinstance(groups, str):
        groups = [groups]
    try:
        cfg = await_proxy_config(explicit, groups)
        if cfg:
            Actor.log.info(f"Using Apify Proxy groups: {groups}")
            return cfg
    except Exception as e:
        Actor.log.warning(f"Proxy setup failed ({e}); running direct.")
    return None


async def await_proxy_config(explicit: Any, groups: List[str]) -> Optional[str]:
    """Create a proxy configuration and return its URL."""
    if isinstance(explicit, dict) and explicit:
        cfg = await Actor.create_proxy_configuration(actor_proxy_input=explicit)
    else:
        cfg = await Actor.create_proxy_configuration(groups=groups)
    if not cfg:
        return None
    return await cfg.new_url()


async def main() -> None:
    # Imported lazily so the parsing helpers stay unit-testable without Playwright
    # installed (Playwright + Chrome come from the Docker image in production).
    from playwright.async_api import async_playwright

    async with Actor:
        actor_input = await Actor.get_input() or {}
        sortings = actor_input.get("sortings") or DEFAULT_SORTINGS
        if isinstance(sortings, str):
            sortings = [sortings]
        max_products = int(actor_input.get("maxProducts", 40))

        Actor.log.info(
            f"Starting Indie Hackers Scraper: sortings={sortings}, maxProducts={max_products}"
        )

        proxy_url = await make_proxy(actor_input)
        launch_kwargs: Dict[str, Any] = {
            "headless": True,
            "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        }
        if proxy_url and proxy_url != "@@DEFERRED@@":
            # Strip credentials out of the URL form Playwright expects.
            server = proxy_url
            if "@" in proxy_url and "://" in proxy_url:
                scheme, rest = proxy_url.split("://", 1)
                creds, host = rest.rsplit("@", 1)
                user, _, pwd = creds.partition(":")
                launch_kwargs["proxy"] = {
                    "server": f"{scheme}://{host}",
                    "username": user,
                    "password": pwd,
                }
            else:
                launch_kwargs["proxy"] = {"server": server}

        harvested = 0
        seen_slugs: set = set()

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(**launch_kwargs)
            context = await browser.new_context(
                user_agent=UA,
                locale="en-US",
                timezone_id="Africa/Lagos",
                viewport={"width": 1440, "height": 900},
            )
            # Hide the most obvious automation tell.
            await context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )
            page = await context.new_page()
            page.set_default_timeout(45000)

            for sorting in sortings:
                if harvested >= max_products:
                    break
                dir_url = f"{BASE}/products?sorting={sorting}"
                try:
                    await page.goto(dir_url, wait_until="domcontentloaded")
                    hrefs = await page.evaluate(HARVEST_JS)
                except Exception as e:
                    Actor.log.warning(f"Directory '{sorting}' failed: {e}")
                    continue

                urls = [urljoin(BASE, h) for h in hrefs]
                Actor.log.info(f"sorting '{sorting}' -> {len(urls)} product links")

                for url in urls:
                    if harvested >= max_products:
                        break
                    slug = url.rstrip("/").rsplit("/", 1)[-1]
                    if not slug or slug in seen_slugs:
                        continue
                    seen_slugs.add(slug)

                    try:
                        await page.goto(url, wait_until="domcontentloaded")
                        raw = await page.evaluate(EXTRACT_JS)
                    except Exception as e:
                        Actor.log.warning(f"Failed {url}: {e}")
                        continue

                    record = build_record(raw or {}, url, sorting)
                    if not record:
                        continue

                    await Actor.push_data(record)
                    harvested += 1
                    Actor.log.info(
                        f"[{harvested}/{max_products}] Saved: {record['name']}"
                        + (f" — {record['monthly_revenue']}" if record["monthly_revenue"] else "")
                    )
                    await asyncio.sleep(0.8)

            await context.close()
            await browser.close()

        Actor.log.info(f"Indie Hackers scrape complete! Products harvested: {harvested}")


if __name__ == "__main__":
    asyncio.run(main())
