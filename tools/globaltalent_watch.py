#!/usr/bin/env python3
"""GlobalTalentManagerAfrica inventory watcher (GitHub issue #6).

globaltalentmanagerafrica.com is a WordPress site whose custom ``jobs`` post type
is served unauthenticated by WP REST:

    https://globaltalentmanagerafrica.com/wp-json/wp/v2/jobs?per_page=100

As of 2026-11-24 it holds five posts, and they are test content ("Demo …", a body
that literally reads "youll be responsible for eating"). Building a scraper for
that inventory would be building a scraper for nothing, so the issue asks for a
watcher instead: report the current count, the newest few titles, and how many
posts look like *real* listings. Exit code 3 means "real inventory crossed the
threshold" — a scheduler can turn that into a notification.

No third-party dependencies: plain urllib, so this runs anywhere Python does.

Usage:
    python tools/globaltalent_watch.py
    python tools/globaltalent_watch.py --threshold 10 --recent 5
    python tools/globaltalent_watch.py --json
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

API = "https://globaltalentmanagerafrica.com/wp-json/wp/v2/jobs?per_page=100&orderby=date&order=desc"

UA = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Titles/bodies that are placeholder content rather than a job.
JUNK_PATTERNS = [
    re.compile(r"\bdemo\b", re.IGNORECASE),
    re.compile(r"\btest\b", re.IGNORECASE),
    re.compile(r"\blorem ipsum\b", re.IGNORECASE),
    re.compile(r"\bsample\b", re.IGNORECASE),
    re.compile(r"\byou'?ll be responsible for eating\b", re.IGNORECASE),
    re.compile(r"^\W*$"),
]

MIN_REAL_BODY_CHARS = 120


def clean(text: str) -> str:
    """WP REST returns HTML entities and tags in `content.rendered`."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def is_real(post: dict) -> bool:
    title = (post.get("title") or {}).get("rendered", "")
    body = clean((post.get("content") or {}).get("rendered", ""))
    if any(p.search(title) for p in JUNK_PATTERNS):
        return False
    if any(p.search(body) for p in JUNK_PATTERNS[:5]):
        return False
    return len(body) >= MIN_REAL_BODY_CHARS


def fetch_posts() -> list[dict]:
    request = urllib.request.Request(API, headers=UA)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def main() -> int:
    # Windows consoles default to cp1252 and choke on title characters (e.g. "ù").
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Watch GlobalTalentManagerAfrica job inventory")
    parser.add_argument("--threshold", type=int, default=10,
                        help="Alert (exit 3) when real posts reach this count (default 10)")
    parser.add_argument("--recent", type=int, default=3, help="How many newest titles to print")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()

    try:
        posts = fetch_posts()
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code} from {API}", file=sys.stderr)
        return 2
    except (urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"Could not reach {API}: {e}", file=sys.stderr)
        return 2

    real = [p for p in posts if is_real(p)]

    def summary(post: dict) -> dict:
        return {
            "id": post.get("id"),
            "title": clean((post.get("title") or {}).get("rendered", "")),
            "date": post.get("date"),
            "link": post.get("link"),
            "real": is_real(post),
        }

    recent = [summary(p) for p in posts[: max(0, args.recent)]]
    payload = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "total_posts": len(posts),
        "real_posts": len(real),
        "threshold": args.threshold,
        "alert": len(real) >= args.threshold,
        "newest": recent,
        "real_titles": [clean((p.get("title") or {}).get("rendered", "")) for p in real][:10],
    }

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"GlobalTalentManagerAfrica — {payload['checked_at']}")
        print(f"  total posts : {len(posts)}")
        print(f"  real posts  : {len(real)} (real = non-placeholder title/body ≥{MIN_REAL_BODY_CHARS} chars)")
        print(f"  threshold   : {args.threshold} real posts")
        print(f"  newest {len(recent)}:")
        for item in recent:
            mark = "real" if item["real"] else "junk"
            print(f"    [{mark}] {item['date']}  {item['title']}")
        if payload["alert"]:
            print("  ALERT: real inventory crossed the threshold — build the plain-HTTP actor now.")
        else:
            print("  no alert: still parked (no scraper worth building yet).")

    return 3 if payload["alert"] else 0


if __name__ == "__main__":
    sys.exit(main())
