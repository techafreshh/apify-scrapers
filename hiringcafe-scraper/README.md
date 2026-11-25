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

1. Open `/recently-posted-jobs` with real Chrome (headful under Xvfb) through a
   residential proxy, wait out the Cloudflare interstitial, scroll up to
   `maxScrolls` times, and collect `/job/<slug>` links with their card leaves.
   `innerText` collapses the card into "TitleCompanyLocation · date", so the
   parser reads the leaf elements to keep title / company / location apart.
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
| `maxJobs` | 50 | Postings saved |
| `fetchDetails` | **false** | Open each posting for description/salary/apply URL. Blocked by Cloudflare today — see Verification status |
| `listingAttempts` | 3 | Fresh proxy sessions when the listing is blocked |
| `timeFilter` | `past_week` | `past_24h` / `past_week` / `past_month` / `any` (undated postings kept) |
| `maxScrolls` | 6 | List scrolls before harvesting links |
| `requestDelaySecs` | 3 | Pause between posting loads |
| `headless` | false | Keep off: the image runs under Xvfb, and headless is hard-blocked |
| `diagnostics` | — | List of URLs to probe (status/title/body) and stop |
| `proxyGroups` | `["RESIDENTIAL"]` | Used when `proxyConfiguration` is absent |
| `proxyConfiguration` | residential | Full proxy config; overrides `proxyGroups` |

If residential access is unavailable on the account, switch `proxyGroups` to
`["BUYPROXIES94952"]` or `["StaticUS3"]` and expect more challenges.

## Output

`external_id, title, company, location, cities, countries, country, remote,
employment_type, salary_min, salary_max, salary_currency, salary_period,
posted_at, description, apply_url, source_url, card_text, source, scraped_at`.
See `.actor/dataset_schema.json`.

## Verification status (live, 2026-11-25)

| Path | Real Chrome + residential proxy | Verdict |
|---|---|---|
| `/recently-posted-jobs` | **HTTP 200**, full card list | allowed — this is the dataset |
| `/jobs/<role>` (sitemap) | HTTP 403 "Just a moment…" challenge, never clears | blocked |
| `/job/<slug>` (posting) | HTTP 403, then "Sorry, you have been blocked" (WAF) | blocked |

Also tried and rejected: plain HTTP 403, TinyFish `bot_blocked`, headless Chrome
from a home IP, Playwright Chromium, real Chrome (headful under Xvfb), **Patchright**
(stealth Playwright), and the `UNBLOCKER` / `BUYPROXIES94952` proxy groups (tunnel
errors or the same 403). Cloudflare's WAF matches the request shape on posting
paths, not just the IP.

**Consequence:** the actor works in list-only mode — `fetchDetails` defaults to
**false**. Each row carries title, company, location and posted date parsed from
the listing card (positional: title / company / `city, region, country · Mon DD`)
plus the posting URL. `description` and `apply_url` are `null` because the only
page carrying them is blocked; `detail_blocked` records whether a detail fetch was
attempted and blocked.

If Cloudflare relaxes, set `fetchDetails: true` and the existing JSON-LD/DOM
pipeline fills description + salary + apply URL again.

## Notes

- `apply_url` comes from the posting page; the `/applyclick` redirect is
  robots-disallowed and is never requested.
- `description` is capped at 40k characters.
- `diagnostics` input: pass a list of URLs to get HTTP status/title/body per URL
  and stop — the fastest way to re-check which paths Cloudflare still allows.
