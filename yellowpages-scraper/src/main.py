import asyncio
import urllib.parse
from typing import Any, Dict, List, Optional
from bs4 import BeautifulSoup
import httpx
from apify import Actor


def extract_card(card: BeautifulSoup) -> Optional[Dict[str, Any]]:
    """Extract lead details from a single Yellow Pages card."""
    name_el = card.select_one("a.business-name") or card.select_one(".info h2 a")
    if not name_el:
        return None

    name = name_el.get_text(strip=True)
    detail_url = name_el.get("href", "")
    if detail_url and detail_url.startswith("/"):
        detail_url = f"https://www.yellowpages.com{detail_url}"

    phone_el = card.select_one(".phones") or card.select_one(".phone")
    phone = phone_el.get_text(strip=True) if phone_el else None

    website_el = card.select_one("a.track-visit-website")
    website = website_el.get("href") if website_el else None

    street_el = card.select_one(".street-address")
    street = street_el.get_text(strip=True) if street_el else ""

    locality_el = card.select_one(".locality")
    locality = locality_el.get_text(strip=True) if locality_el else ""

    full_address = f"{street}, {locality}".strip(", ") if (street or locality) else None

    # Categories / Tags
    cat_els = card.select(".categories a")
    categories = [c.get_text(strip=True) for c in cat_els if c.get_text(strip=True)]

    # Years in business badge
    years_el = card.select_one(".years-in-business .number") or card.select_one(".years-in-business")
    years_in_business = years_el.get_text(strip=True) if years_el else None

    return {
        "company_name": name,
        "phone": phone,
        "website": website,
        "address": full_address,
        "categories": categories,
        "years_in_business": years_in_business,
        "yellowpages_url": detail_url,
        "source": "yellowpages",
    }


async def main() -> None:
    async with Actor:
        actor_input = await Actor.get_input() or {}
        search_term = actor_input.get("searchTerm", "plumbers")
        location = actor_input.get("location", "Austin, TX")
        max_pages = int(actor_input.get("maxPages", 3))

        Actor.log.info(
            f"Starting Yellow Pages Scraper: Query='{search_term}', Location='{location}', MaxPages={max_pages}"
        )

        # Setup proxy only if explicitly requested
        proxy_url = None
        proxy_input = actor_input.get("proxyConfiguration")
        if proxy_input and isinstance(proxy_input, dict) and proxy_input.get("useApifyProxy"):
            try:
                proxy_configuration = await Actor.create_proxy_configuration(actor_proxy_input=proxy_input)
                if proxy_configuration:
                    proxy_url = await proxy_configuration.new_url()
            except Exception as e:
                Actor.log.warning(f"Could not initialize proxy: {e}. Proceeding without proxy.")

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        total_scraped = 0
        seen_names = set()

        async with httpx.AsyncClient(proxy=proxy_url, headers=headers, timeout=20.0, follow_redirects=True) as client:
            for page in range(1, max_pages + 1):
                params = {
                    "search_terms": search_term,
                    "geo_location_terms": location,
                    "page": page,
                }
                encoded_query = urllib.parse.urlencode(params)
                target_url = f"https://www.yellowpages.com/search?{encoded_query}"

                Actor.log.info(f"Scraping page {page}/{max_pages}: {target_url}")

                try:
                    resp = await client.get(target_url)
                    if resp.status_code != 200:
                        Actor.log.warning(f"Failed to fetch page {page}: HTTP {resp.status_code}")
                        break

                    soup = BeautifulSoup(resp.text, "html.parser")
                    cards = soup.select(".search-results .result") or soup.select(".organic .result")

                    if not cards:
                        Actor.log.info(f"No results found on page {page}. Ending crawl.")
                        break

                    page_items: List[Dict[str, Any]] = []
                    for card in cards:
                        item = extract_card(card)
                        if not item:
                            continue

                        # Deduplicate within this run
                        dedup_key = f"{item['company_name']}_{item['phone']}".lower()
                        if dedup_key in seen_names:
                            continue
                        seen_names.add(dedup_key)

                        page_items.append(item)

                    if page_items:
                        await Actor.push_data(page_items)
                        total_scraped += len(page_items)
                        Actor.log.info(f"Page {page}: Saved {len(page_items)} leads (Total so far: {total_scraped})")
                    else:
                        Actor.log.info(f"Page {page}: No new unique leads.")

                    # Friendly pause between pages
                    await asyncio.sleep(1.0)

                except Exception as e:
                    Actor.log.error(f"Error fetching page {page}: {e}")
                    break

        Actor.log.info(f"Crawl completed! Total businesses harvested: {total_scraped}")


if __name__ == "__main__":
    asyncio.run(main())
