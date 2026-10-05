import asyncio
import html
import json
import os
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from apify import Actor
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


def clean_text(raw_text: Optional[str]) -> str:
    """Strip HTML tags and unescape entities."""
    if not raw_text:
        return ""
    clean = re.sub(r"<[^>]+>", "", raw_text)
    clean = html.unescape(clean)
    return clean.strip()


def resolve_nuxt_item(data: list, idx: Any, seen: Optional[Set[int]] = None) -> Any:
    """Recursively resolves Nuxt 3 de-duplicated indexed state tree."""
    if seen is None:
        seen = set()
    if not isinstance(idx, int) or idx < 0 or idx >= len(data):
        return idx
    if idx in seen:
        return None
    seen.add(idx)

    val = data[idx]
    if isinstance(val, dict):
        return {k: resolve_nuxt_item(data, v, seen.copy()) for k, v in val.items()}
    if isinstance(val, list):
        return [resolve_nuxt_item(data, v, seen.copy()) for v in val]
    return val


def extract_jobs_from_nuxt(data: list) -> List[Dict[str, Any]]:
    """Locate and unpack jobs list from Nuxt 3 payload."""
    for item in data:
        if isinstance(item, dict) and "jobs" in item:
            jobs_ref = item["jobs"]
            if isinstance(jobs_ref, int) and 0 <= jobs_ref < len(data):
                raw_list = data[jobs_ref]
                if isinstance(raw_list, list) and len(raw_list) > 0:
                    first_ref = raw_list[0]
                    if isinstance(first_ref, int) and 0 <= first_ref < len(data):
                        first_obj = data[first_ref]
                        if isinstance(first_obj, dict) and ("ciphertext" in first_obj or "title" in first_obj or "uid" in first_obj):
                            resolved_jobs = []
                            for j_ref in raw_list:
                                j_obj = resolve_nuxt_item(data, j_ref)
                                if isinstance(j_obj, dict) and (j_obj.get("title") or j_obj.get("uid")):
                                    resolved_jobs.append(j_obj)
                            return resolved_jobs
    return []


def parse_nuxt_job(job_data: Dict[str, Any], query: str) -> Optional[Dict[str, Any]]:
    """Parse resolved Nuxt job dictionary into clean output schema."""
    uid = str(job_data.get("uid") or "")
    ciphertext = str(job_data.get("ciphertext") or "")
    if not ciphertext and uid:
        ciphertext = f"~02{uid}"

    raw_title = job_data.get("title") or ""
    title = clean_text(raw_title)
    if not title:
        return None

    raw_desc = job_data.get("description") or ""
    description = clean_text(raw_desc)

    url = f"https://www.upwork.com/jobs/{ciphertext}" if ciphertext else ""

    # Parse skills
    skills = []
    attrs = job_data.get("attrs")
    if isinstance(attrs, list):
        for attr in attrs:
            if isinstance(attr, dict):
                skill_name = attr.get("prettyName") or attr.get("prefLabel")
                if skill_name:
                    skills.append(clean_text(skill_name))

    # Job type & budget
    hourly_budget = job_data.get("hourlyBudget") or {}
    fixed_amount = None
    amount_obj = job_data.get("amount") or {}
    if isinstance(amount_obj, dict) and amount_obj.get("amount"):
        try:
            fixed_amount = float(amount_obj.get("amount"))
        except Exception:
            pass

    job_type = "hourly" if (hourly_budget and (hourly_budget.get("min") or hourly_budget.get("max"))) else "fixed"

    budget_min = None
    budget_max = None
    if job_type == "hourly":
        if isinstance(hourly_budget, dict):
            try:
                budget_min = float(hourly_budget.get("min")) if hourly_budget.get("min") is not None else None
                budget_max = float(hourly_budget.get("max")) if hourly_budget.get("max") is not None else None
            except Exception:
                pass
    else:
        budget_min = fixed_amount
        budget_max = fixed_amount

    # Experience level
    tier_raw = job_data.get("tierText") or ""
    experience_level = "Intermediate"
    if "entry" in str(tier_raw).lower():
        experience_level = "Entry"
    elif "expert" in str(tier_raw).lower():
        experience_level = "Expert"

    duration = job_data.get("durationLabel") or clean_text(str(job_data.get("duration") or ""))

    # Client Info
    client_raw = job_data.get("client") or {}
    client_country = None
    is_verified = False
    total_spent = None
    total_reviews = None
    rating = None

    if isinstance(client_raw, dict):
        loc = client_raw.get("location") or {}
        if isinstance(loc, dict):
            client_country = loc.get("country")
        is_verified = bool(client_raw.get("isPaymentVerified"))
        total_spent = client_raw.get("totalSpent")
        total_reviews = client_raw.get("totalReviews")
        total_feedback = client_raw.get("totalFeedback")
        if total_feedback is not None:
            try:
                rating = float(total_feedback)
            except Exception:
                pass

    published_at = job_data.get("publishedOn") or job_data.get("createdOn")

    return {
        "id": uid or ciphertext,
        "ciphertext": ciphertext,
        "title": title,
        "url": url,
        "description": description,
        "skills": skills,
        "job_type": job_type,
        "budget_min": budget_min,
        "budget_max": budget_max,
        "hourly_budget": hourly_budget if job_type == "hourly" else None,
        "fixed_amount": fixed_amount,
        "experience_level": experience_level,
        "duration": duration,
        "client": {
            "country": client_country,
            "is_payment_verified": is_verified,
            "total_spent": total_spent,
            "total_reviews": total_reviews,
            "rating": rating,
        },
        "published_at": published_at,
        "search_query": query,
        "source": "upwork",
    }


