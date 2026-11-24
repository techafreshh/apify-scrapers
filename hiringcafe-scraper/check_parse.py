"""Offline checks for the HiringCafe parser (no browser, no network).

Run from the actor directory:  python check_parse.py
"""

import json
import sys
from datetime import datetime, timedelta, timezone

from src.main import (
    in_window,
    parse_jsonld_postings,
    record_from_dom,
    record_from_jsonld,
    slug_id,
)

FAILS = []


def check(name, condition, detail=""):
    if condition:
        print(f"  ok   {name}")
    else:
        FAILS.append(name)
        print(f"  FAIL {name} {detail}")


SAMPLE_LD = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "Platform Engineer",
    "datePosted": "2026-11-23T10:00:00Z",
    "employmentType": ["FULL_TIME"],
    "jobLocationType": "TELECOMMUTE",
    "hiringOrganization": {"@type": "Organization", "name": "Supabase"},
    "jobLocation": {
        "@type": "Place",
        "address": {
            "@type": "PostalAddress",
            "addressLocality": "Lagos",
            "addressCountry": "Nigeria",
        },
    },
    "applicantLocationRequirements": [{"@type": "Country", "name": "Nigeria"}],
    "baseSalary": {
        "@type": "MonetaryAmount",
        "currency": "USD",
        "value": {"@type": "QuantitativeValue", "minValue": 90000, "maxValue": 130000, "unitText": "YEAR"},
    },
    "description": "<p>Own the platform.</p><ul><li>Postgres</li></ul>",
}

HTML = '<html><head><script type="application/ld+json">%s</script></head></html>' % json.dumps(SAMPLE_LD)

print("jsonld extraction")
parsed = parse_jsonld_postings(HTML)
check("finds JobPosting", parsed is not None)
check("handles @graph", parse_jsonld_postings(
    '<script type="application/ld+json">%s</script>'
    % json.dumps({"@context": "https://schema.org", "@graph": [SAMPLE_LD]})
) is not None)

print("jsonld mapping")
record = record_from_jsonld(parsed, "https://hiringcafe.com/job/platform-engineer-supabase-lagos-abc123")
check("title", record["title"] == "Platform Engineer")
check("company", record["company"] == "Supabase")
check("country", record["country"] == "Nigeria")
check("remote", record["remote"] is True)
check("employment_type", record["employment_type"] == "FULL_TIME")
check("salary min/max", record["salary_min"] == 90000 and record["salary_max"] == 130000)
check("salary currency", record["salary_currency"] == "USD")
check("description has no tags", "<p>" not in (record["description"] or ""))
check("description text kept", "Own the platform." in (record["description"] or ""))
check("external id", record["external_id"] == "abc123")
check("source_url", record["source_url"].startswith("https://hiringcafe.com/job/"))

print("slug ids")
check("slug id suffix", slug_id("/job/cook-na-04-army-fort-kjldtk0gx1d40flz") == "kjldtk0gx1d40flz")
check("non-job path", slug_id("/recently-posted-jobs") is None)

print("dom fallback")
dom = record_from_dom(
    {
        "url": "https://hiringcafe.com/job/backend-dev-acme-berlin-zzz999",
        "title": "Backend Developer at Acme | HiringCafe",
        "og_title": "Backend Developer at Acme",
        "h1": "Backend Developer",
        "text": "Backend Developer\nAcme\nFully remote\n$120,000 - $150,000 a year\nBuild APIs.",
        "external_links": ["https://boards.greenhouse.io/acme/jobs/123"],
    },
    "Backend DeveloperAcmeBerlin, Germany",
)
check("title", dom["title"] == "Backend Developer")
check("company from og:title", dom["company"] == "Acme")
check("remote detected", dom["remote"] is True)
check("salary range parsed", dom["salary_min"] == 120000 and dom["salary_max"] == 150000)
check("apply url", dom["apply_url"] == "https://boards.greenhouse.io/acme/jobs/123")

print("time window")
now = datetime.now(timezone.utc)
check("recent kept", in_window((now - timedelta(hours=2)).isoformat(), now - timedelta(days=1)))
check("old dropped", not in_window((now - timedelta(days=10)).isoformat(), now - timedelta(days=7)))
check("undated kept", in_window(None, now - timedelta(days=7)))
check("any keeps all", in_window("2020-01-01T00:00:00Z", None))

print()
if FAILS:
    print(f"{len(FAILS)} check(s) FAILED: {FAILS}")
    sys.exit(1)
print("all checks passed")
