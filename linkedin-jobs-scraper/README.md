# LinkedIn Jobs & Remote Career Scraper

Extract verified job postings, requirements, company details, and hiring criteria directly from LinkedIn without cookies, logins, or accounts.

## Features

- **No Authentication Required:** Scrapes directly through public LinkedIn guest API endpoints. Zero risk of account flags, blocks, or cookie expirations.
- **Deep Job Metadata:** Extracts Job ID, Role Title, Company Name, Location, Application Link, Full Description (with formatted paragraphs), and Job Criteria (Seniority, Employment Type, Industries).
- **Multi-Query Support:** Pass multiple search queries in a single run (e.g. `["AI Engineer", "n8n", "Automation"]`).
- **Targeted Filters:** Filter by remote-only postings and publication recency (`past_24h`, `past_week`, `past_month`).
- **High Speed & Low Cost:** Pure asynchronous HTTP requests (no heavy headless browser required), maximizing speed and minimizing cloud credit consumption.

## Input Parameters

| Field | Type | Description | Default |
|---|---|---|---|
| `queries` | Array | List of job keywords / titles to search for | `["AI Automation", "Python Developer"]` |
| `location` | String | Target search location | `Worldwide` |
| `remoteOnly` | Boolean | Limit search to remote-eligible positions | `true` |
| `timeFilter` | String | Date posted filter (`past_24h`, `past_week`, `past_month`, `any`) | `past_week` |
| `maxJobs` | Number | Maximum total jobs to extract | `30` |
| `fetchDetails` | Boolean | Whether to fetch the full job description and hiring criteria | `true` |
| `proxyConfiguration` | Object | Optional Apify proxy configuration | None |

## Sample Output Record

```json
{
  "id": "4168923011",
  "title": "Senior AI Automation Engineer",
  "company": "NextGen Automations",
  "location": "Worldwide (Remote)",
  "url": "https://www.linkedin.com/jobs/view/4168923011",
  "posted_at": "2026-10-01",
  "description": "We are seeking a Senior AI Automation Engineer to lead workflow design using n8n, Python, and LLM APIs...",
  "criteria": [
    "Seniority level: Mid-Senior level",
    "Employment type: Full-time",
    "Job function: Engineering and Information Technology",
    "Industries: Technology, Information and Internet"
  ],
  "search_query": "AI Automation",
  "source": "linkedin_guest"
}
```