def parse_dom_tiles(soup: BeautifulSoup, query: str) -> List[Dict[str, Any]]:
    """DOM fallback parser if Nuxt payload structure changes."""
    results = []
    articles = soup.find_all("article")
    for article in articles:
        uid = article.get("data-ev-job-uid", "")
        title_el = article.find(["h2", "h3"], class_=re.compile(r"job-tile-title|title"))
        if not title_el:
            continue
        title = clean_text(title_el.get_text())

        desc_el = article.find("p", class_=re.compile(r"text-body|description"))
        description = clean_text(desc_el.get_text()) if desc_el else ""

        skills = [clean_text(btn.get_text()) for btn in article.select("button.air3-token span")]

        job_type = "fixed"
        budget_min = None
        budget_max = None
        exp_level = "Intermediate"
        duration = ""

        for li in article.find_all("li"):
            text = li.get_text(" ", strip=True)
            if "hourly" in text.lower():
                job_type = "hourly"
                nums = re.findall(r"\$([0-9,.]+)", text)
                if nums:
                    try:
                        budget_min = float(nums[0].replace(",", ""))
                        budget_max = float(nums[-1].replace(",", ""))
                    except Exception:
                        pass
            elif "$" in text and job_type != "hourly":
                nums = re.findall(r"\$([0-9,.]+)", text)
                if nums:
                    try:
                        budget_min = float(nums[0].replace(",", ""))
                        budget_max = budget_min
                    except Exception:
                        pass

            if any(lvl in text.lower() for lvl in ["entry", "intermediate", "expert"]):
                if "entry" in text.lower():
                    exp_level = "Entry"
                elif "expert" in text.lower():
                    exp_level = "Expert"

            if "est. time" in text.lower() or "less than" in text.lower() or "month" in text.lower():
                duration = text.replace("Est. time:", "").strip()

        ciphertext = f"~02{uid}" if uid else ""
        url = f"https://www.upwork.com/jobs/{ciphertext}" if ciphertext else ""

        results.append({
            "id": uid,
            "ciphertext": ciphertext,
            "title": title,
            "url": url,
            "description": description,
            "skills": skills,
            "job_type": job_type,
            "budget_min": budget_min,
            "budget_max": budget_max,
            "hourly_budget": {"min": budget_min, "max": budget_max} if job_type == "hourly" else None,
            "fixed_amount": budget_min if job_type == "fixed" else None,
            "experience_level": exp_level,
            "duration": duration,
            "client": {
                "country": None,
                "is_payment_verified": False,
                "total_spent": None,
                "total_reviews": None,
                "rating": None,
            },
            "published_at": datetime.now(timezone.utc).isoformat(),
            "search_query": query,
            "source": "upwork",
        })
    return results


