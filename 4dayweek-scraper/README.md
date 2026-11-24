# 4dayweek.io Job Scraper

Scrapes 4-day-week job postings from 4dayweek.io's **public JSON API** and the
posting pages' embedded data. Plain HTTP — no browser, no proxy, no login.

Store actor: `4dayweek-scraper` (push with `apify push` from this directory).

## Why plain HTTP

`https://4dayweek.io/api/jobs?page=N` answers anonymously with JSON (HTTP 200,
`application/json`, ~25 rows/page, 38k+ rows total). Nothing on the path is
bot-protected.

## What the API does *not* do

- **No server-side filtering.** `?work_arrangement=remote` and friends are
  ignored — every variant returns the same `total`. All filtering happens inside
  the Actor.
- **No descriptions.** The listing carries title, company, locations, salary,
  schedule, category and level, but the body text lives on `/job/<slug>`.
- **Early access.** Rows inserted after the API's `meta.early_access_cutoff`
  (roughly the last two days) come back `locked: true` with an empty
  `company_name` and `slug` — paywalled and unusable. They are skipped unless
  `includeLocked` is on.

## Detail pass

For each row that will be emitted, the Actor reads `/job/<slug>` once:

1. **JSON-LD first** — indexable pages carry a schema.org `JobPosting` block
   (`description`, `baseSalary`, `applicantLocationRequirements`, `skills`,
   `industry`, `datePosted`).
2. **Next.js flight fallback** — non-indexable pages (`robots: noindex`) ship no
   JSON-LD. Their description is a React flight text row
   (`9c:T1c08,<markdown>`) referenced from the job object as
   `"description":"$9c"`; the Actor resolves the row and its hex length.
3. **Meta description** — last resort, truncated (~200 chars).

Anonymous visitors cannot see the apply destination: the payload flags
`apply_url_withheld: true`, so `apply_url` stays `null` and the flag records why.

## Input

| Field | Default | Meaning |
|---|---|---|
| `maxJobs` | 100 | Cap on saved postings |
| `timeFilter` | `past_week` | `past_24h` / `past_week` / `past_month` / `any`, applied to the posting date |
| `workArrangement` | `remote` | `remote` / `hybrid` / `onsite` / `any` (a row counts as remote if the job or any location says so) |
| `regions` | `[]` | Optional allow-list matched against countries and continents, case-insensitive |
| `includeLocked` | false | Keep early-access/paywalled rows |
| `fetchDetails` | true | Read each emitted posting's page for description + JSON-LD |
| `maxPages` | 60 | Safety cap on API pages walked |
| `requestDelaySecs` | 1.5 | Pause between requests (the API drops connections under burst load) |

The feed is roughly newest-first, so the Actor stops after 3 consecutive pages
entirely outside the time window instead of walking all ~1,500 pages.

## Output

One row per posting: `external_id, title, company, company_url, country,
continent, countries, continents, remote, work_arrangement, schedule_type,
employment_type, category, level, industry, skills, timezones, stack, salary,
salary_min, salary_max, salary_currency, salary_period,
applicant_location_requirements, work_life_score, posted_at, inserted_at,
expired, locked, url, apply_url, apply_url_withheld, description, source,
scraped_at`. See `.actor/dataset_schema.json`.

## Local run

```bash
# apify run needs a python3 binary in PATH on Windows; the SDK-local equivalent is:
mkdir -p .storage/key_value_stores/default
cp test_input.json .storage/key_value_stores/default/INPUT.json
APIFY_LOCAL_STORAGE_DIR=.storage python -m src.main
# results land in .storage/datasets/default/
```

## Notes

- Geography is reported as-is (this is a UK/EU-heavy board). The downstream
  Nigeria-remote gate decides eligibility; use `regions` to narrow it here.
- Salary comes from the API when present, then is refreshed from the JSON-LD's
  `baseSalary` when the detail page has one.
