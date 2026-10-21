"""BetaList startup scraper.

BetaList is a Rails app that server-renders everything and sits behind no bot
protection, so this Actor is plain HTTP — no browser, no proxy, no per-GB cost.

Two phases:
  1. Discovery — walk /search?q=<term>&page=N and harvest /startups/<slug> links
     (~21 per page).
  2. Detail — read each profile for name, tagline, description and categories,
     then resolve the real website from the /startups/<slug>/visit redirect.

The website matters more than it looks: it yields the registrable domain, which
is the strongest key for merging one startup across BetaList, Product Hunt and
funding news.
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import httpx
from apify import Actor
from bs4 import BeautifulSoup

BASE = "https://www.betalist.com"
# The canonical host answers /visit with a single 301 straight to the target site,
# whereas the www host adds an extra canonicalization hop.
VISIT_BASE = "https://betalist.com"

DEFAULT_QUERIES = ["ai", "automation", "saas", "developer", "marketing", "productivity"]

MULTI_PART_TLDS = {
    "co.uk", "org.uk", "ac.uk", "com.au", "net.au", "org.au", "co.nz", "co.jp",
    "co.in", "co.za", "com.br", "com.mx", "com.sg", "com.tr", "co.kr",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_STARTUP_HREF_RE = re.compile(r"^/startups/([A-Za-z0-9][\w\-\.]*)/?$")


def registrable_domain(url: str) -> str:
    """Reduce a URL to a comparable registrable domain ('https://a.b.example.co.uk/x' -> 'example.co.uk')."""
    if not url:
        return ""
    raw = url.strip()
    if "//" not in raw:
        raw = "//" + raw
    try:
        host = (urlparse(raw).hostname or "").lower()
    except ValueError:
        return ""
    if not host or "." not in host:
        return ""
    host = host[4:] if host.startswith("www.") else host
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in MULTI_PART_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def clean_redirect(location: str) -> str:
    """Strip BetaList's ?ref=betalist and any fragment from a /visit redirect target."""
    if not location:
        return ""
    try:
        p = urlparse(location)
        return urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
    except ValueError:
        return location.split("?")[0]


def harvest_slugs(html: str) -> List[str]:
    """Collect unique /startups/<slug> slugs from a search results page."""
    soup = BeautifulSoup(html, "html.parser")
    out: List[str] = []
    seen: set = set()
    for a in soup.find_all("a", href=True):
        m = _STARTUP_HREF_RE.match(a["href"].split("?")[0])
        if not m:
            continue
        slug = m.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        out.append(slug)
    return out


_BOOSTED_RE = re.compile(r"\bBOOSTED\b", re.IGNORECASE)


