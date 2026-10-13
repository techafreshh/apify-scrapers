import asyncio
import html
import json
import os
import random
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from apify import Actor
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


def clean_text(raw_text: Optional[str]) -> str:
    if not raw_text:
        return ""
    clean = re.sub(r"<[^>]+>", "", raw_text)
    clean = html.unescape(clean)
    return clean.strip()


def resolve_nuxt_item(data: list, idx: Any, seen: Optional[Set[int]] = None) -> Any:
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


def extract_freelancers_from_nuxt(data: list) -> List[Dict[str, Any]]:
    for item in data:
        if isinstance(item, dict) and "freelancers" in item:
            fl_ref = item["freelancers"]
            if isinstance(fl_ref, int) and 0 <= fl_ref < len(data):
                raw_list = data[fl_ref]
                if isinstance(raw_list, list) and len(raw_list) > 0:
                    first_ref = raw_list[0]
                    if isinstance(first_ref, int) and 0 <= first_ref < len(data):
                        first_obj = data[first_ref]
                        if isinstance(first_obj, dict) and ("name" in first_obj or "uid" in first_obj or "title" in first_obj):
                            resolved = []
                            for f_ref in raw_list:
                                f_obj = resolve_nuxt_item(data, f_ref)
                                if isinstance(f_obj, dict) and (f_obj.get("name") or f_obj.get("uid")):
                                    resolved.append(f_obj)
                            return resolved
    return []


def parse_nuxt_job(job_data: Dict[str, Any], query: str) -> Optional[Dict[str, Any]]:
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

    skills = []
    attrs = job_data.get("attrs")
    if isinstance(attrs, list):
        for attr in attrs:
            if isinstance(attr, dict):
                skill_name = attr.get("prettyName") or attr.get("prefLabel")
                if skill_name:
                    skills.append(clean_text(skill_name))

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

    tier_raw = job_data.get("tierText") or ""
    experience_level = "Intermediate"
    if "entry" in str(tier_raw).lower():
        experience_level = "Entry"
    elif "expert" in str(tier_raw).lower():
        experience_level = "Expert"

    duration = job_data.get("durationLabel") or clean_text(str(job_data.get("duration") or ""))

    client_raw = job_data.get("client") or {}
    client_country = None
    is_verified = False
    total_spent = None
    total_reviews = None
    rating = None
    client_hires = None

    if isinstance(client_raw, dict):
        loc = client_raw.get("location") or {}
        if isinstance(loc, dict):
            client_country = loc.get("country")
        is_verified = bool(client_raw.get("isPaymentVerified"))
        total_spent = client_raw.get("totalSpent")
        total_reviews = client_raw.get("totalReviews")
        client_hires = client_raw.get("totalHires")
        total_feedback = client_raw.get("totalFeedback")
        if total_feedback is not None:
            try:
                rating = float(total_feedback)
            except Exception:
                pass

    published_at = job_data.get("publishedOn") or job_data.get("createdOn")

    return {
        "recordType": "job",
        "recordTitle": title,
        "recordUrl": url,
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
            "total_hires": client_hires,
        },
        "published_at": published_at,
        "search_query": query,
        "source": "upwork",
    }


def parse_nuxt_freelancer(fl_data: Dict[str, Any], query: str) -> Optional[Dict[str, Any]]:
    uid = str(fl_data.get("uid") or "")
    name = clean_text(fl_data.get("name") or "")
    title = clean_text(fl_data.get("title") or fl_data.get("headline") or "")
    if not name and not title:
        return None

    url = f"https://www.upwork.com/freelancers/{uid}" if uid else ""

    skills = []
    attrs = fl_data.get("attrs")
    if isinstance(attrs, list):
        for attr in attrs:
            if isinstance(attr, dict):
                skill_name = attr.get("prettyName") or attr.get("prefLabel")
                if skill_name:
                    skills.append(clean_text(skill_name))

    hourly_rate = None
    rate_obj = fl_data.get("hourlyRate") or fl_data.get("rate") or {}
    if isinstance(rate_obj, dict) and rate_obj.get("amount"):
        try:
            hourly_rate = float(rate_obj.get("amount"))
        except Exception:
            pass

    jss = fl_data.get("jobSuccessScore") or fl_data.get("jss")
    if jss is not None:
        try:
            jss = float(jss)
        except Exception:
            jss = None

    badge = clean_text(fl_data.get("badge") or fl_data.get("tier") or "")
    location = ""
    loc_obj = fl_data.get("location") or {}
    if isinstance(loc_obj, dict):
        city = loc_obj.get("city") or ""
        country = loc_obj.get("country") or ""
        location = f"{city}, {country}".strip(", ")

    total_earnings = fl_data.get("totalEarnings")
    total_hours = fl_data.get("totalHours")
    description = clean_text(fl_data.get("description") or fl_data.get("bio") or "")

    return {
        "recordType": "freelancer",
        "recordTitle": title or name,
        "recordUrl": url,
        "id": uid,
        "name": name,
        "title": title,
        "url": url,
        "description": description,
        "skills": skills,
        "hourly_rate": hourly_rate,
        "jss": jss,
        "badge": badge,
        "location": location,
        "total_earnings": total_earnings,
        "total_hours": total_hours,
        "search_query": query,
        "source": "upwork",
    }


