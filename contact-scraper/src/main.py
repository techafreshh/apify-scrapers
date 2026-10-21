import asyncio
import re
import urllib.parse
from typing import Any, Dict, List, Optional, Set, Tuple
from apify import Actor
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, BrowserContext, Page


EMAIL_REGEX = re.compile(
    r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
    re.IGNORECASE
)

PHONE_REGEX = re.compile(
    r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"
)

# File extensions / image paths that mimic emails
INVALID_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico",
    ".css", ".js", ".jsx", ".ts", ".tsx", ".woff", ".woff2", ".ttf",
    ".mp4", ".mp3", ".pdf", ".zip", ".tar", ".gz"
}

# Junk / vendor / test domains
DISALLOWED_DOMAINS = {
    "example.com", "domain.com", "email.com", "yourdomain.com",
    "test.com", "sample.com", "mysite.com", "wixpress.com", "sentry.io",
    "schema.org", "cloudflare.com", "recaptcha.net", "googleapis.com",
    "gravatar.com", "doubleclick.net", "google-analytics.com",
    "facebook.com", "instagram.com", "twitter.com", "linkedin.com",
    "youtube.com", "pinterest.com", "tiktok.com", "yelp.com"
}


def clean_url(url: str) -> str:
    """Normalize input URL with http/https scheme."""
    url = url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url
    return url


def extract_domain(url: str) -> str:
    """Extract registrable domain or hostname for matching."""
    try:
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        return netloc.split(":")[0]
    except Exception:
        return ""


def is_valid_email(email: str) -> bool:
    """Validate discovered email address and filter false positives."""
    email = email.strip().lower()
    if not email or len(email) < 5 or "@" not in email:
        return False

    parts = email.split("@")
    if len(parts) != 2:
        return False

    user, domain = parts
    if not user or not domain or "." not in domain:
        return False

    # Enforce valid alphabetical TLD (2 to 24 chars, no digits in TLD)
    tld_match = re.search(r"\.([a-zA-Z]{2,24})$", domain)
    if not tld_match:
        return False

    # Block version numbers or npm-like patterns
    if re.search(r"\d+\.\d+", domain):
        return False

    # Check extension
    for ext in INVALID_EXTENSIONS:
        if email.endswith(ext):
            return False

    # Check disallowed domain substrings
    for dis in DISALLOWED_DOMAINS:
        if dis in domain:
            return False

    # Filter dummy strings
    if any(dummy in user for dummy in ["user", "name", "yourname", "test", "demo", "sample", "example"]):
        if any(dummy in domain for dummy in ["example", "domain", "company", "test"]):
            return False

    return True


def rank_emails(emails: Set[str], site_domain: str) -> List[str]:
    """Sort emails placing domain-matched and prominent addresses first."""
    def email_score(e: str) -> int:
        score = 0
        e_lower = e.lower()
        parts = e_lower.split("@")
        if len(parts) == 2:
            domain = parts[1]
            if site_domain and site_domain in domain:
                score += 50
            if any(role in parts[0] for role in ["info", "contact", "office", "service", "estimates", "sales", "hello", "support"]):
                score += 20
        return score

    return sorted(list(emails), key=email_score, reverse=True)


async def dismiss_popups(page: Page) -> None:
    """Attempt to close cookie / privacy banners or modal overlays."""
    try:
        selectors = [
            "button[aria-label*='Accept']",
            "button[aria-label*='Close']",
            "button:has-text('Accept All')",
            "button:has-text('I Agree')",
            "button:has-text('Got it')",
            "button:has-text('Accept Cookies')",
            ".modal-close", ".close", "#close"
        ]
        for sel in selectors:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1000):
                await btn.click()
                break
    except Exception:
        pass


