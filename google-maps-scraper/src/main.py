import asyncio
import re
import urllib.parse
from typing import Any, Dict, List, Optional
from apify import Actor
from playwright.async_api import async_playwright, Page


PHONE_REGEX = re.compile(
    r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}"
)


async def accept_consent(page: Page) -> None:
    """Dismiss Google cookie/privacy consent modal if it appears."""
    try:
        consent_selector = (
            "form[action*='consent'] button, "
            "button[aria-label*='Accept all'], "
            "button:has-text('Accept all'), "
            "button:has-text('I agree')"
        )
        button = page.locator(consent_selector).first
        if await button.is_visible(timeout=2500):
            await button.click()
            await page.wait_for_timeout(1000)
    except Exception:
        pass


def parse_card_info(raw_text: str) -> Dict[str, Any]:
    """Extract secondary info like phone, address, and category from text snippets."""
    lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
    phone = None
    category = None
    address = None

    phone_match = PHONE_REGEX.search(raw_text)
    if phone_match:
        phone = phone_match.group(0).strip()

    # Search for Category: typically following rating (e.g. '4.9(913)Dentist ·')
    cat_match = re.search(r'(?:\d\.\d|\([\d,]+\))\s*([A-Za-z\s&/-]{3,30}?)\s*·', raw_text)
    if cat_match:
        category = cat_match.group(1).strip()

    # Search for Street Address: typically preceded by '·' and followed by Open/Closed
    addr_match = re.search(r'·\s*([0-9]+\s+[A-Za-z0-9\s.,#-]+?)(?:\s*·|\s+Open|\s+Closed|$)', raw_text)
    if addr_match:
        clean_addr = addr_match.group(1).replace("", "").strip(" ·")
        if len(clean_addr) > 3:
            address = clean_addr

    return {
        "phone": phone,
        "category": category,
        "address": address,
        "raw_snippet": " | ".join(lines[:4]) if lines else raw_text[:200]
    }


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        search_query = actor_input.get("searchQuery", "plumbers in Austin, TX")
        max_results = int(actor_input.get("maxResults", 20))

        Actor.log.info(
            f"Starting Google Maps Scraper: Query='{search_query}', Target Results={max_results}"
        )

        # Proxy setup (only if explicitly enabled)
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
            page = await context.new_page()

            # Google Maps search URL with English language forced
            encoded_query = urllib.parse.quote_plus(search_query)
            target_url = f"https://www.google.com/maps/search/{encoded_query}?hl=en"

            Actor.log.info(f"Navigating to {target_url}")
            await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
            await accept_consent(page)

            # Wait for search feed to appear
            feed_selector = "div[role='feed']"
            single_result_selector = "h1.DUwDvf"

            try:
                await page.wait_for_selector(
                    f"{feed_selector}, {single_result_selector}", timeout=15000
                )
            except Exception:
                Actor.log.warning("No listings feed detected. Checking page content...")

            results: List[Dict[str, Any]] = []
            seen_urls = set()

            # Case 1: Check if Google Maps redirected to a single exact business listing
            single_heading = page.locator(single_result_selector)
            if await single_heading.count() > 0 and await single_heading.first.is_visible():
                title = await single_heading.first.text_content()
                curr_url = page.url
                Actor.log.info(f"Single exact match found: {title}")
                lead = {
                    "title": title.strip() if title else "Unknown Business",
                    "url": curr_url,
                    "search_query": search_query,
                    "source": "google_maps"
                }
                await Actor.push_data(lead)
                Actor.log.info("Finished single result extraction.")
                await browser.close()
                return

            # Case 2: List of search results
            feed = page.locator(feed_selector)
            if await feed.count() == 0:
                Actor.log.error("Could not find the results feed element.")
                await browser.close()
                return

            Actor.log.info("Scrolling results feed to harvest listings...")

            # Scroll loop
            scroll_attempts = 0
            max_scroll_attempts = max(15, (max_results // 5) + 5)

            while len(results) < max_results and scroll_attempts < max_scroll_attempts:
                # Select all current result cards
                cards = page.locator("div.Nv2PK")
                card_count = await cards.count()

                for i in range(card_count):
                    if len(results) >= max_results:
                        break

                    card = cards.nth(i)
                    try:
                        # Title and Link
                        link_el = card.locator("a.hfpxzc").first
                        if await link_el.count() == 0:
                            continue

                        detail_url = await link_el.get_attribute("href") or ""
                        clean_url = detail_url.split("?")[0] if detail_url else ""

                        if not clean_url or clean_url in seen_urls:
                            continue

                        title = await link_el.get_attribute("aria-label") or ""
                        if not title:
                            title_el = card.locator("div.qBF1Pd").first
                            if await title_el.count() > 0:
                                title = await title_el.text_content() or ""

                        title = title.strip()
                        if not title:
                            continue

                        seen_urls.add(clean_url)

                        # Rating and Reviews Count
                        rating = None
                        reviews_count = None
                        rating_el = card.locator("span.MW4etd").first
                        if await rating_el.count() > 0:
                            r_txt = await rating_el.text_content()
                            try:
                                rating = float(r_txt.strip()) if r_txt else None
                            except ValueError:
                                pass

                        reviews_el = card.locator("span.UY7F9").first
                        if await reviews_el.count() > 0:
                            rev_txt = await reviews_el.text_content()
                            if rev_txt:
                                rev_clean = re.sub(r"[^\d]", "", rev_txt)
                                reviews_count = int(rev_clean) if rev_clean else None

                        # Website button on card
                        website = None
                        website_el = card.locator("a[aria-label*='Website'], a[data-value='Website'], a.lcr4fd").first
                        if await website_el.count() > 0:
                            website = await website_el.get_attribute("href")

                        # Card text extraction for phone, category, and address
                        try:
                            full_card_text = await card.inner_text()
                        except Exception:
                            full_card_text = await card.text_content() or ""

                        parsed_meta = parse_card_info(full_card_text)

                        record = {
                            "title": title,
                            "phone": parsed_meta["phone"],
                            "website": website,
                            "category": parsed_meta["category"],
                            "address": parsed_meta["address"],
                            "rating": rating,
                            "reviews_count": reviews_count,
                            "address_snippet": parsed_meta["raw_snippet"],
                            "google_maps_url": clean_url,
                            "search_query": search_query,
                            "source": "google_maps"
                        }

                        results.append(record)
                        await Actor.push_data(record)
                        Actor.log.info(f"Harvested ({len(results)}/{max_results}): {title} - {parsed_meta['phone'] or 'No phone'}")

                    except Exception as e:
                        Actor.log.debug(f"Error reading card {i}: {e}")
                        continue

                # Scroll down inside feed element
                scroll_attempts += 1
                await feed.evaluate("el => el.scrollBy(0, 1000)")
                await page.wait_for_timeout(1200)

                # Check if reached the end
                end_indicator = page.locator("span:has-text(\"You've reached the end of the list\")")
                if await end_indicator.count() > 0 and await end_indicator.first.is_visible():
                    Actor.log.info("Reached the end of Google Maps results.")
                    break

            Actor.log.info(f"Google Maps scrape completed! Total extracted: {len(results)} businesses.")
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
