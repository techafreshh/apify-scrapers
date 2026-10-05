# Upwork Jobs & Freelance Lead Scraper

Fast, reliable, and headless Upwork job search scraper. Extract high-intent freelance gigs, client spending history, required skills, and budget parameters without cookies, account credentials, or rate limits.

---

## Features

- **No Cookies or Logins Required:** Scrapes directly using browser-grade TLS fingerprinting, bypassing Cloudflare protections without needing an Upwork account.
- **Deep Client Intelligence:** Extracts client location, total spend, review counts, average feedback rating, and payment verification status.
- **Comprehensive Job Data:**
  - Full title and un-truncated description
  - Ciphertext job URL for direct application
  - Skills & tags taxonomy
  - Job type (hourly vs. fixed-price)
  - Hourly rate ranges ($/hr) or fixed contract amounts ($)
  - Experience tier required (Entry, Intermediate, Expert)
  - Project duration and weekly commitment estimates
- **Nuxt 3 State De-duplication + DOM Fallback:** Built with dual extraction layers so changes in UI styling never break your data pipeline.
- **Fast & Lightweight:** Built on `curl_cffi` and Python 3.11, pulling up to 50 jobs per single page request with sub-second execution speeds.

---

## Input Parameters

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `queries` | Array of strings | `["n8n", "AI Automation"]` | List of job keywords or search phrases to scrape |
| `maxJobs` | Integer | `30` | Maximum total job postings to extract across all queries |
| `sort` | String (`recency` / `relevance`) | `recency` | Order of job postings |
| `paymentVerifiedOnly` | Boolean | `false` | Filter strictly for jobs posted by payment-verified clients |
| `contractorTier` | String (`any`, `entry`, `intermediate`, `expert`) | `any` | Filter by required client experience level |
| `proxyConfiguration` | Object | `{}` | Optional proxy configuration |

### Example Input

```json
{
  "queries": [
    "AI Automation",
    "n8n",
    "Make.com"
  ],
  "maxJobs": 50,
  "sort": "recency",
  "paymentVerifiedOnly": true,
  "contractorTier": "any"
}
```

---

## Sample Output

```json
{
  "id": "2106075078980555987",
  "ciphertext": "~022106075078980555987",
  "title": "AI Automation Expert Engineer Needed",
  "url": "https://www.upwork.com/jobs/~022106075078980555987",
  "description": "We are seeking an experienced AI Automation Engineer to help us build and maintain AI-powered workflows and business automation systems using n8n and Make.com...",
  "skills": [
    "n8n",
    "Artificial Intelligence",
    "AI Agent Development",
    "Workflow Automation"
  ],
  "job_type": "hourly",
  "budget_min": 16.0,
  "budget_max": 25.0,
  "hourly_budget": {
    "min": 16.0,
    "max": 25.0
  },
  "fixed_amount": null,
  "experience_level": "Intermediate",
  "duration": "Less than 1 month",
  "client": {
    "country": "United States",
    "is_payment_verified": true,
    "total_spent": 12500.0,
    "total_reviews": 14,
    "rating": 4.95
  },
  "published_at": "2026-10-02T17:37:22.248Z",
  "search_query": "AI Automation",
  "source": "upwork"
}
```

---

## Integrations & Automation

Perfect for pairing with AI Proposal generators, Slack/Discord notification webhooks, or your CRM pipeline.
