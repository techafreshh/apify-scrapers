"""Offline check of Wellfound actor record-building (no browser required)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.main import build_record, normalize_stage, team_size_estimate  # noqa: E402

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# team size bands
check("band 11-50", team_size_estimate("11-50"), 30)
check("band 1-10", team_size_estimate("1-10"), 5)
check("band plus", team_size_estimate("100+"), 100)
check("band single", team_size_estimate("25"), 25)
check("band none", team_size_estimate(""), None)
check("band junk", team_size_estimate("n/a"), None)

# stage normalization
check("st seed", normalize_stage("Seed"), "seed")
check("st preseed", normalize_stage("Pre-Seed"), "pre_seed")
check("st a", normalize_stage("Series A"), "series_a")
check("st b", normalize_stage("Series B"), "series_b")
check("st late", normalize_stage("Late Stage"), "series_d_plus")
check("st boot", normalize_stage("Bootstrapped"), "unknown")
check("st none", normalize_stage(""), "unknown")
check("st junk", normalize_stage("Profitable"), "unknown")

listing = {
    "name": "Vested Finance",
    "slug": "vested-finance-1",
    "url": "https://wellfound.com/company/vested-finance-1",
    "team_size_raw": "51-200",
    "tagline": "US stock investing for global investors",
    "tags": ["B2C", "Growth Stage"],
    "open_roles": 4,
    "logo_url": "https://photos.wellfound.com/x.jpg",
}
detail = {
    "funding_stage": "Series A",
    "team_size": "51-200",
    "headquarters": "Austin, TX",
    "industry": "Finance",
    "founded": "2019",
    "website": "https://vested.finance",
    "description": "Investing app",
}

rec = build_record(listing, detail)
check("name", rec["name"], "Vested Finance")
check("slug", rec["slug"], "vested-finance-1")
check("stage", rec["funding_stage"], "Series A")
check("stage norm", rec["funding_stage_normalized"], "series_a")
check("team est", rec["team_size_estimate"], 125)
check("hq", rec["location"], "Austin, TX")
check("industry", rec["industry"], "Finance")
check("website", rec["website"], "https://vested.finance")
check("open roles", rec["open_roles"], 4)
check("tags", rec["tags"], ["B2C", "Growth Stage"])
check("source", rec["source"], "wellfound_browser")
check("scraped_at", bool(rec["scraped_at"]), True)

# listing-only pass (fetchDetails off) must still produce a usable record
rec2 = build_record(listing, None)
check("listing-only stage", rec2["funding_stage_normalized"], "unknown")
check("listing-only team", rec2["team_size_estimate"], 125)
check("listing-only name", rec2["name"], "Vested Finance")

# open roles parsed from tagline when not in the card
rec3 = build_record({**listing, "open_roles": None,
                     "tagline": "We build things — 12 open positions"}, None)
check("roles from tagline", rec3["open_roles"], 12)

# rejects nameless cards
check("no name rejected", build_record({"name": ""}, detail), None)

if failures:
    for f in failures:
        print("FAIL", f)
    sys.exit(1)
print("WELLFOUND ACTOR CHECKS PASSED")
