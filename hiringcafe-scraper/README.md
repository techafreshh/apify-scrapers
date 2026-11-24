# HiringCafe Job Scraper

Scrapes [HiringCafe](https://hiringcafe.com/) — an aggregator of ~4.5M open jobs
with the best remote/worldwide filtering of the boards reviewed — with a real
browser through an Apify **residential proxy**.

Store actor: `hiringcafe-scraper` (push with `apify push` from this directory).

## Why a browser + residential proxy

Verified 2026-11-24:

| Route | Result |
|---|---|
| Plain `curl` / `urllib` | **HTTP 403** (Cloudflare) — even `robots.txt` and the sitemaps |
| TinyFish server-side fetch | `bot_blocked` |
| Headless Chrome from a home IP | listing passed once, then every navigation hit "Just a moment…" |
| Browser + residential proxy | the supported route (Cloudflare's expected signal) |

## robots.txt compliance

Only paths HiringCafe's `robots.txt` **allows** are requested:

- allowed: `/job`, `/job/`, `/jobs/`, `/recently-posted-jobs/`, the six sitemaps
- never touched: `/applyclick`, `/api/applyclick/`, `/api/sponsored/`, `/*?page=*`,
  `/b/`, `/company/`, `/org/`, `/req/`

Discovery uses `/recently-posted-jobs` plus bounded infinite scrolling — not
`?page=` pagination, and not the JSON APIs.

## Flow

1. Open `/recently-posted-jobs`, wait out the Cloudflare interstitial (title/body
   challenge markers), scroll up to `maxScrolls` times, collect `/job/<slug>`
   links and their card text (title, company, location, date).
2. For each link, open the posting and read its schema.org `JobPosting` JSON-LD:
   title, company, description, `baseSalary`, `jobLocation`,
   `applicantLocationRequirements`, `datePosted`, `employmentType`,
   `jobLocationType: TELECOMMUTE`.
3. Fallback when a posting has no JSON-LD: `h1` / `og:title` for title and company,
   the `<main>` text for the description, an external ATS link
   (Greenhouse/Lever/Ashby/Workable/…) for `apply_url`, and a `$X - $Y` regex for
   salary.
4. Emit one row per posting. Geography is published as-is; the downstream
   Nigeria-remote gate decides eligibility.

Cloudflare handling is **non-fatal**: a page that is still challenged after the
retry ladder (3 navigations, up to ~30s of waiting each) is counted in
`blocked` and skipped. If the listing itself never clears, the run logs an error
and exits with 0 rows instead of crashing.

## Input

| Field | Default | Meaning |
|---|---|---|
| `queries` | `[]` | Keyword filter on card text (title/company/location) |
| `maxJobs` | 50 | Postings saved (one proxy page load each) |
| `timeFilter` | `past_week` | `past_24h` / `past_week` / `past_month` / `any` (undated postings kept) |
| `maxScrolls` | 6 | List scrolls before harvesting links |
| `requestDelaySecs` | 3 | Pause between posting loads |
| `headless` | true | Turn off to see what Cloudflare is showing |
| `proxyGroups` | `["RESIDENTIAL"]` | Used when `proxyConfiguration` is absent |
| `proxyConfiguration` | residential | Full proxy config; overrides `proxyGroups` |

If residential access is unavailable on the account, switch `proxyGroups` to
`["BUYPROXIES94952"]` or `["StaticUS3"]` and expect more challenges.

## Output

`external_id, title, company, location, cities, countries, country, remote,
employment_type, salary_min, salary_max, salary_currency, salary_period,
posted_at, description, apply_url, source_url, card_text, source, scraped_at`.
See `.actor/dataset_schema.json`.

## Verification status

- **Parsers:** 24 offline checks pass — `python check_parse.py` (JSON-LD incl.
  `@graph`, slug ids, DOM fallback, time window).
- **Live:** pending a proxy run. The parsing was written against the schema.org
  shape HiringCafe publishes for indexed postings plus a DOM fallback, so a
  change in their markup degrades rather than empties the dataset. Run once from
  the console and check the `blocked` count in the log before scheduling.

## Notes

- `apply_url` is the destination embedded in the posting page when present. The
  `/applyclick` redirect is robots-disallowed and is never requested.
- `description` is capped at 40k characters to keep dataset rows sane.