async def solve_turnstile_if_present(page) -> bool:
    try:
        title = await page.title()
        if "challenge" not in title.lower() and "just a moment" not in title.lower():
            return True

        print("[*] Cloudflare challenge detected. Attempting Turnstile solve...", flush=True)

        for attempt in range(3):
            for frame in page.frames:
                frame_url = frame.url or ""
                if "cloudflare" in frame_url or "challenges" in frame_url or "turnstile" in frame_url:
                    selectors = [
                        "input[type='checkbox']",
                        "#cf-stage",
                        ".ctp-checkbox-label",
                        "div.challenge-stage",
                        ".ctp-checkbox-container",
                        "label.ctp-checkbox-label",
                    ]
                    for sel in selectors:
                        try:
                            el = frame.locator(sel)
                            if await el.count() > 0:
                                await el.first.click()
                                print(f"[+] Turnstile checkbox clicked (attempt {attempt + 1})", flush=True)
                                await page.wait_for_timeout(5000)
                                new_title = await page.title()
                                if "challenge" not in new_title.lower() and "just a moment" not in new_title.lower():
                                    print("[+] Challenge passed!", flush=True)
                                    return True
                        except Exception:
                            continue
            await page.wait_for_timeout(2000)

        print("[-] Turnstile solve failed after retries", flush=True)
        return False
    except Exception as e:
        print(f"[*] Turnstile solver error: {e}", flush=True)
        return False


def is_blocked_page(title: str) -> bool:
    title_lower = title.lower()
    return any(phrase in title_lower for phrase in [
        "challenge", "just a moment", "attention required", "access denied", "403", "captcha"
    ])


async def fetch_page_with_retry(page, url: str, max_retries: int = 5) -> Tuple[bool, str]:
    for attempt in range(max_retries):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            title = await page.title()

            if is_blocked_page(title):
                print(f"[{attempt + 1}/{max_retries}] Blocked: '{title}'. Attempting Turnstile solve...", flush=True)
                passed = await solve_turnstile_if_present(page)
                if passed:
                    return True, await page.content()
                if attempt < max_retries - 1:
                    wait = 10 * (attempt + 1) + random.randint(3, 10)
                    print(f"[*] Cooldown {wait}s before retry {attempt + 2}/{max_retries}...", flush=True)
                    await page.wait_for_timeout(wait * 1000)
                    continue
                return False, ""

            return True, await page.content()
        except Exception as e:
            print(f"[-] Navigation error (attempt {attempt + 1}): {e}", flush=True)
            if attempt < max_retries - 1:
                await page.wait_for_timeout(5000)

    return False, ""


def parse_dom_tiles(soup: BeautifulSoup, query: str) -> List[Dict[str, Any]]:
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
        is_payment_verified = False

        for li in article.find_all("li"):
            text = li.get_text(" ", strip=True)
            text_lower = text.lower()
            if "payment unverified" in text_lower:
                is_payment_verified = False
            elif "payment verified" in text_lower:
                is_payment_verified = True

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
                    elif "expert" in text_lower:
                        exp_level = "Expert"

            if "est. time" in text_lower or "less than" in text_lower or "month" in text_lower:
                duration = text.replace("Est. time:", "").strip()

        ciphertext = f"~02{uid}" if uid else ""
        url = f"https://www.upwork.com/jobs/{ciphertext}" if ciphertext else ""

        results.append({
            "recordType": "job",
            "recordTitle": title,
            "recordUrl": url,
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
                "is_payment_verified": is_payment_verified,
                "total_spent": None,
                "total_reviews": None,
                "rating": None,
                "total_hires": None,
            },
            "published_at": datetime.now(timezone.utc).isoformat(),
            "search_query": query,
            "source": "upwork",
        })
    return results


