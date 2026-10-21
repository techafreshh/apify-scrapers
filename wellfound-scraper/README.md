# Wellfound Scraper

Extract Wellfound (formerly AngelList) startup profiles with a real browser.

## Why a browser

Wellfound applies aggressive bot detection, and company profile pages are
client-rendered SPAs (the raw HTML is an ~11KB shell with no company data).
Listing pages are server-rendered, so the listing pass is cheap; the per-company
detail pass needs JavaScript and is optional.

## Two passes

1. **Listing pass** — reads `h3 a[href^="/company/"]` cards and captures name,
   slug, headcount band, tagline, tags (industry + market stage) and open roles.
2. **Detail pass** (optional) — visits each company page for funding stage,
   HQ, industry, website and description.

Because Wellfound ships hashed CSS class names, detail extraction reads labelled
values (`Funding Stage`, `Team Size`, `Headquarters`) out of the rendered text
rather than relying on stable selectors, which keeps it resilient to styling
changes.

## How this clears Cloudflare

- **Headful by default.** Headless Chromium is detected immediately; headful
  (via the image's `xvfb`) passes. This is the single most important setting.
- **Residential proxy.** Datacenter IPs are challenged.
- **`wait_until="commit"` + challenge poll**, with a 3-attempt retry that
  relaunches the browser each time — relaunching rotates the proxy IP, which is
  what rescues a run that draws a bad IP.
- Once the challenge clears, the listing is re-requested through the browser
  context (`context.request.get`) so we parse the full server-rendered HTML
  instead of the smaller client-hydrated grid.

## Known limitations (please read)

- **Company profile pages return a hard Cloudflare `403 SECURITY CHECK`** that
  requires interactive verification. Headful + residential proxy does *not* pass
  it. `fetchDetails` therefore defaults to `false` and will usually be blocked;
  `funding_stage`, `location`, `industry` and `website` are normally `null`.
- **`/jobs` only exposes ~4 company cards per load.** The page contains ~52
  `/company/*` links, but ~48 of them are job rows ("Engineering jobs") rather
  than company cards. `?page=N` does not paginate server-side. So a run yields
  roughly 4–8 companies, not hundreds.

Net effect: this actor reliably contributes **company name + headcount band** and
little else. It is the weakest of the three actors for funding-targeting, since
headcount is a secondary signal compared with raise recency and amount. Run it
occasionally; do not treat it as a primary funding source.

## Input

| Field | Type | Default | Notes |
|---|---|---|---|
| `listingUrls` | string[] | `["https://wellfound.com/jobs"]` | Listing pages to crawl |
| `maxCompanies` | int | `25` | Cap on companies |
| `fetchDetails` | bool | `false` | Usually blocked — see limitations |
| `headless` | bool | `false` | Leave off; headful passes Cloudflare |
| `useProxy` | bool | `true` | Recommended |
| `proxyGroups` | string[] | `["BUYPROXIES94952"]` | Apify Proxy groups |
| `proxyConfiguration` | object | — | Overrides `useProxy`/`proxyGroups` |

## Output

One record per company. Key fields: `name`, `funding_stage`,
`funding_stage_normalized`, `team_size`, `team_size_estimate`, `location`,
`industry`, `tags`, `open_roles`, `website`, `url`.

`team_size_estimate` is the midpoint of the headcount band as an integer, which
is what scoring actually needs — `11-50` becomes `30`.

## Run locally

```bash
pip install -r requirements.txt
playwright install --with-deps chromium
apify run
```

## Run on Apify

```bash
apify push
echo '{"maxCompanies": 3}' | apify call techafresh/wellfound-scraper --silent --output-dataset
```

## Notes / limitations

- Cost scales with `maxCompanies` when `fetchDetails` is on, since each company
  costs a full page load plus a hydration wait. Keep it modest for periodic runs.
- If a run returns zero companies, the IP is likely blocked — confirm `useProxy`
  is on, or rotate proxy groups.
