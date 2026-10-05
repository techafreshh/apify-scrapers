# Google Maps Leads & Business Scraper

Extract local business leads, phone numbers, websites, ratings, reviews, and addresses from Google Maps worldwide.

## Features

- **Global Coverage:** Scrapes businesses in any country, city, or language.
- **Rich B2B Lead Metadata:** Extracts Business Title, Phone Number, Official Website, Rating, Review Count, Category, and Address snippet.
- **Headless Browser Driven:** Powered by Playwright Chromium to seamlessly handle infinite scrolling and dynamic map rendering.
- **Automatic Consent Bypass:** Automatically detects and dismisses Google cookie and GDPR consent modals.
- **Export Ready:** Direct export to JSON, CSV, Excel, or integration with Google Sheets, Make, n8n, and webhooks.

## Input Parameters

| Field | Type | Description | Default |
|---|---|---|---|
| `searchQuery` | String | Keyword and location query (e.g. `plumbers in Austin, TX` or `dentists in London`) | `plumbers in Austin, TX` |
| `maxResults` | Number | Maximum number of business records to harvest | `20` |
| `proxyConfiguration` | Object | Optional Apify proxy settings (Residential or Datacenter) | None |

## Sample Output Record

```json
{
  "title": "Precision Plumbing Solutions",
  "phone": "(512) 555-0199",
  "website": "https://precisionplumbingaustin.com",
  "category": "Plumber",
  "rating": 4.9,
  "reviews_count": 218,
  "address_snippet": "Precision Plumbing Solutions | 4.9 (218) · Plumber | Austin, TX | Open 24 hours",
  "google_maps_url": "https://www.google.com/maps/place/Precision+Plumbing+Solutions/data=...",
  "search_query": "plumbers in Austin, TX",
  "source": "google_maps"
}
```
