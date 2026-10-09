"""Unit tests for parse_dom_tiles — the DOM fallback parser in src/main.py.

These tests lock in the payment-verification badge fix (PR #2): the flag must
be derived only from exact badge phrases on <li> elements, never from
substring matches over the article or the job-description free text.

Run with:  python -m pytest upwork-jobs-scraper/tests -v
"""

import sys
from pathlib import Path

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from main import parse_dom_tiles  # noqa: E402


def _tile(
    lis=(),
    title="Build an AI automation workflow",
    description="We need someone to automate our sales pipeline.",
    uid="2106075078980555987",
):
    """Build a minimal Upwork job tile as HTML."""
    li_html = "".join(f"<li>{text}</li>" for text in lis)
    desc_html = f'<p class="text-body">{description}</p>' if description is not None else ""
    return f"""
    <article data-ev-job-uid="{uid}">
        <h2 class="job-tile-title">{title}</h2>
        {desc_html}
        <ul>{li_html}</ul>
    </article>
    """


def _parse(html, query="AI Automation"):
    return parse_dom_tiles(BeautifulSoup(html, "html.parser"), query)


# --- Payment verification badge (the CRITICAL fix) -------------------------


def test_unverified_badge_must_not_match_substring():
    """'verified' in 'payment unverified' must NOT flip the flag to True."""
    job = _parse(_tile(lis=["Payment unverified"]))[0]
    assert job["client"]["is_payment_verified"] is False


def test_verified_badge_sets_true():
    job = _parse(_tile(lis=["Payment verified"]))[0]
    assert job["client"]["is_payment_verified"] is True


def test_no_badge_defaults_false():
    """Tiles without any badge keep the pre-PR default of False."""
    job = _parse(_tile(lis=["$15.00-$35.00 Hourly"]))[0]
    assert job["client"]["is_payment_verified"] is False


def test_description_free_text_does_not_flip_flag():
    """Job-description text mentioning 'verified' must not count as a badge."""
    job = _parse(_tile(
        description="You must have verified accounts to apply.",
        lis=["$500.00 Fixed-price"],
    ))[0]
    assert job["client"]["is_payment_verified"] is False


def test_description_unverified_phrase_does_not_flip_flag():
    """Even the exact phrase in description text is not a badge."""
    job = _parse(_tile(
        description="Payment unverified clients are ok.",
        lis=["$500.00 Fixed-price"],
    ))[0]
    assert job["client"]["is_payment_verified"] is False


def test_unverified_badge_wins_over_verified_text_elsewhere():
    """Badge check is scoped to <li> elements; a later 'unverified' li wins."""
    job = _parse(_tile(lis=["Payment verified", "Payment unverified"]))[0]
    assert job["client"]["is_payment_verified"] is False


# --- Other <li> signals must keep working alongside the badge check --------


def test_badge_parsing_does_not_disturb_hourly_budget_and_tier():
    job = _parse(_tile(lis=[
        "Payment verified",
        "$15.00-$35.00 Hourly",
        "Expert",
    ]))[0]
    assert job["client"]["is_payment_verified"] is True
    assert job["job_type"] == "hourly"
    assert job["budget_min"] == 15.0
    assert job["budget_max"] == 35.0
    assert job["hourly_budget"] == {"min": 15.0, "max": 35.0}
    assert job["fixed_amount"] is None
    assert job["experience_level"] == "Expert"


def test_fixed_price_budget_parsing():
    job = _parse(_tile(lis=["$500.00 Fixed-price"]))[0]
    assert job["job_type"] == "fixed"
    assert job["fixed_amount"] == 500.0
    assert job["hourly_budget"] is None


# --- Tile assembly ----------------------------------------------------------


def test_missing_title_skips_tile():
    html = """
    <article data-ev-job-uid="123">
        <ul><li>$500.00 Fixed-price</li></ul>
    </article>
    """
    assert _parse(html) == []


def test_missing_description_element_yields_empty_description():
    job = _parse(_tile(description=None))[0]
    assert job["description"] == ""


def test_dataset_shape_matches_readme_contract():
    job = _parse(_tile(lis=["Payment verified", "$16.00-$25.00 Hourly", "Less than 1 month"]))[0]
    assert set(job.keys()) == {
        "id", "ciphertext", "title", "url", "description", "skills",
        "job_type", "budget_min", "budget_max", "hourly_budget",
        "fixed_amount", "experience_level", "duration", "client",
        "published_at", "search_query", "source",
    }
    assert set(job["client"].keys()) == {
        "country", "is_payment_verified", "total_spent", "total_reviews", "rating",
    }
    assert job["source"] == "upwork"
    assert job["search_query"] == "AI Automation"
    assert job["url"] == "https://www.upwork.com/jobs/~022106075078980555987"


def test_multiple_tiles_parsed_independently():
    html = _tile(uid="111", lis=["Payment verified"]) + _tile(uid="222", lis=["Payment unverified"])
    jobs = _parse(html)
    by_id = {job["id"]: job for job in jobs}
    assert by_id["111"]["client"]["is_payment_verified"] is True
    assert by_id["222"]["client"]["is_payment_verified"] is False