async def solve_turnstile_if_present(page) -> None:
    """Attempts to click Cloudflare Turnstile human verification checkbox if rendered in an iframe."""
    try:
        title = await page.title()
        if "challenge" in title.lower() or "just a moment" in title.lower():
            print("[*] Cloudflare challenge screen detected. Searching for Turnstile widget...", flush=True)
            for _ in range(4):
                for frame in page.frames:
                    if "cloudflare" in frame.url or "challenges" in frame.url or "turnstile" in frame.url:
                        box = frame.locator("input[type='checkbox'], #cf-stage, .ctp-checkbox-label, div.challenge-stage")
                        if await box.count() > 0:
                            print("[*] Found Turnstile checkbox, clicking...", flush=True)
                            await box.first.click()
                            await page.wait_for_timeout(3500)
                            return
                await page.wait_for_timeout(1500)
    except Exception as e:
        print(f"[*] Turnstile solver notice: {e}", flush=True)


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        queries: List[str] = actor_input.get("queries") or ["n8n", "AI Automation"]
        if isinstance(queries, str):
            queries = [queries]

        max_jobs: int = int(actor_input.get("maxJobs", 30))
        sort_order: str = actor_input.get("sort", "recency")
        payment_verified_only: bool = actor_input.get("paymentVerifiedOnly", False)
        contractor_tier: str = actor_input.get("contractorTier", "any").lower()

        print(f"[*] Starting Upwork Jobs Scraper (Playwright Engine): Queries={queries}, MaxJobs={max_jobs}", flush=True)
        Actor.log.info(
            f"Starting Upwork Jobs Scraper (Playwright Engine): Queries={queries}, MaxJobs={max_jobs}, "
            f"Sort='{sort_order}', PaymentVerifiedOnly={payment_verified_only}, Tier='{contractor_tier}'"
        )

        # Proxy setup (Residential by default to bypass Cloudflare)
        proxy_pwd = os.getenv("APIFY_PROXY_PASSWORD")
        proxy_input = actor_input.get("proxyConfiguration") or {}
        proxy_opt = None

        if proxy_input.get("useApifyProxy", True):
            group = "RESIDENTIAL"
            if proxy_input.get("apifyProxyGroups"):
                group = proxy_input["apifyProxyGroups"][0]

            if proxy_pwd:
                proxy_opt = {
                    "server": "http://proxy.apify.com:8000",
                    "username": f"groups-{group}",
                    "password": proxy_pwd,
                }
                print(f"[*] Configured Playwright with Apify {group} proxy via APIFY_PROXY_PASSWORD.", flush=True)
            else:
                try:
                    proxy_config = await Actor.create_proxy_configuration(groups=[group])
                    if proxy_config:
                        proxy_url = await proxy_config.new_url()
                        proxy_opt = {"server": proxy_url}
                        print(f"[*] Initialized Apify Proxy via SDK: {group}.", flush=True)
                except Exception as e:
                    print(f"[-] Apify proxy config fallback: {e}. Running direct connection.", flush=True)

        launch_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
        ]

        seen_job_ids = set()
        harvested_count = 0

        print("[*] Launching Playwright Chromium...", flush=True)
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=launch_args, proxy=proxy_opt)
            print("[*] Chromium launched successfully.", flush=True)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )

            # Mask navigator.webdriver
            await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            page = await context.new_page()

            for query in queries:
                if harvested_count >= max_jobs:
                    break

                print(f"[*] Searching Upwork for keyword: '{query}'", flush=True)
                page_num = 1
                query_exhausted = False

                while harvested_count < max_jobs and not query_exhausted and page_num <= 10:
                    params = {
                        "q": query,
                        "sort": sort_order,
                        "page": page_num,
                        "per_page": 50,
                    }
                    search_url = f"https://www.upwork.com/nx/search/jobs/?{urllib.parse.urlencode(params)}"
                    print(f"[*] Navigating to page {page_num}: {search_url}", flush=True)

                    try:
                        await page.goto(search_url, wait_until="domcontentloaded", timeout=45000)
                        title = await page.title()
                        print(f"[*] Page loaded. Title: '{title}'", flush=True)

                        # Check if Cloudflare challenge or loading screen is present
                        if "challenge" in title.lower() or "just a moment" in title.lower():
                            await solve_turnstile_if_present(page)

                        try:
                            await page.wait_for_selector(
                                "article.job-tile, script#__NUXT_DATA__, div[data-test='job-tile-list']",
                                timeout=15000
                            )
                        except Exception:
                            print("[*] Waiting 6s for dynamic content / challenge to settle...", flush=True)
                            await page.wait_for_timeout(6000)

                        html_content = await page.content()
                        soup = BeautifulSoup(html_content, "html.parser")

                        # Try primary Nuxt extraction
                        jobs_found = []
                        nuxt_script = soup.find("script", id="__NUXT_DATA__")
                        if nuxt_script and nuxt_script.text:
                            try:
                                nuxt_data = json.loads(nuxt_script.text)
                                raw_jobs = extract_jobs_from_nuxt(nuxt_data)
                                for rj in raw_jobs:
                                    parsed = parse_nuxt_job(rj, query)
                                    if parsed:
                                        jobs_found.append(parsed)
                            except Exception as ne:
                                Actor.log.debug(f"Nuxt extraction error: {ne}")

                        # If Nuxt didn't yield jobs, try DOM fallback
                        if not jobs_found:
                            jobs_found = parse_dom_tiles(soup, query)

                        if not jobs_found:
                            print(f"[-] No listings found for '{query}' on page {page_num}. Page title: '{await page.title()}'", flush=True)
                            Actor.log.info(f"No listings found for '{query}' on page {page_num}. Page title: '{await page.title()}'")
                            query_exhausted = True
                            break

                        print(f"[+] Successfully extracted {len(jobs_found)} jobs from page {page_num}.", flush=True)
                        batch_count = 0
                        for job in jobs_found:
                            if harvested_count >= max_jobs:
                                break

                            job_id = job.get("id") or job.get("ciphertext")
                            if not job_id or job_id in seen_job_ids:
                                continue

                            # Apply user filters
                            if payment_verified_only:
                                client_info = job.get("client") or {}
                                if not client_info.get("is_payment_verified"):
                                    continue

                            if contractor_tier != "any":
                                job_exp = (job.get("experience_level") or "").lower()
                                if contractor_tier not in job_exp:
                                    continue

                            seen_job_ids.add(job_id)
                            await Actor.push_data(job)
                            harvested_count += 1
                            batch_count += 1

                            print(
                                f"[{harvested_count}/{max_jobs}] Saved: {job['title'][:50]} | "
                                f"Type: {job['job_type']} | Budget: {job.get('budget_min')}-{job.get('budget_max')}",
                                flush=True
                            )
                            Actor.log.info(
                                f"[{harvested_count}/{max_jobs}] Saved Upwork Job: "
                                f"{job['title']} | Type: {job['job_type']} | Budget: {job.get('budget_min')}-{job.get('budget_max')}"
                            )

                        if batch_count == 0 and len(jobs_found) < 10:
                            query_exhausted = True
                            break

                        page_num += 1
                        await page.wait_for_timeout(2000)

                    except Exception as e:
                        print(f"[-] Error scraping Upwork page {page_num} for query '{query}': {e}", flush=True)
                        Actor.log.error(f"Error scraping Upwork page {page_num} for query '{query}': {e}")
                        break

            await browser.close()

        Actor.log.info(f"Upwork Jobs scrape completed! Total positions saved: {harvested_count}")


if __name__ == "__main__":
    asyncio.run(main())
