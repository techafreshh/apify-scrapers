# Apify Scrapers

> **Prerequisite:** To use these scrapers you must have a developer account on [Apify](https://apify.com/). Sign up free at [console.apify.com](https://console.apify.com) — every Actor is run, tested, and deployed through your Apify account, and all scraped datasets are stored there.

Production Apify Actors for lead generation and opportunity scouting. Four standalone scrapers, each with its own Docker image, input schema, and dataset schema.

All four run **without cookies, logins, or account credentials** — they hit public endpoints and browser-grade TLS fingerprints rather than authenticated sessions, so there's no cookie expiry and no account-ban risk.

## Actors

| Actor | Store ID | What it extracts |
|---|---|---|
| [`linkedin-jobs-scraper`](linkedin-jobs-scraper/) | `tweKK21J43FhTE8FO` | Remote job postings, full descriptions, hiring criteria, company details |
| [`upwork-jobs-scraper`](upwork-jobs-scraper/) | `T9kIGjwzvgE3qHXxf` | Freelance gigs, skills, budgets, client spend history, verification status |
| [`google-maps-scraper`](google-maps-scraper/) | `urrEtlATpXz0VDKg5` | Local business leads, phone, website, rating, review count, address |
| [`yellowpages-scraper`](yellowpages-scraper/) | `TDeaCVxV9BiCGh0re` | Business leads, phone, website, categories, years in business |
| [`google-dorks-scraper`](google-dorks-scraper/) | `RR5TOBdPlAUzlJWd7` | Google dork SERP results: ranked URL, title, snippet per query |

Each actor's README documents its own input parameters and output schema.

## Extraction strategy

The four actors deliberately use two different approaches, because the targets demand it:

- **Pure HTTP** (`httpx` async, no browser) — LinkedIn guest API and YellowPages. Fast and cheap, but breaks if the target ships JS-rendered markup.
- **Headless browser** (Playwright Chromium) — Google Maps (infinite scroll, consent modals) and Upwork (Cloudflare). Slower and pricier, but survives the protections those sites actually deploy.

Upwork layers **Nuxt 3 state extraction over a DOM fallback**, so restyling upstream doesn't kill the pipeline.

## Repository layout

```
<actor-name>/
  src/main.py              # Actor entrypoint
  .actor/
    actor.json             # Name, version, Dockerfile build config
    input_schema.json      # Form schema shown in the Apify console
    dataset_schema.json    # Output record schema (not all actors emit one)
  Dockerfile
  requirements.txt
  test_input.json          # Local smoke-test payload
  README.md                # Per-actor input/output docs
```

## Local development

Requires the [Apify CLI](https://docs.apify.com/cli/docs).

```bash
apify login

# Smoke-test with the checked-in sample input
apify run -i test_input.json

# Push local changes to the store
apify push
```

Local run state (`.storage/`, datasets, key-value stores) is gitignored — it's regenerated on every `apify run`.

## Secrets

Nothing sensitive lives in this repository. Proxy credentials are read at runtime from the `APIFY_PROXY_PASSWORD` environment variable, which is set as an **Actor environment variable or secret** in the Apify console — never committed. `.env`, `token.json`, `credentials.json`, and `*_STATE.json` are gitignored.

Scraped datasets contain PII (names, emails, phone numbers) and live in Apify's dataset storage, not in git.

## Notes on maintenance

These scrapers break when their targets change markup. When a run returns empty or partial results, the fix is almost always a selector change in `src/main.py` — git history is the fastest way to find what a previous patch touched.
