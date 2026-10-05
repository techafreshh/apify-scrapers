# Yellow Pages Leads Scraper

Extract business leads, contact information, and local company registries from YellowPages.com with high speed and reliability.

## Features

- **Fast & Lightweight:** Direct HTTP requests using modern async client (no heavy headless browser required).
- **Rich Lead Fields:** Company name, phone number, website link, physical street address, city, state, business categories, and years in business.
- **Built-in Deduplication:** Automatically filters out duplicate listings within the same search query.
- **Configurable Pagination:** Scrape from 1 up to 50 pages per search run.
- **Proxy Support:** Supports Apify Datacenter and Residential proxy rotation.

## Input Parameters

| Field | Type | Description | Default |
|---|---|---|---|
| `searchTerm` | String | Business category or niche (e.g. `plumbers`, `dentists`, `real estate`) | `plumbers` |
| `location` | String | City, State or ZIP code (e.g. `Austin, TX`, `Miami, FL`) | `Austin, TX` |
| `maxPages` | Number | Maximum number of pagination pages to scrape | `3` |
| `proxyConfiguration` | Object | Optional Apify proxy configuration | None |

## Output Example

Each result pushed to the dataset includes:

```json
{
  "company_name": "Austin Plumbing Pros",
  "phone": "(512) 555-0144",
  "website": "https://austinplumbingpros.com",
  "address": "123 Main St, Austin, TX 78701",
  "categories": [
    "Plumbers",
    "Water Heater Repair",
    "Plumbing-Drain & Sewer Cleaning"
  ],
  "years_in_business": "15",
  "yellowpages_url": "https://www.yellowpages.com/austin-tx/mip/austin-plumbing-pros-123456",
  "source": "yellowpages"
}
```
