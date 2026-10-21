# BetaList Startups Scraper

Extract BetaList startup profiles with plain HTTP.

## Why no browser

BetaList is a Rails app that server-renders every profile and sits behind **no bot
protection** — a plain `requests.get` returns the full page. So this Actor needs
no browser, no residential proxy, and no per-GB proxy cost. That makes it the
cheapest of the three actors by a wide margin.

## How it works

1. **Discovery** — walks `/search?q=<term>&page=N` and harvests `/startups/<slug>`
   links (~21 per page).
2. **Detail** — reads each profile for name, tagline, description, categories and
   the Boosted flag.
3. **Website resolution** — the real website is *not* in the page HTML. BetaList
   masks it behind `/startups/<slug>/visit`, which answers with a `301` whose
   `Location` header carries the target URL. The actor reads that header with
   `follow_redirects=False` and never contacts the destination site.

The website matters more than it looks: it yields the registrable domain, which is
the strongest key for merging one startup across BetaList, Product Hunt and
funding news.

Note: `betalist.com` (no `www`) answers `/visit` with a single redirect to the
target. The `www` host adds an extra canonicalization hop, so the actor uses the
bare host for website resolution.

## Input

| Field | Type | Default | Notes |
|---|---|---|---|
| `queries` | string[] | `["ai","automation","saas","developer","marketing","productivity"]` | Search terms |
| `maxPagesPerQuery` | int | `2` | Result pages per query |
| `maxStartups` | int | `60` | Total cap |
| `fetchWebsite` | bool | `true` | Resolve the `/visit` redirect (one cheap extra request each) |
| `useProxy` | bool | `false` | Off by default — not needed, and it costs |

## Output

One record per startup: `name`, `slug`, `url`, `website`, `domain`, `tagline`,
`description`, `categories`, `boosted`, `queries`, `scraped_at`.

## Run locally

```bash
pip install -r requirements.txt
apify run
```

## Run on Apify

```bash
apify push
echo '{"maxStartups": 5, "queries": ["ai"]}' | apify call techafresh/betalist-scraper --silent --output-dataset
```

## Notes / limitations

- Discovery is keyword-driven. A startup is only found if it matches one of your
  `queries`, so widen the term list for broader coverage.
- Some profiles expose no `/browse/` breadcrumbs, so `categories` can be `null`.
- Dead startups may still resolve a `/visit` redirect; the domain is still useful
  for deduplication even if the site no longer resolves.
