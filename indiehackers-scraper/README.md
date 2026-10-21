# Indie Hackers Scraper

Extract Indie Hackers product profiles with a real browser.

## Why a browser

Indie Hackers sits behind Cloudflare bot management. Plain HTTP clients
(`requests`, `httpx`) receive a `Just a moment...` interstitial with HTTP 403,
even with full browser headers — the block is based on TLS fingerprint, not
headers. This actor drives Playwright/Chromium and optionally routes through an
Apify residential proxy.

## What it extracts

Per product: name, slug, profile URL, external website, tagline, meta
description, monthly revenue (raw string plus a USD-normalized number) and the
full metrics strip (posts, followers, upvotes).

## Input

| Field | Type | Default | Notes |
|---|---|---|---|
| `sortings` | string[] | `["recently-added","highest-revenue","recently-updated"]` | Directory listings to crawl |
| `maxProducts` | int | `40` | Cap across all sortings |
| `useProxy` | bool | `true` | Recommended — datacenter IPs get challenged |
| `proxyGroups` | string[] | `["BUYPROXIES94952"]` | Apify Proxy groups |
| `proxyConfiguration` | object | — | Overrides `useProxy`/`proxyGroups` |

## Output

One record per product, written to the default dataset. See
`.actor/dataset_schema.json`. Useful views: `name`, `tagline`, `website`,
`monthly_revenue`, `monthly_revenue_usd`, `url`.

## Run locally

Playwright browsers are preinstalled in the Docker image only. Locally:

```bash
pip install -r requirements.txt
playwright install --with-deps chromium
apify run
```

## Run on Apify

```bash
apify push
echo '{"maxProducts": 5}' | apify call techafresh/indiehackers-scraper --silent --output-dataset
```

Or from Python:

```python
from apify_client import ApifyClient

client = ApifyClient("<your token>")
run = client.actor("techafresh/indiehackers-scraper").call(run_input={"maxProducts": 20})
items = client.dataset(run.default_dataset_id).list_items().items
```

## Notes / limitations

- The directory page links to product profiles without login; some deeper
  features (full revenue history, interviews) do require an account and are not
  scraped.
- If a run starts returning very few products, the IP is likely being
  challenged — confirm `useProxy` is on, or rotate proxy groups.
