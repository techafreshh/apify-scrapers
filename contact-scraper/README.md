# Business Contact & Email Scraper

Deep contact and email discovery scraper for local business and SMB websites.
Extracts verified business email addresses, phone numbers, contact subpages, and social media links.

## Features

- **Headless Browser Rendering**: Uses Playwright Chromium to bypass Cloudflare bot checks, render JavaScript/SPAs (Wix, Squarespace, Webflow), and decode obfuscated contact forms.
- **Deep Contact Crawling**: Automatically checks the homepage, `/contact`, `/contact-us`, `/about`, and `/about-us` subpages if no email is found initially.
- **Social Media Fallback**: Captures Facebook, Instagram, LinkedIn, and Twitter links. When enabled, inspects linked Facebook pages where small contractors regularly post direct contact emails.
- **Intelligent Email Filtering & Ranking**: Eliminates bogus placeholder emails (`example.com`, `user@domain`), image artifacts (`.png`, `.jpg`), and vendor SDKs (`sentry.io`, `wixpress.com`). Prioritizes domain-matched emails and direct business inboxes (`info@`, `contact@`, `service@`).
- **Real-Time Streaming**: Pushes results directly to the Apify dataset as each website is processed.
