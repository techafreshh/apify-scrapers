"""Offline check of Indie Hackers actor record-building (no browser required)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.main import build_record, parse_revenue  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# revenue normalization
check("rev k", parse_revenue("Revenue $5K / mo"), 5000.0)
check("rev plain", parse_revenue("Revenue $12,000 / mo"), 12000.0)
check("rev m", parse_revenue("Revenue $1.2M / mo"), 1200000.0)
check("rev eur", parse_revenue("€4.2k / mo"), round(4200 * 1.08, 2))
check("rev none", parse_revenue("no revenue here"), None)
check("rev empty", parse_revenue(""), None)

# record building from a representative DOM payload
raw = {
    "name": "MediaFast",
    "tagline": "Marketing on Reddit without getting suspended",
    "website": "http://redditfa.com",
    "monthly_revenue": "Revenue $5K / mo",
    "description": "People struggle with Reddit",
    "metrics": {"posts": "30+", "followers": "1.2K"},
    "title": "MediaFast on Indie Hackers",
}
url = "https://www.indiehackers.com/product/mediafast-15"
rec = build_record(raw, url, "highest-revenue")

check("name", rec["name"], "MediaFast")
check("slug", rec["slug"], "mediafast-15")
check("website", rec["website"], "http://redditfa.com")
check("revenue label stripped", rec["monthly_revenue"], "$5K / mo")
check("revenue usd", rec["monthly_revenue_usd"], 5000.0)
check("metrics", rec["metrics"]["posts"], "30+")
check("sorting", rec["sorting"], "highest-revenue")
check("source", rec["source"], "indiehackers_browser")
check("scraped_at present", bool(rec["scraped_at"]), True)

# relative website link gets resolved to absolute
rec2 = build_record({**raw, "website": "/somewhere"}, url, "x")
check("relative website", rec2["website"], "https://www.indiehackers.com/somewhere")

# missing name must be rejected (Cloudflare interstitial)
check("no name rejected", build_record({"name": None}, url, "x"), None)
check("empty dict rejected", build_record({}, url, "x"), None)

# missing revenue is fine
rec3 = build_record({**raw, "monthly_revenue": None}, url, "x")
check("no revenue ok", rec3["monthly_revenue_usd"], None)

if failures:
    for f in failures:
        print("FAIL", f)
    sys.exit(1)
print("IH ACTOR CHECKS PASSED")
