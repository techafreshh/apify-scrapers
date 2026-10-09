# Google Dorks SERP Scraper

Runs raw **Google dork queries** (`site:`, `inurl:`, `intitle:`, `filetype:`, etc.) through headless Chromium and returns ranked organic results. Built for OSINT, SEO research, and **authorized** security testing — the same extraction pattern as `google-maps-scraper` (Playwright), not the HTTP-only scrapers.

Google aggressively throttles automated search. For production runs **enable Apify Proxy with the RESIDENTIAL group** and keep `maxPagesPerQuery` at 3–5.

## Input

| Field | Type | Default | Notes |
|---|---|---|---|
| `dorkQueries` | string[] | 3 examples | One dork per entry. Paste any valid Google syntax. |
| `maxPagesPerQuery` | int | `3` | SERP pages per dork, 10 results each. Max 10. |
| `maxResults` | int | `60` | Global cap across all dorks. Max 1000. |
| `hl` | string | `en` | Interface language (`hl` param). |
| `gl` | string | `us` | Geo country (`gl` param). |
| `delaySec` | number | `2.5` | Pause between pages. Raise to 4–6s if blocked. |
| `proxyConfiguration` | proxy | — | Use Apify residential proxies for Google. |

Example `test_input.json`:

```json
{
  "dorkQueries": ["site:github.com python asyncio scraper"],
  "maxPagesPerQuery": 2,
  "maxResults": 20,
  "hl": "en",
  "gl": "us"
}
```

## Output (per organic result)

```json
{
  "dork_query": "site:github.com python asyncio scraper",
  "rank": 1,
  "page": 1,
  "title": "…",
  "url": "https://…",
  "display_url": "github.com › …",
  "snippet": "…",
  "serp_url": "https://www.google.com/search?q=…",
  "source": "google_dorks"
}
```

Blocked pages emit a `{"dork_query": …, "status": "blocked", …}` marker row so you can see which dork was throttled.

## Local dev

```bash
apify login
apify run -i test_input.json
apify push
```

## Notes

- Query params force `filter=0` (no omitted-results hiding) and `pws=0` (no personalization).
- Results deduped by normalized URL across the whole run.
- If a dork returns zero cards the actor ends pagination for that dork — usually end-of-results or a Google markup change (fix selectors in `parse_serp`).
