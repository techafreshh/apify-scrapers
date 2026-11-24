"""4dayweek.io job scraper.

4dayweek.io exposes a free, unauthenticated JSON API at
``https://4dayweek.io/api/jobs?page=N`` (~25 rows/page, 38k+ rows total), so this
Actor is plain HTTP — no browser, no proxy, no per-GB cost.

Two wrinkles the API has that matter:

* **Early access.** Every row carries ``locked``. Rows inserted after the API's
  ``meta.early_access_cutoff`` (roughly the last two days) are locked: they come
  back with an empty ``company_name`` and ``slug``, which makes them useless.
  ``includeLocked`` defaults to false and those rows are skipped.
* **No server-side filtering.** ``?work_arrangement=remote`` is ignored — every
  query returns the same ``total``. So filtering happens here: paginate, keep
  rows matching ``workArrangement`` / ``regions`` / ``timeFilter``, stop when
  ``maxJobs`` is reached or the feed runs past the time window.

Rows that stay in the dataset get an optional detail pass: the public job page
(``/job/<slug>``) embeds a schema.org ``JobPosting`` JSON-LD block with the full
description, salary and applicant-location requirements. Detail pages are only
read for rows that will be emitted, so locked rows never cost a request.
"""

import asyncio
import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from apify import Actor

BASE = "https://4dayweek.io"
API = f"{BASE}/api/jobs"
PAGE_SIZE = 25

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": f"{BASE}/",
}

TIME_FILTERS = {
    "past_24h": timedelta(days=1),
    "past_week": timedelta(days=7),
    "past_month": timedelta(days=30),
    "any": None,
}


def is_remote(job: Dict[str, Any]) -> bool:
    """A row counts as remote if the job or any of its locations says so."""
    if str(job.get("work_arrangement", "")).lower() == "remote":
        return True
    return any(
        str(loc.get("work_arrangement", "")).lower() == "remote"
        for loc in job.get("locations") or []
        if isinstance(loc, dict)
    )


def primary_location(job: Dict[str, Any]) -> Dict[str, Any]:
    """Pick the location marked primary, else the first one, else an empty dict."""
    locations = [loc for loc in job.get("locations") or [] if isinstance(loc, dict)]
    for loc in locations:
        if loc.get("is_primary"):
            return loc
    return locations[0] if locations else {}