async def extract_contacts_from_page(page: Page, base_domain: str) -> Tuple[Set[str], Optional[str], Dict[str, str], List[str]]:
    """Extract emails, phone numbers, social links, and candidate contact page URLs from current page."""
    emails: Set[str] = set()
    phone: Optional[str] = None
    socials: Dict[str, str] = {}
    contact_links: List[str] = []

    try:
        content = await page.content()
    except Exception:
        content = ""

    try:
        text = await page.inner_text("body")
    except Exception:
        text = ""

    soup = BeautifulSoup(content, "html.parser")

    # 1. Look for mailto: links
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith("mailto:"):
            raw_email = href[7:].split("?")[0].strip()
            if is_valid_email(raw_email):
                emails.add(raw_email.lower())

        # Look for tel: links
        if not phone and href.lower().startswith("tel:"):
            raw_tel = href[4:].strip()
            if len(re.sub(r"\D", "", raw_tel)) >= 10:
                phone = raw_tel

        # Look for Social Links
        href_lower = href.lower()
        if "facebook.com/" in href_lower and "facebook.com/sharer" not in href_lower:
            socials.setdefault("facebook", href)
        elif "instagram.com/" in href_lower:
            socials.setdefault("instagram", href)
        elif "linkedin.com/company" in href_lower:
            socials.setdefault("linkedin", href)
        elif "twitter.com/" in href_lower or "x.com/" in href_lower:
            socials.setdefault("twitter", href)

        # Look for contact / about subpage links
        link_text = (a.get_text() or "").lower()
        is_contact_link = any(kw in href_lower for kw in ["/contact", "contact-us", "/about", "about-us", "/reach", "/touch", "/quote"])
        is_contact_text = any(kw in link_text for kw in ["contact", "about us", "get in touch", "reach us", "estimate", "quote"])

        if is_contact_link or is_contact_text:
            full_sub_url = urllib.parse.urljoin(page.url, href)
            # Only stay on same domain
            if extract_domain(full_sub_url) == base_domain and full_sub_url not in contact_links and full_sub_url != page.url:
                contact_links.append(full_sub_url)

    # 2. Decompose script, style, and svg elements to prevent false matches from JS bundles
    for tag in soup(["script", "style", "noscript", "svg", "symbol"]):
        tag.decompose()

    clean_html = str(soup)
    clean_text = soup.get_text(separator=" ", strip=True)

    found_in_text = EMAIL_REGEX.findall(clean_text)
    for e in found_in_text:
        if is_valid_email(e):
            emails.add(e.lower())

    found_in_html = EMAIL_REGEX.findall(clean_html)
    for e in found_in_html:
        if is_valid_email(e):
            emails.add(e.lower())

    # 3. Regex phone if none found
    if not phone:
        phone_match = PHONE_REGEX.search(text)
        if phone_match:
            phone = phone_match.group(0).strip()

    return emails, phone, socials, contact_links


