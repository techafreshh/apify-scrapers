"""Offline check of BetaList actor helpers, including a live discovery pass."""
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.main import (  # noqa: E402
    HEADERS,
    clean_redirect,
    harvest_slugs,
    parse_startup,
    registrable_domain,
    resolve_website,
)

failures = []


def check(label, got, want):
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")


# domain extraction
check("dom www", registrable_domain("https://www.apres.io/?ref=betalist"), "apres.io")
check("dom path", registrable_domain("https://breezing.io/"), "breezing.io")
check("dom co.uk", registrable_domain("https://shop.example.co.uk/x"), "example.co.uk")
check("dom none", registrable_domain(""), "")
check("dom bare", registrable_domain("bigideasdb.com"), "bigideasdb.com")

# redirect cleanup
check("clean ref", clean_redirect("https://bigideasdb.com/?ref=betalist"), "https://bigideasdb.com/")
check("clean frag", clean_redirect("https://a.io/x#y"), "https://a.io/x")
check("clean empty", clean_redirect(""), "")

with httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True) as c:
    # discovery
    r = c.get("https://www.betalist.com/search?q=ai")
    slugs = harvest_slugs(r.text)
    if len(slugs) < 5:
        failures.append(f"harvest_slugs: only {len(slugs)} slugs from live search")
    if len(set(slugs)) != len(slugs):
        failures.append("harvest_slugs: duplicates present")
    print("live slugs:", len(slugs), slugs[:5])

    # detail parse
    if slugs:
        slug = slugs[0]
        d = c.get(f"https://www.betalist.com/startups/{slug}")
        rec = parse_startup(d.text, slug, "ai")
        if not rec:
            failures.append("parse_startup: returned None")
        else:
            for f in ("name", "url", "source", "scraped_at"):
                if not rec.get(f):
                    failures.append(f"parse_startup: missing {f}")
            check("url", rec["url"], f"https://www.betalist.com/startups/{slug}")
            if "BOOSTED" in rec["name"].upper():
                failures.append(f"parse_startup: badge leaked into name: {rec['name']!r}")
            print("sample:", {k: rec.get(k) for k in ("name", "tagline", "boosted")})
            print("desc head:", (rec.get("description") or "")[:110])

        # visit redirect resolution (async client — resolve_website is async)
        import asyncio

        async def _resolve():
            async with httpx.AsyncClient(headers=HEADERS, timeout=30.0) as ac:
                return await resolve_website(ac, slug)

        site, domain = asyncio.run(_resolve())
        if not domain:
            failures.append(f"resolve_website: no domain for {slug}")
        print("website:", site, "| domain:", domain)

if failures:
    for f in failures:
        print("FAIL", f)
    sys.exit(1)
print("BETALIST ACTOR CHECKS PASSED")
