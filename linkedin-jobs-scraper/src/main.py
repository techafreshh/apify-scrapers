import asyncio
import os
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
import httpx
from apify import Actor


TIME_FILTERS = {
    "past_24h": "r86400",
    "past_week": "r604800",
    "past_month": "r2592000",
}


async def fetch_job_details(
    client: httpx.AsyncClient, job_id: str
) -> Dict[str, Any]:
    """Fetch complete job description and criteria from LinkedIn guest detail endpoint."""
    detail_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    try:
        resp = await client.get(detail_url, timeout=12.0)
        if resp.status_code != 200:
            return {"description": None, "criteria": []}

        soup = BeautifulSoup(resp.text, "html.parser")
        desc_el = soup.select_one(".show-more-less-html__markup")
        description = desc_el.get_text(separator="\n", strip=True) if desc_el else None

        criteria_items = []
        for item in soup.select("li.description__job-criteria-item"):
            sub = item.select_one(".description__job-criteria-subheader")
            val = item.select_one(".description__job-criteria-text")
            if sub and val:
                criteria_items.append(f"{sub.get_text(strip=True)}: {val.get_text(strip=True)}")
            elif item.get_text(strip=True):
                criteria_items.append(item.get_text(strip=True).replace("\n", " "))

        return {"description": description, "criteria": criteria_items}
    except Exception as e:
        Actor.log.debug(f"Failed to fetch details for job {job_id}: {e}")
        return {"description": None, "criteria": []}


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        queries: List[str] = actor_input.get("queries") or ["AI Automation", "Python Developer"]
        if isinstance(queries, str):
            queries = [queries]

        location: str = actor_input.get("location", "Worldwide")
        remote_only: bool = actor_input.get("remoteOnly", True)
        time_filter: str = actor_input.get("timeFilter", "past_week")
        max_jobs: int = int(actor_input.get("maxJobs", 30))
        fetch_details: bool = actor_input.get("fetchDetails", True)

        Actor.log.info(
            f"Starting LinkedIn Jobs Scraper: Queries={queries}, Location='{location}', "
            f"RemoteOnly={remote_only}, TimeFilter='{time_filter}', MaxJobs={max_jobs}"
        )

        # Setup proxy (defaults to Apify Proxy if available or configured)
        proxy_url = None
        proxy_input = actor_input.get("proxyConfiguration")
        groups = ["BUYPROXIES94952"]
        if isinstance(proxy_input, dict) and proxy_input.get("apifyProxyGroups"):
            groups = proxy_input["apifyProxyGroups"]

        try:
            proxy_config = await Actor.create_proxy_configuration(groups=groups)
            if proxy_config:
                proxy_url = await proxy_config.new_url()
                Actor.log.info(f"Initialized Apify Proxy with groups: {groups}")
        except Exception as e:
            Actor.log.warning(f"Actor.create_proxy_configuration failed: {e}. Trying direct proxy password fallback...")
            proxy_pwd = os.getenv("APIFY_PROXY_PASSWORD")
            if proxy_pwd:
                proxy_url = f"http://groups-{groups[0]}:{proxy_pwd}@proxy.apify.com:8000"
                Actor.log.info(f"Initialized Apify Proxy fallback via APIFY_PROXY_PASSWORD ({groups[0]}).")
            else:
                Actor.log.warning("No proxy password found. Running without proxy.")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        seen_job_ids = set()
        harvested_count = 0

        async with httpx.AsyncClient(proxy=proxy_url, headers=headers, timeout=20.0, follow_redirects=True) as client:
            for query in queries:
                if harvested_count >= max_jobs:
                    break

                Actor.log.info(f"Scanning listings for query: '{query}'")
                offset = 0
                query_exhausted = False

                while harvested_count < max_jobs and not query_exhausted and offset < 200:
                    params: Dict[str, Any] = {
                        "keywords": query,
                        "location": location,
                        "start": offset,
                    }
                    if remote_only:
                        params["f_WT"] = "2"
                    if time_filter in TIME_FILTERS:
                        params["f_TPR"] = TIME_FILTERS[time_filter]

                    search_url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{urllib.parse.urlencode(params)}"

                    try:
                        resp = await client.get(search_url)
                        if resp.status_code == 429:
                            Actor.log.warning(f"Rate limited (429) for '{query}' offset {offset}. Backing off 3s and retrying...")
                            await asyncio.sleep(3.0)
                            resp = await client.get(search_url)

                        if resp.status_code != 200:
                            Actor.log.warning(f"Query '{query}' offset {offset} returned status {resp.status_code}")
                            break

                        soup = BeautifulSoup(resp.text, "html.parser")
                        cards = soup.select(".base-search-card, li")
                        if not cards:
                            Actor.log.info(f"No more cards found for query '{query}' at offset {offset}")
                            query_exhausted = True
                            break

                        batch_found = 0
                        for card in cards:
                            if harvested_count >= max_jobs:
                                break

                            link_el = card.select_one("a.base-card__full-link")
                            urn = card.get("data-entity-urn", "")
                            job_id = None

                            if urn and ":" in urn:
                                job_id = urn.split(":")[-1]
                            elif link_el and link_el.get("href"):
                                m = re.search(r"-(\d+)(?:\?|$)", link_el["href"])
                                if m:
                                    job_id = m.group(1)

                            if not job_id or job_id in seen_job_ids:
                                continue

                            seen_job_ids.add(job_id)
                            batch_found += 1

                            title_el = card.select_one("h3.base-search-card__title")
                            company_el = card.select_one("h4.base-search-card__subtitle")
                            loc_el = card.select_one("span.job-search-card__location")
                            time_el = card.select_one("time")

                            title = title_el.get_text(strip=True) if title_el else "Unknown Role"
                            company = company_el.get_text(strip=True) if company_el else "Unknown Company"
                            job_loc = loc_el.get_text(strip=True) if loc_el else location

                            raw_href = link_el.get("href", "") if link_el else ""
                            job_url = raw_href.split("?")[0] if raw_href else f"https://www.linkedin.com/jobs/view/{job_id}"

                            posted_at = None
                            if time_el:
                                posted_at = time_el.get("datetime") or time_el.get_text(strip=True)

                            # Fetch complete description and criteria if enabled
                            description = None
                            criteria = []
                            if fetch_details:
                                details = await fetch_job_details(client, job_id)
                                description = details.get("description")
                                criteria = details.get("criteria", [])
                                await asyncio.sleep(0.4)

                            record = {
                                "id": job_id,
                                "title": title,
                                "company": company,
                                "location": job_loc,
                                "url": job_url,
                                "posted_at": posted_at,
                                "description": description or f"{title} at {company}",
                                "criteria": criteria,
                                "search_query": query,
                                "source": "linkedin_guest",
                            }

                            await Actor.push_data(record)
                            harvested_count += 1
                            Actor.log.info(f"[{harvested_count}/{max_jobs}] Saved: {title} @ {company}")

                        if batch_found == 0:
                            query_exhausted = True
                            break

                        offset += 25
                        await asyncio.sleep(1.0)

                    except Exception as e:
                        Actor.log.error(f"Error scraping LinkedIn search batch: {e}")
                        break

        Actor.log.info(f"LinkedIn scrape complete! Total positions harvested: {harvested_count}")


if __name__ == "__main__":
    asyncio.run(main())
