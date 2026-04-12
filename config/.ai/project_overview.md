# Project Overview

## Purpose
- Crawl a target website (same domain + subdomains), generate a structured sitemap, and use OpenAI to extend and analyze that sitemap.

## Major Components
- `nightmare.py`: CLI entrypoint containing crawler, sitemap builder, and OpenAI analysis flow.

## Major Dependencies
- `scrapy` for crawling.
- `openai` Python SDK for Responses API calls.
- `tldextract` (optional fallback logic present) for root-domain extraction.

## Runtime Model
- User runs CLI with a starting URL.
- Script crawls internal links up to a page cap.
- Script writes sitemap JSON to disk.
- Script performs two OpenAI requests: URL expansion and final report.