def parse_dom_freelancer_cards(soup: BeautifulSoup, query: str) -> List[Dict[str, Any]]:
    results = []
    cards = soup.find_all("div", class_=re.compile(r"freelancer-card|user-card|tile-card"))
    if not cards:
        cards = soup.find_all("article")

    for card in cards:
        name_el = card.find(["h3", "h4", "a"], class_=re.compile(r"name|title|freelancer-name"))
        if not name_el:
            continue
        name = clean_text(name_el.get_text())

        title_el = card.find(["p", "span", "div"], class_=re.compile(r"headline|title|role"))
        title = clean_text(title_el.get_text()) if title_el else ""

        uid = card.get("data-ev-user-uid", "") or ""
        link_el = card.find("a", href=re.compile(r"/freelancers/"))
        if link_el:
            href = link_el.get("href", "")
            match = re.search(r"/freelancers/(~[0-9a-zA-Z]+)", href)
            if match:
                uid = uid or match.group(1)

        url = f"https://www.upwork.com/freelancers/{uid}" if uid else ""

        skills = [clean_text(s.get_text()) for s in card.select("button.air3-token span, .skill-tag span")]

        hourly_rate = None
        rate_text = card.get_text()
        rate_match = re.search(r"\$([0-9,.]+)/hr", rate_text)
        if rate_match:
            try:
                hourly_rate = float(rate_match.group(1).replace(",", ""))
            except Exception:
                pass

        jss = None
        jss_match = re.search(r"(\d+)%\s*Job Success", card.get_text())
        if jss_match:
            try:
                jss = float(jss_match.group(1))
            except Exception:
                pass

        location = ""
        loc_el = card.find(["span", "div"], class_=re.compile(r"location|country"))
        if loc_el:
            location = clean_text(loc_el.get_text())

        if name or title:
            results.append({
                "recordType": "freelancer",
                "recordTitle": title or name,
                "recordUrl": url,
                "id": uid,
                "name": name,
                "title": title,
                "url": url,
                "description": "",
                "skills": skills,
                "hourly_rate": hourly_rate,
                "jss": jss,
                "badge": "",
                "location": location,
                "total_earnings": None,
                "total_hours": None,
                "search_query": query,
                "source": "upwork",
            })
    return results


async def scrape_jobs(page, query: str, max_jobs: int, seen_ids: Set[str], proxy_pwd: Optional[str] = None, proxy_group: str = "RESIDENTIAL") -> List[Dict[str, Any]]:
    jobs = []
    page_num = 1

    while len(jobs) < max_jobs and page_num <= 10:
        params = {
            "q": query,
            "sort": "recency",
            "page": page_num,
            "per_page": 50,
        }
        search_url = f"https://www.upwork.com/nx/search/jobs/?{urllib.parse.urlencode(params)}"
        print(f"[*] Jobs search page {page_num}: {search_url}", flush=True)

        success, html_content = await fetch_page_with_retry(page, search_url)
        if not success:
            print(f"[-] Failed to load jobs page {page_num} for '{query}'", flush=True)
            break

        soup = BeautifulSoup(html_content, "html.parser")
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

        if not jobs_found:
            jobs_found = parse_dom_tiles(soup, query)

        if not jobs_found:
            print(f"[-] No jobs found for '{query}' on page {page_num}", flush=True)
            break

        print(f"[+] Extracted {len(jobs_found)} jobs from page {page_num}", flush=True)

        for job in jobs_found:
            job_id = job.get("id") or job.get("ciphertext")
            if not job_id or job_id in seen_ids:
                continue
            seen_ids.add(job_id)
            jobs.append(job)
            if len(jobs) >= max_jobs:
                break

        page_num += 1
        page_cooldown = random.randint(15, 30)
        print(f"[*] Inter-page cooldown: {page_cooldown}s...", flush=True)
        await page.wait_for_timeout(page_cooldown * 1000)

    return jobs


