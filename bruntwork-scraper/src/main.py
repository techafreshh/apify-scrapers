"""BruntWork careers scraper.

bruntworkcareers.co is a Next.js App Router site with no ``__NEXT_DATA__``, no
JSON-LD, no ``jobs.json`` feed and no third-party ATS. What it *does* have is
server-rendered HTML for both pages that matter, so this Actor is plain HTTP:

  * ``/jobs``             — one card per open role: numeric id, title, commitment.
  * ``/jobs/<id>``        — the full description inside
                            ``<div class="text-gray-600 job-description">`` and an
                            apply link at ``/jobs/<id>/apply``.

What the site does *not* expose: posting dates, salary, or a structured
location. BruntWork is a remote-staffing (BPO) employer, so every role in this
inventory is remote; ``location`` is reported as "Remote" and a timezone hint is
pulled from the description's schedule line when one is present. With no dates
there is nothing to filter on, so there is no ``timeFilter`` input — an output
field that was always null would be worse than an honest absence.
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

import httpx
from apify import Actor
from bs4 import BeautifulSoup

BASE = "https://bruntworkcareers.co"
LISTING = f"{BASE}/jobs"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_JOB_HREF_RE = re.compile(r"^/jobs/(\d+)/?$")
_EMPLOYMENT_RE = re.compile(
    r"(Full[ -]?Time|Part[ -]?Time|Project Based|Contract|Freelance|Temporary|Internship)[^<]*",
    re.IGNORECASE,
)

# "Schedule: Monday to Friday, 9:00 am to 6:00 pm (Central European Time - Amsterdam, Netherlands, 40 hours per week)"
_TIMEZONE_RE = re.compile(
    r"\(([A-Za-z][A-Za-z .+-]{2,40}Time[^)]{0,120})\)", re.IGNORECASE
)


def parse_listing(html: str) -> List[Dict[str, Any]]:
    """Collect one entry per role card on /jobs."""
    soup = BeautifulSoup(html, "html.parser")
    roles: List[Dict[str, Any]] = []
    seen: set = set()

    for a in soup.find_all("a", href=True):
        match = _JOB_HREF_RE.match(a["href"].split("?")[0])
        if not match:
            continue
        job_id = match.group(1)
        if job_id in seen:
            continue

        paragraphs = [p.get_text(" ", strip=True) for p in a.find_all("p")]
        paragraphs = [p for p in paragraphs if p]
        if not paragraphs:
            continue

        title = paragraphs[0]
        commitment = None
        for text in paragraphs[1:]:
            found = _EMPLOYMENT_RE.search(text)
            if found:
                commitment = text
                break
        if not commitment:
            # Some cards render the commitment outside <p> (icon + text div).
            found = _EMPLOYMENT_RE.search(a.get_text(" ", strip=True))
            if found:
                commitment = found.group(0).strip()

        seen.add(job_id)
        roles.append(
            {
                "external_id": job_id,
                "title": title,
                "commitment": commitment,
                "source_url": f"{BASE}/jobs/{job_id}",
            }
        )
    return roles


def strip_html(fragment: str) -> str:
    return BeautifulSoup(fragment, "html.parser").get_text("\n", strip=True)


def parse_detail(html: str) -> Dict[str, Any]:
    """Extract description, apply URL and a timezone hint from a role page."""
    soup = BeautifulSoup(html, "html.parser")
    out: Dict[str, Any] = {
        "description": None,
        "description_html": None,
        "apply_url": None,
        "timezone_hint": None,
    }

    container = soup.find("div", class_="job-description")
    if container is not None:
        out["description_html"] = str(container)
        out["description"] = container.get_text("\n", strip=True)

    apply_link = soup.find("a", href=re.compile(r"^/jobs/\d+/apply"))
    if apply_link is not None:
        out["apply_url"] = urljoin(BASE, apply_link["href"])

    text = soup.get_text(" ", strip=True)
    match = _TIMEZONE_RE.search(text)
    if match:
        out["timezone_hint"] = match.group(1).strip()

    if not out["description"]:
        # Some renderings omit the wrapper class; fall back to the longest text block.
        blocks = [strip_html(str(div)) for div in soup.find_all("div")]
        blocks = [b for b in blocks if len(b) > 400]
        if blocks:
            out["description"] = max(blocks, key=len)

    return out


async def get(client: httpx.AsyncClient, url: str, label: str) -> Optional[str]:
    """GET with a short retry ladder; returns HTML or None."""
    for attempt in range(3):
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.text
            if resp.status_code in (429, 500, 502, 503, 504):
                Actor.log.warning(f"{label} -> HTTP {resp.status_code}, retrying")
            else:
                Actor.log.warning(f"{label} -> HTTP {resp.status_code}")
                return None
        except httpx.HTTPError as e:
            Actor.log.warning(f"{label} failed ({type(e).__name__}), retrying")
        await asyncio.sleep(2 * (attempt + 1))
    return None


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        max_jobs = int(actor_input.get("maxJobs", 50))
        fetch_descriptions = bool(actor_input.get("fetchDescriptions", True))
        request_delay = float(actor_input.get("requestDelaySecs", 1.0))

        Actor.log.info(
            f"Starting BruntWork Careers Scraper: maxJobs={max_jobs}, "
            f"fetchDescriptions={fetch_descriptions}"
        )

        harvested = 0
        async with httpx.AsyncClient(headers=HEADERS, timeout=30.0, follow_redirects=True) as client:
            listing = await get(client, LISTING, "listing")
            if not listing:
                Actor.log.error("Could not load the jobs listing — run harvested 0 rows")
                return

            roles = parse_listing(listing)
            Actor.log.info(f"listing: {len(roles)} role cards")
            if not roles:
                Actor.log.error("No role cards parsed — the site markup probably changed")
                return

            for role in roles:
                if harvested >= max_jobs:
                    break

                record: Dict[str, Any] = {
                    "external_id": role["external_id"],
                    "title": role["title"],
                    "company": "BruntWork",
                    "location": "Remote",
                    "remote": True,
                    "employment_type": role["commitment"],
                    "timezone_hint": None,
                    "posted_at": None,
                    "description": None,
                    "apply_url": f"{BASE}/jobs/{role['external_id']}/apply",
                    "source_url": role["source_url"],
                    "source": "bruntwork_careers",
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                }

                if fetch_descriptions:
                    detail = await get(client, role["source_url"], f"job {role['external_id']}")
                    if detail:
                        parsed = parse_detail(detail)
                        record.update(
                            {
                                "description": parsed["description"],
                                "description_html": parsed["description_html"],
                                "apply_url": parsed["apply_url"] or record["apply_url"],
                                "timezone_hint": parsed["timezone_hint"],
                            }
                        )
                    await asyncio.sleep(request_delay)

                await Actor.push_data(record)
                harvested += 1
                Actor.log.info(
                    f"[{harvested}/{max_jobs}] Saved: {record['title']} "
                    f"({record['employment_type'] or 'type unknown'})"
                )

        Actor.log.info(f"BruntWork scrape complete! Roles harvested: {harvested}")


if __name__ == "__main__":
    asyncio.run(main())
