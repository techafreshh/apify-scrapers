# BruntWork Careers Scraper

Scrapes the open roles on **bruntworkcareers.co** — BruntWork is a remote-staffing
(BPO) employer, so the board is small but entirely remote-eligible.

Store actor: `bruntwork-scraper` (push with `apify push` from this directory).

## What was ruled out first

| Route | Result |
|---|---|
| `/jobs.json` | 404 — not a Teamtailor/Recruitee feed |
| `__NEXT_DATA__` | absent (Next.js App Router) |
| JSON-LD `JobPosting` | absent |
| Greenhouse / Lever / Ashby / Workable / Recruitee / Teamtailor / SmartRecruiters | no such URLs in the HTML — the ATS is custom or proxied |
| Client-side fetch behind auth | not needed — see below |

The site is a Next.js App Router app, but **both pages we need are server-rendered**:

- `/jobs` returns one `<a href="/jobs/<id>">` card per role with the title and
  commitment (`Full Time (35 hours or more per week)`, `Part Time (20 - 34 …)`).
- `/jobs/<id>` renders the description inside
  `<div class="text-gray-600 job-description">` and the apply link at
  `/jobs/<id>/apply`. The flight payload's job component confirms both
  (`applyUrl`, `jobId`) — useful when the markup class changes.

So this is a plain-HTTP actor: no browser, no proxy, no login, no CAPTCHA.

## Input

| Field | Default | Meaning |
|---|---|---|
| `maxJobs` | 50 | Cap on saved roles (the board currently holds ~16) |
| `fetchDescriptions` | true | Read each role page for description + apply URL |
| `requestDelaySecs` | 1.0 | Pause between role-page requests |

No `timeFilter`: BruntWork publishes no posting dates, and an input that could
never change the output is worse than an honest absence. `posted_at` is always
`null` for this source.

## Output

`external_id, title, company, location, remote, employment_type, timezone_hint,
posted_at, description, description_html, apply_url, source_url, source,
scraped_at`. See `.actor/dataset_schema.json`.

- `company` is always `BruntWork`; `location` is always `Remote` (remote-staffing
  employer; the site never publishes a structured location).
- `timezone_hint` parses the schedule line when the description has one, e.g.
  `Manitoba Time` or `Central European Time - Amsterdam, Netherlands`.
- Geography for eligibility is a downstream decision: the same roles are open to
  offshore staff across regions, and the Nigeria-remote gate should vet them.

## Local run

```bash
# apify run needs a python3 binary in PATH on Windows; the SDK-local equivalent is:
mkdir -p .storage/key_value_stores/default
cp test_input.json .storage/key_value_stores/default/INPUT.json
APIFY_LOCAL_STORAGE_DIR=.storage python -m src.main
# results land in .storage/datasets/default/
```

Verified locally on 2026-11-24: 16/16 role cards parsed, all descriptions and
apply URLs non-empty.