async def scrape_freelancers(page, query: str, max_results: int, seen_ids: Set[str], proxy_pwd: Optional[str] = None, proxy_group: str = "RESIDENTIAL") -> List[Dict[str, Any]]:
    freelancers = []
    page_num = 1

    while len(freelancers) < max_results and page_num <= 10:
        params = {
            "q": query,
            "sort": "relevance",
            "page": page_num,
            "per_page": 50,
        }
        search_url = f"https://www.upwork.com/nx/search/freelancers/?{urllib.parse.urlencode(params)}"
        print(f"[*] Freelancers search page {page_num}: {search_url}", flush=True)

        success, html_content = await fetch_page_with_retry(page, search_url)
        if not success:
            print(f"[-] Failed to load freelancers page {page_num} for '{query}'", flush=True)
            break

        soup = BeautifulSoup(html_content, "html.parser")
        fl_found = []

        nuxt_script = soup.find("script", id="__NUXT_DATA__")
        if nuxt_script and nuxt_script.text:
            try:
                nuxt_data = json.loads(nuxt_script.text)
                raw_fls = extract_freelancers_from_nuxt(nuxt_data)
                for rf in raw_fls:
                    parsed = parse_nuxt_freelancer(rf, query)
                    if parsed:
                        fl_found.append(parsed)
            except Exception as ne:
                Actor.log.debug(f"Nuxt freelancer extraction error: {ne}")

        if not fl_found:
            fl_found = parse_dom_freelancer_cards(soup, query)

        if not fl_found:
            print(f"[-] No freelancers found for '{query}' on page {page_num}", flush=True)
            break

        print(f"[+] Extracted {len(fl_found)} freelancers from page {page_num}", flush=True)

        for fl in fl_found:
            fl_id = fl.get("id")
            if not fl_id or fl_id in seen_ids:
                continue
            seen_ids.add(fl_id)
            freelancers.append(fl)
            if len(freelancers) >= max_results:
                break

        page_num += 1
        page_cooldown = random.randint(15, 30)
        print(f"[*] Inter-page cooldown: {page_cooldown}s...", flush=True)
        await page.wait_for_timeout(page_cooldown * 1000)

    return freelancers


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}

        operation = actor_input.get("operation", "searchJobs")
        queries = actor_input.get("queries") or ["n8n", "AI Automation"]
        if isinstance(queries, str):
            queries = [queries]

        urls = actor_input.get("urls") or []
        max_pages = int(actor_input.get("maxPages", 3))

        max_jobs = int(actor_input.get("maxJobs", 30))
        sort_order = actor_input.get("sort", "recency")
        payment_verified_only = actor_input.get("paymentVerifiedOnly", False)
        contractor_tier = actor_input.get("contractorTier", "any").lower()

        print(f"[*] Starting Upwork Scraper | Operation: {operation} | Queries: {queries}", flush=True)
        Actor.log.info(
            f"Starting Upwork Scraper | Operation: {operation} | Queries: {queries} | "
            f"URLs: {urls} | MaxPages: {max_pages}"
        )

        proxy_pwd = os.getenv("APIFY_PROXY_PASSWORD")
        proxy_input = actor_input.get("proxyConfiguration") or {}
        use_proxy = proxy_input.get("useApifyProxy", True)
        proxy_group = "RESIDENTIAL"
        if proxy_input.get("apifyProxyGroups"):
            proxy_group = proxy_input["apifyProxyGroups"][0]

        launch_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
        ]

        seen_ids: Set[str] = set()
        harvested: List[Dict[str, Any]] = []
        session_counter = 0

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=launch_args)

            async def create_fresh_context():
                nonlocal session_counter
                session_counter += 1
                session_id = f"upwork-{session_counter}-{random.randint(1000, 9999)}"

                context_proxy = None
                if use_proxy:
                    if proxy_pwd:
                        context_proxy = {
                            "server": "http://proxy.apify.com:8000",
                            "username": f"groups-{proxy_group}",
                            "password": proxy_pwd,
                        }
                    else:
                        try:
                            proxy_config = await Actor.create_proxy_configuration(
                                groups=[proxy_group],
                                session_id=session_id,
                            )
                            if proxy_config:
                                proxy_url = await proxy_config.new_url(session_id=session_id)
                                context_proxy = {"server": proxy_url}
                        except Exception as e:
                            print(f"[-] Proxy session failed: {e}", flush=True)

                context = await browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    locale="en-US",
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                    proxy=context_proxy,
                )
                await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                return context

            if operation == "searchJobs":
                for qi, query in enumerate(queries):
                    if len(harvested) >= max_jobs:
                        break
                    if qi > 0:
                        cooldown = random.randint(30, 60)
                        print(f"[*] Inter-query cooldown: {cooldown}s...", flush=True)
                        await asyncio.sleep(cooldown)

                    context = await create_fresh_context()
                    page = await context.new_page()
                    remaining = max_jobs - len(harvested)
                    jobs = await scrape_jobs(page, query, remaining, seen_ids)
                    for job in jobs:
                        if payment_verified_only:
                            client_info = job.get("client") or {}
                            if not client_info.get("is_payment_verified"):
                                continue
                        if contractor_tier != "any":
                            job_exp = (job.get("experience_level") or "").lower()
                            if contractor_tier not in job_exp:
                                continue
                        harvested.append(job)
                    await context.close()

            elif operation == "searchFreelancers":
                for qi, query in enumerate(queries):
                    if len(harvested) >= max_jobs:
                        break
                    if qi > 0:
                        cooldown = random.randint(30, 60)
                        print(f"[*] Inter-query cooldown: {cooldown}s...", flush=True)
                        await asyncio.sleep(cooldown)

                    context = await create_fresh_context()
                    page = await context.new_page()
                    remaining = max_jobs - len(harvested)
                    freelancers = await scrape_freelancers(page, query, remaining, seen_ids)
                    harvested.extend(freelancers)
                    await context.close()

            elif operation == "jobDetail":
                for job_url in urls[:max_jobs]:
                    context = await create_fresh_context()
                    page = await context.new_page()
                    print(f"[*] Fetching job detail: {job_url}", flush=True)
                    success, html_content = await fetch_page_with_retry(page, job_url)
                    if not success:
                        await Actor.push_data({"recordType": "job", "recordUrl": job_url, "error": "Failed to load page"})
                        await context.close()
                        continue

                    soup = BeautifulSoup(html_content, "html.parser")
                    nuxt_script = soup.find("script", id="__NUXT_DATA__")
                    if nuxt_script and nuxt_script.text:
                        try:
                            nuxt_data = json.loads(nuxt_script.text)
                            for item in nuxt_data:
                                if isinstance(item, dict):
                                    resolved = resolve_nuxt_item(nuxt_data, nuxt_data.index(item))
                                    if isinstance(resolved, dict) and resolved.get("title"):
                                        parsed = parse_nuxt_job(resolved, "")
                                        if parsed:
                                            parsed["_operation"] = "jobDetail"
                                            harvested.append(parsed)
                                            break
                        except Exception as ne:
                            Actor.log.debug(f"Job detail Nuxt error: {ne}")
                    await context.close()

            elif operation == "freelancerProfile":
                for fl_url in urls[:max_jobs]:
                    context = await create_fresh_context()
                    page = await context.new_page()
                    print(f"[*] Fetching freelancer profile: {fl_url}", flush=True)
                    success, html_content = await fetch_page_with_retry(page, fl_url)
                    if not success:
                        await Actor.push_data({"recordType": "freelancer", "recordUrl": fl_url, "error": "Failed to load page"})
                        await context.close()
                        continue

                    soup = BeautifulSoup(html_content, "html.parser")
                    nuxt_script = soup.find("script", id="__NUXT_DATA__")
                    if nuxt_script and nuxt_script.text:
                        try:
                            nuxt_data = json.loads(nuxt_script.text)
                            for item in nuxt_data:
                                if isinstance(item, dict) and (item.get("name") or item.get("title")):
                                    resolved = resolve_nuxt_item(nuxt_data, nuxt_data.index(item))
                                    if isinstance(resolved, dict):
                                        parsed = parse_nuxt_freelancer(resolved, "")
                                        if parsed:
                                            parsed["_operation"] = "freelancerProfile"
                                            harvested.append(parsed)
                                            break
                        except Exception as ne:
                            Actor.log.debug(f"Freelancer profile Nuxt error: {ne}")
                    await context.close()

            await browser.close()

        for record in harvested:
            await Actor.push_data(record)

        print(f"[*] Scrape complete. Total records: {len(harvested)}", flush=True)
        Actor.log.info(f"Upwork scrape completed! Total records saved: {len(harvested)}")


if __name__ == "__main__":
    asyncio.run(main())