async def scrape_single_lead(
    context: BrowserContext,
    lead: Dict[str, Any],
    max_depth: int,
    check_socials: bool,
    semaphore: asyncio.Semaphore
) -> Dict[str, Any]:
    """Crawl a single business website and extract emails/contacts."""
    async with semaphore:
        target_url = clean_url(lead.get("url") or lead.get("website") or "")
        lead_id = lead.get("id")
        business_name = lead.get("business_name") or lead.get("title") or "Unknown"

        base_domain = extract_domain(target_url)
        result: Dict[str, Any] = {
            "id": lead_id,
            "business_name": business_name,
            "url": target_url,
            "domain": base_domain,
            "primary_email": None,
            "all_emails": [],
            "phone": lead.get("phone"),
            "social_links": {},
            "pages_visited": [],
            "status": "pending"
        }

        if not target_url or not base_domain:
            result["status"] = "error"
            result["error"] = "Invalid URL"
            await Actor.push_data(result)
            return result

        page = await context.new_page()
        all_emails: Set[str] = set()
        pages_visited: List[str] = []

        try:
            Actor.log.info(f"[{business_name}] Visiting homepage: {target_url}")
            await page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
            pages_visited.append(page.url)
            await dismiss_popups(page)

            # Extract from homepage
            h_emails, h_phone, h_socials, sub_links = await extract_contacts_from_page(page, base_domain)
            all_emails.update(h_emails)
            if h_phone and not result["phone"]:
                result["phone"] = h_phone
            result["social_links"].update(h_socials)

            # If no email on homepage, visit internal contact/about pages
            if not all_emails and max_depth > 0:
                # Add default fallbacks if not in sub_links
                default_paths = ["/contact", "/contact-us", "/about", "/about-us"]
                for p in default_paths:
                    candidate = urllib.parse.urljoin(target_url, p)
                    if candidate not in sub_links and candidate not in pages_visited:
                        sub_links.append(candidate)

                for sub_url in sub_links[:max_depth]:
                    if sub_url in pages_visited:
                        continue
                    try:
                        Actor.log.info(f"[{business_name}] Checking contact subpage: {sub_url}")
                        await page.goto(sub_url, wait_until="domcontentloaded", timeout=20000)
                        pages_visited.append(page.url)
                        await dismiss_popups(page)

                        s_emails, s_phone, s_socials, _ = await extract_contacts_from_page(page, base_domain)
                        all_emails.update(s_emails)
                        if s_phone and not result["phone"]:
                            result["phone"] = s_phone
                        result["social_links"].update(s_socials)

                        if all_emails:
                            break
                    except Exception as sub_err:
                        Actor.log.debug(f"[{business_name}] Subpage error {sub_url}: {sub_err}")

            # If still no email, check Facebook page if available and check_socials is enabled
            if not all_emails and check_socials and "facebook" in result["social_links"]:
                fb_url = result["social_links"]["facebook"]
                try:
                    Actor.log.info(f"[{business_name}] Inspecting Facebook page for email: {fb_url}")
                    await page.goto(fb_url, wait_until="domcontentloaded", timeout=20000)
                    pages_visited.append(page.url)
                    fb_content = await page.content()
                    fb_text = await page.inner_text("body")
                    for e in EMAIL_REGEX.findall(fb_text) + EMAIL_REGEX.findall(fb_content):
                        if is_valid_email(e):
                            all_emails.add(e.lower())
                except Exception as fb_err:
                    Actor.log.debug(f"[{business_name}] Facebook inspection error: {fb_err}")

        except Exception as e:
            Actor.log.warning(f"[{business_name}] Homepage navigation failed: {e}")
            result["error"] = str(e)
        finally:
            await page.close()

        ranked = rank_emails(all_emails, base_domain)
        result["all_emails"] = ranked
        result["primary_email"] = ranked[0] if ranked else None
        result["pages_visited"] = pages_visited
        result["status"] = "found" if ranked else ("error" if result.get("error") else "not_found")

        Actor.log.info(f"[{business_name}] Result: {result['status']} | Found email: {result['primary_email'] or 'None'}")
        await Actor.push_data(result)
        return result


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        raw_start_urls = actor_input.get("startUrls", [])
        max_depth = int(actor_input.get("maxDepth", 3))
        check_socials = bool(actor_input.get("checkSocials", True))
        max_concurrency = int(actor_input.get("maxConcurrency", 4))

        # Parse startUrls format (strings or objects with url/id/business_name)
        leads: List[Dict[str, Any]] = []
        for item in raw_start_urls:
            if isinstance(item, str):
                leads.append({"url": item})
            elif isinstance(item, dict):
                leads.append(item)

        if not leads:
            Actor.log.warning("No startUrls provided. Exiting.")
            return

        Actor.log.info(f"Starting Contact Scraper for {len(leads)} target websites with max_concurrency={max_concurrency}")

        # Proxy setup
        proxy_url = None
        proxy_input = actor_input.get("proxyConfiguration")
        if proxy_input and isinstance(proxy_input, dict) and proxy_input.get("useApifyProxy"):
            try:
                proxy_config = await Actor.create_proxy_configuration(actor_proxy_input=proxy_input)
                if proxy_config:
                    proxy_url = await proxy_config.new_url()
            except Exception as e:
                Actor.log.warning(f"Could not initialize proxy: {e}. Running without proxy.")

        launch_args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
        ]

        async with async_playwright() as p:
            proxy_opt = {"server": proxy_url} if proxy_url else None
            browser = await p.chromium.launch(headless=True, args=launch_args, proxy=proxy_opt)
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US"
            )

            semaphore = asyncio.Semaphore(max_concurrency)
            tasks = [
                scrape_single_lead(context, lead, max_depth, check_socials, semaphore)
                for lead in leads
            ]
            await asyncio.gather(*tasks)
            await browser.close()

        Actor.log.info("All target websites processed successfully!")


if __name__ == "__main__":
    asyncio.run(main())