def all_countries(job: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for loc in job.get("locations") or []:
        if not isinstance(loc, dict):
            continue
        country = (loc.get("country") or "").strip()
        if country and country not in out:
            out.append(country)
    return out


def all_continents(job: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for loc in job.get("locations") or []:
        if not isinstance(loc, dict):
            continue
        continent = (loc.get("continent") or "").strip()
        if continent and continent not in out:
            out.append(continent)
    return out


def matches_regions(job: Dict[str, Any], regions: List[str]) -> bool:
    """Case-insensitive match against any country or continent on the row."""
    if not regions:
        return True
    wanted = {r.strip().lower() for r in regions if r and r.strip()}
    values = {c.lower() for c in all_countries(job)} | {c.lower() for c in all_continents(job)}
    return bool(wanted & values)


def matches_work_arrangement(job: Dict[str, Any], work_arrangement: str) -> bool:
    wanted = (work_arrangement or "any").strip().lower()
    if wanted == "any":
        return True
    if wanted == "remote":
        return is_remote(job)
    return str(job.get("work_arrangement", "")).strip().lower() == wanted


def posted_datetime(job: Dict[str, Any]) -> Optional[datetime]:
    """API gives `posted` as epoch seconds; older/exotic rows may use ISO strings."""
    raw = job.get("posted")
    if isinstance(raw, (int, float)) and raw > 0:
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def in_window(job: Dict[str, Any], cutoff: Optional[datetime]) -> bool:
    if cutoff is None:
        return True
    posted = posted_datetime(job)
    # Undated rows are kept rather than silently dropped.
    return posted is None or posted >= cutoff


def job_url(job: Dict[str, Any]) -> str:
    slug = (job.get("slug") or "").strip()
    return f"{BASE}/job/{slug}" if slug else f"{BASE}/jobs"


def base_record(job: Dict[str, Any], salary: Dict[str, Any]) -> Dict[str, Any]:
    """Build the dataset row from an API record."""
    loc = primary_location(job)
    posted = posted_datetime(job)
    record: Dict[str, Any] = {
        "external_id": job.get("id"),
        "title": job.get("title"),
        "company": (job.get("company_name") or "").strip() or None,
        "company_id": job.get("company_id"),
        "country": loc.get("country") or None,
        "continent": loc.get("continent") or None,
        "countries": all_countries(job) or None,
        "continents": all_continents(job) or None,
        "remote": is_remote(job),
        "work_arrangement": job.get("work_arrangement"),
        "schedule_type": job.get("schedule_type"),
        "category": job.get("category"),
        "level": job.get("level"),
        "timezones": job.get("timezones") or None,
        "stack": job.get("stack") or None,
        "salary": salary.get("raw") or None,
        "salary_min": salary.get("min"),
        "salary_max": salary.get("max"),
        "salary_currency": salary.get("currency"),
        "salary_period": salary.get("period"),
        "work_life_score": job.get("work_life_score"),
        "posted_at": posted.isoformat() if posted else None,
        "inserted_at": job.get("inserted"),
        "expired": bool(job.get("is_expired")),
        "locked": bool(job.get("locked")),
        "url": job_url(job),
        "apply_url": None,
        "description": None,
        "source": "4dayweek_api",
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }
    return record


def api_salary(job: Dict[str, Any]) -> Dict[str, Any]:
    """Salary as carried by the API (the cheaper source, refreshed below from JSON-LD)."""
    if job.get("salary_lower") is None and job.get("salary_upper") is None:
        return {}
    return {
        "min": job.get("salary_lower"),
        "max": job.get("salary_upper"),
        "currency": job.get("salary_currency"),
        "period": job.get("salary_period"),
        "raw": job.get("salary"),
    }


def jsonld_job_posting(html: str) -> Optional[Dict[str, Any]]:
    """Pull the first schema.org JobPosting object out of a page's JSON-LD blocks."""
    marker = '<script'
    idx = 0
    while True:
        idx = html.find(marker, idx)
        if idx == -1:
            return None
        end = html.find("</script>", idx)
        if end == -1:
            return None
        block = html[idx:end]
        idx = end + 1
        if "ld+json" not in block.lower():
            continue
        start = block.find(">")
        payload = block[start + 1:] if start != -1 else ""
        payload = payload.strip()
        if not payload:
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


def flight_chunks(html: str) -> List[str]:
    """Concatenate the string payloads of every ``self.__next_f.push([1, "..."])``."""
    decoder = json.JSONDecoder()
    chunks: List[str] = []
    pos = 0
    needle = "self.__next_f.push("
    while True:
        i = html.find(needle, pos)
        if i == -1:
            return chunks
        start = i + len(needle)
        try:
            arr, end = decoder.raw_decode(html[start:])
        except json.JSONDecodeError:
            pos = start + 1
            continue
        if isinstance(arr, list) and len(arr) > 1 and isinstance(arr[1], str):
            chunks.append(arr[1])
        pos = start + end


def flight_rows(flight: str) -> Dict[str, str]:
    """Resolve flight rows into ``{row_id: text}``.

    Pages that are not ``seo_indexable`` ship no JSON-LD at all; the description
    still exists as a React flight text row such as ``9c:T1c08,<markdown>`` that
    the job object references as ``"description":"$9c"``. Text rows carry their
    own hex length, so newlines inside the description parse cleanly.
    """
    decoder = json.JSONDecoder()
    rows: Dict[str, str] = {}
    for match in re.finditer(r"(?:^|\n)([0-9a-f]{1,3}):", flight):
        row_id = match.group(1)
        start = match.end()
        if start < len(flight) and flight[start] == "T":
            comma = flight.find(",", start + 1)
            if comma == -1:
                continue
            try:
                length = int(flight[start + 1:comma], 16)
            except ValueError:
                continue
            rows[row_id] = flight[comma + 1:comma + 1 + length]
        elif start < len(flight) and flight[start] == '"':
            try:
                value, _ = decoder.raw_decode(flight[start:])
            except json.JSONDecodeError:
                continue
            if isinstance(value, str):
                rows[row_id] = value
    return rows


def flight_description(html: str) -> Optional[str]:
    """Description text from the flight payload, preferring the HTML variant."""
    flight = "".join(flight_chunks(html))
    if not flight:
        return None
    rows = flight_rows(flight)
    if not rows:
        return None
    # The job object sits inside a JSON string, so its quotes arrive escaped.
    for key in ("description_html", "description"):
        match = re.search(r'\\"' + key + r'\\":\\"\$([0-9a-f]{1,3})\\"', flight)
        if not match:
            match = re.search(r'"' + key + r'":"\$([0-9a-f]{1,3})"', flight)
        if match and rows.get(match.group(1)):
            return rows[match.group(1)]
    # Last resort: the longest multi-line text row that looks like a job body.
    candidates = [text for text in rows.values() if len(text) > 200 and "\n" in text]
    return max(candidates, key=len) if candidates else None


def meta_description(html: str) -> Optional[str]:
    match = re.search(r'<meta[^>]+(?:property="og:description"|name="description")[^>]+content="([^"]+)"', html)
    if not match:
        return None
    text = match.group(1).strip()
    # The meta copy is truncated with a trailing ellipsis; keep it only if useful.
    return text if len(text) > 40 else None


def money(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def apply_jsonld(record: Dict[str, Any], posting: Dict[str, Any]) -> None:
    """Fold JSON-LD fields into a dataset row (only when they carry real values)."""
    if posting.get("description"):
        record["description"] = posting["description"]
    if posting.get("employmentType"):
        record["employment_type"] = posting["employmentType"]
    if posting.get("industry"):
        record["industry"] = posting["industry"]
    if posting.get("skills"):
        record["skills"] = posting["skills"]
    if posting.get("jobLocationType"):
        record["remote"] = record["remote"] or str(posting["jobLocationType"]).upper() == "TELECOMMUTE"
    if posting.get("datePosted") and not record.get("posted_at"):
        record["posted_at"] = posting["datePosted"]

    org = posting.get("hiringOrganization")
    if isinstance(org, dict):
        if not record.get("company") and org.get("name"):
            record["company"] = org["name"]
        if org.get("sameAs"):
            record["company_url"] = org["sameAs"]

    reqs = posting.get("applicantLocationRequirements")
    if reqs:
        names = [r.get("name") for r in reqs if isinstance(r, dict) and r.get("name")]
        if names:
            record["applicant_location_requirements"] = names

    salary = posting.get("baseSalary")
    if isinstance(salary, dict):
        value = salary.get("value") if isinstance(salary.get("value"), dict) else {}
        record["salary_min"] = money(value.get("minValue")) or record.get("salary_min")
        record["salary_max"] = money(value.get("maxValue")) or record.get("salary_max")
        record["salary_currency"] = salary.get("currency") or record.get("salary_currency")
        record["salary_period"] = value.get("unitText") or record.get("salary_period")
        if record.get("salary_min") or record.get("salary_max"):
            currency = record.get("salary_currency") or ""
            period = (record.get("salary_period") or "").lower()
            low, high = record.get("salary_min"), record.get("salary_max")
            span = f"{low:g}" if low else "?"
            if high and high != low:
                span += f"-{high:g}"
            record["salary"] = " ".join(x for x in (currency, span, period) if x)


async def fetch_page(client: httpx.AsyncClient, page: int) -> Optional[Dict[str, Any]]:
    """GET one API page with retries. The API drops connections under burst load."""
    url = f"{API}?page={page}"
    for attempt in range(3):
        try:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504):
                Actor.log.warning(f"page {page} -> HTTP {resp.status_code}, retrying")
            else:
                Actor.log.warning(f"page {page} -> HTTP {resp.status_code}, giving up")
                return None
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            Actor.log.warning(f"page {page} failed ({type(e).__name__}), retrying")
        await asyncio.sleep(3 * (attempt + 1))
    return None


async def fetch_detail(client: httpx.AsyncClient, record: Dict[str, Any]) -> None:
    """Read the job page and fold its description (and any JSON-LD) into the record."""
    url = record.get("url")
    if not url or "/job/" not in url:
        return
    for attempt in range(2):
        try:
            resp = await client.get(url, headers={**HEADERS, "Accept": "text/html"})
            if resp.status_code == 200:
                html = resp.text
                posting = jsonld_job_posting(html)
                if posting:
                    apply_jsonld(record, posting)
                if not record.get("description"):
                    record["description"] = flight_description(html) or meta_description(html)
                # Anonymous visitors get no apply URL: the payload says so explicitly.
                record["apply_url_withheld"] = bool(
                    re.search(r'apply_url_withheld\\?"?:\\?"?true', html)
                )
                direct = re.search(r'apply_url\\?":\\?"(https?://[^"\\]+)', html)
                if direct:
                    record["apply_url"] = direct.group(1)
                return
            if resp.status_code in (429, 500, 502, 503, 504):
                await asyncio.sleep(3)
                continue
            Actor.log.debug(f"detail {url} -> HTTP {resp.status_code}")
            return
        except httpx.HTTPError as e:
            Actor.log.debug(f"detail {url} failed: {e}")
            await asyncio.sleep(2)


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        max_jobs = int(actor_input.get("maxJobs", 100))
        time_filter = str(actor_input.get("timeFilter", "past_week"))
        work_arrangement = str(actor_input.get("workArrangement", "remote"))
        regions = actor_input.get("regions") or []
        if isinstance(regions, str):
            regions = [regions]
        include_locked = bool(actor_input.get("includeLocked", False))
        fetch_details = bool(actor_input.get("fetchDetails", True))
        max_pages = int(actor_input.get("maxPages", 60))
        request_delay = float(actor_input.get("requestDelaySecs", 1.5))

        delta = TIME_FILTERS.get(time_filter, TIME_FILTERS["past_week"])
        cutoff = datetime.now(timezone.utc) - delta if delta else None

        Actor.log.info(
            f"Starting 4dayweek Scraper: maxJobs={max_jobs}, timeFilter={time_filter}, "
            f"workArrangement={work_arrangement}, regions={regions or 'any'}, "
            f"includeLocked={include_locked}, fetchDetails={fetch_details}, maxPages={max_pages}"
        )

        harvested = 0
        seen: set = set()
        stale_pages = 0

        async with httpx.AsyncClient(
            headers=HEADERS, timeout=30.0, follow_redirects=True
        ) as client:
            for page in range(1, max_pages + 1):
                if harvested >= max_jobs:
                    break

                data = await fetch_page(client, page)
                if data is None:
                    Actor.log.warning(f"stopping: page {page} unavailable after retries")
                    break

                jobs = data.get("jobs") or []
                if not jobs:
                    Actor.log.info(f"page {page}: empty, feed exhausted")
                    break

                page_in_window = 0
                for job in jobs:
                    if harvested >= max_jobs:
                        break
                    if not isinstance(job, dict):
                        continue
                    external_id = job.get("id")
                    if not external_id or external_id in seen:
                        continue
                    if not include_locked and job.get("locked"):
                        continue
                    if job.get("is_expired"):
                        continue
                    if not matches_work_arrangement(job, work_arrangement):
                        continue
                    if not matches_regions(job, regions):
                        continue
                    if not in_window(job, cutoff):
                        continue

                    page_in_window += 1
                    seen.add(external_id)
                    record = base_record(job, api_salary(job))

                    if fetch_details:
                        await fetch_detail(client, record)
                        await asyncio.sleep(request_delay)

                    await Actor.push_data(record)
                    harvested += 1
                    Actor.log.info(
                        f"[{harvested}/{max_jobs}] Saved: {record.get('title')} "
                        f"@ {record.get('company') or 'locked/unknown'} ({record.get('country') or '?'})"
                    )

                Actor.log.info(
                    f"page {page}: {len(jobs)} rows, {page_in_window} matched "
                    f"(has_more={data.get('has_more')})"
                )

                # The feed is roughly newest-first; bail out once whole pages are
                # older than the window instead of walking all ~1,500 pages.
                if cutoff:
                    all_stale = all(
                        not in_window(j, cutoff) for j in jobs if isinstance(j, dict)
                    )
                    stale_pages = stale_pages + 1 if all_stale else 0
                    if stale_pages >= 3:
                        Actor.log.info("3 consecutive pages outside the time window — stopping")
                        break

                if not data.get("has_more", True):
                    break
                await asyncio.sleep(request_delay)

        Actor.log.info(f"4dayweek scrape complete! Jobs harvested: {harvested}")


if __name__ == "__main__":
    asyncio.run(main())
