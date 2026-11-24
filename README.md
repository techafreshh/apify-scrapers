# Apify Scrapers

> **Prerequisite:** To use these scrapers you must have a developer account on [Apify](https://apify.com/). The Developer plan costs a one-time **$6 for 6 months** (sign up at [console.apify.com](https://console.apify.com)) and includes ~$500 in platform usage credits — Actors are run, tested, and deployed through your Apify account, and all scraped datasets are stored there.

Production Apify Actors for lead generation and opportunity scouting. Each actor is standalone, with its own Docker image, input schema, and dataset schema.

Every actor runs **without cookies, logins, or account credentials** — they hit public endpoints, public JSON APIs and browser-grade TLS fingerprints rather than authenticated sessions, so there's no cookie expiry and no account-ban risk.

## Actors

| Actor | What it extracts | Transport |
|---|---|---|
| [`linkedin-jobs-scraper`](linkedin-jobs-scraper/) | Remote job postings, descriptions, hiring criteria, company details | HTTP + browser fallback |
| [`upwork-jobs-scraper`](upwork-jobs-scraper/) | Freelance gigs, skills, budgets, client spend history, verification | Playwright |
| [`google-maps-scraper`](google-maps-scraper/) | Local business leads: phone, website, rating, review count, address | Playwright |
| [`yellowpages-scraper`](yellowpages-scraper/) | Business leads: phone, website, categories, years in business | HTTP |
| [`google-dorks-scraper`](google-dorks-scraper/) | Google dork SERP results: ranked URL, title, snippet per query | Playwright |
| [`betalist-scraper`](betalist-scraper/) | Startup profiles: name, tagline, description, real website domain | HTTP |
| [`indiehackers-scraper`](indiehackers-scraper/) | Indie Hacker products: name, website, tagline, monthly revenue | Playwright |
| [`wellfound-scraper`](wellfound-scraper/) | Wellfound companies: name, headcount band, tagline, open roles | Playwright + proxy |
| [`contact-scraper`](contact-scraper/) | Business contact emails, phones and socials from a website | Playwright |
| [`4dayweek-scraper`](4dayweek-scraper/) | 4-day-week jobs: title, company, location, salary, description | HTTP (public JSON API + JSON-LD) |
| [`bruntwork-scraper`](bruntwork-scraper/) | BruntWork remote roles: title, commitment, description, apply URL | HTTP |
| [`hiringcafe-scraper`](hiringcafe-scraper/) | HiringCafe postings: title, company, location, salary, description, apply URL | Playwright + residential proxy |

Each actor's README documents its own input parameters and output schema.

## Extraction strategy

Actors pick the cheapest transport the target actually allows:

- **Pure HTTP** (`httpx` async, no browser) — LinkedIn guest API, YellowPages, BetaList, 4dayweek (public JSON + Next.js flight payload), BruntWork (server-rendered Next.js HTML). Fast and cheap, but breaks if the target ships JS-rendered markup.
- **Headless browser** (Playwright Chromium) — Google Maps (infinite scroll, consent modals), Upwork (Cloudflare), Indie Hackers, HiringCafe.
- **Browser + residential proxy** — Cloudflare-hard targets (Wellfound profile pages, HiringCafe). Slower and pricier, but survives the protections those sites actually deploy.

Upwork layers **Nuxt 3 state extraction over a DOM fallback**, and 4dayweek layers **JobPosting JSON-LD over the Next.js flight payload**, so restyling upstream degrades gracefully instead of emptying the dataset.

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

On Windows, `apify run` needs a `python3` binary in `PATH` (the Store install only
provides `python`) and the CLI throws a spurious `Cannot read properties of
undefined (reading 'version')` after `push` even when the build succeeded — check
`apify actors info <name>` before assuming failure. The SDK-local equivalent of a
run is:

```bash
mkdir -p .storage/key_value_stores/default
cp test_input.json .storage/key_value_stores/default/INPUT.json
APIFY_LOCAL_STORAGE_DIR=.storage python -m src.main   # results in .storage/datasets/default/
```

Local run state (`.storage/`, datasets, key-value stores) is gitignored — it's regenerated on every `apify run`.

## Tools (not actors)

| Script | Purpose |
|---|---|
| [`tools/globaltalent_watch.py`](tools/globaltalent_watch.py) | Watches GlobalTalentManagerAfrica's WP REST `jobs` endpoint (issue #6). Prints total posts, real-vs-placeholder split and the newest titles; exits `3` when real inventory crosses `--threshold` (default 10). Plain `urllib`, no dependencies. |

## Secrets

Nothing sensitive lives in this repository. Proxy credentials are read at runtime from the `APIFY_PROXY_PASSWORD` environment variable, which is set as an **Actor environment variable or secret** in the Apify console — never committed. `.env`, `token.json`, `credentials.json`, and `*_STATE.json` are gitignored.

Scraped datasets contain PII (names, emails, phone numbers) and live in Apify's dataset storage, not in git.

## Notes on maintenance

These scrapers break when their targets change markup. When a run returns empty or partial results, the fix is almost always a selector change in `src/main.py` — git history is the fastest way to find what a previous patch touched.