def parse_startup(html: str, slug: str, query: str) -> Optional[Dict[str, Any]]:
    """Extract a startup record from a BetaList profile page."""
    soup = BeautifulSoup(html, "html.parser")

    h1 = soup.find("h1")
    raw_name = h1.get_text(" ", strip=True) if h1 else ""
    if not raw_name:
        return None

    # The Boosted badge is rendered inside the h1, so strip it out of the name.
    boosted = bool(_BOOSTED_RE.search(raw_name))
    name = _BOOSTED_RE.sub("", raw_name).strip() or raw_name

    tagline = None
    if h1 is not None:
        # The tagline is the sibling heading immediately after the name.
        sib = h1.find_next_sibling()
        if sib is not None and sib.name in ("h2", "h3", "p"):
            tagline = sib.get_text(" ", strip=True)

    description = None
    for p in soup.find_all("p"):
        text = p.get_text(" ", strip=True)
        if len(text) > 60 and text != tagline:
            description = text
            break

    categories: List[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/browse/") and len(href.strip("/").split("/")) == 2:
            label = a.get_text(" ", strip=True)
            if label and label not in categories:
                categories.append(label)

    boosted = boosted or bool(soup.find(string=re.compile(r"BOOSTED", re.IGNORECASE)))

    return {
        "name": name,
        "slug": slug,
        "url": f"{BASE}/startups/{slug}",
        "tagline": tagline,
        "description": description,
        "categories": categories or None,
        "boosted": boosted,
        "queries": [query] if query else None,
        "source": "betalist_public",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


async def resolve_website(client: httpx.AsyncClient, slug: str) -> Tuple[str, str]:
    """Return (website_url, domain) from the /visit redirect.

    Uses allow_redirects=False so we never actually contact the target site —
    the Location header alone carries the domain.
    """
    try:
        resp = await client.get(
            f"{VISIT_BASE}/startups/{slug}/visit", timeout=20.0, follow_redirects=False
        )
        if resp.status_code in (301, 302, 303, 307, 308):
            site = clean_redirect(resp.headers.get("location", ""))
            return site, registrable_domain(site)
    except Exception as e:
        Actor.log.debug(f"visit redirect failed for {slug}: {e}")
    return "", ""


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        queries = actor_input.get("queries") or DEFAULT_QUERIES
        if isinstance(queries, str):
            queries = [queries]
        max_pages = int(actor_input.get("maxPagesPerQuery", 2))
        max_startups = int(actor_input.get("maxStartups", 60))
        fetch_website = bool(actor_input.get("fetchWebsite", True))

        Actor.log.info(
            f"Starting BetaList Scraper: queries={queries}, maxPages={max_pages}, "
            f"maxStartups={max_startups}, fetchWebsite={fetch_website}"
        )

        proxy_url = None
        explicit = actor_input.get("proxyConfiguration")
        try:
            if isinstance(explicit, dict) and explicit:
                cfg = await Actor.create_proxy_configuration(actor_proxy_input=explicit)
            elif actor_input.get("useProxy", False):
                cfg = await Actor.create_proxy_configuration()
            else:
                cfg = None
            if cfg:
                proxy_url = await cfg.new_url()
                Actor.log.info("Using Apify Proxy")
        except Exception as e:
            Actor.log.warning(f"Proxy setup skipped: {e}. Running direct.")

        harvested = 0
        seen: set = set()

        async with httpx.AsyncClient(
            proxy=proxy_url,
            headers=HEADERS,
            timeout=25.0,
            follow_redirects=True,
        ) as client:
            for query in queries:
                if harvested >= max_startups:
                    break
                for page_no in range(1, max_pages + 1):
                    if harvested >= max_startups:
                        break
                    search_url = f"{BASE}/search?q={query}&page={page_no}"
                    try:
                        resp = await client.get(search_url)
                        if resp.status_code != 200:
                            Actor.log.warning(
                                f"search '{query}' page {page_no} -> {resp.status_code}"
                            )
                            break
                        slugs = harvest_slugs(resp.text)
                    except Exception as e:
                        Actor.log.warning(f"search '{query}' failed: {e}")
                        break

                    Actor.log.info(
                        f"query '{query}' page {page_no} -> {len(slugs)} slugs"
                    )
                    if not slugs:
                        break

                    for slug in slugs:
                        if harvested >= max_startups:
                            break
                        if slug in seen:
                            continue
                        seen.add(slug)

                        try:
                            detail = await client.get(f"{BASE}/startups/{slug}")
                            if detail.status_code != 200:
                                continue
                            record = parse_startup(detail.text, slug, query)
                        except Exception as e:
                            Actor.log.warning(f"detail failed for {slug}: {e}")
                            continue

                        if not record:
                            continue

                        if fetch_website:
                            site, domain = await resolve_website(client, slug)
                            record["website"] = site or None
                            record["domain"] = domain or None
                            await asyncio.sleep(0.15)

                        await Actor.push_data(record)
                        harvested += 1
                        Actor.log.info(
                            f"[{harvested}/{max_startups}] Saved: {record['name']}"
                            + (f" — {record['domain']}" if record.get("domain") else "")
                        )
                        await asyncio.sleep(0.25)

        Actor.log.info(f"BetaList scrape complete! Startups harvested: {harvested}")


if __name__ == "__main__":
    asyncio.run(main())
