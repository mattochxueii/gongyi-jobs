# Project Rules for AI Agents (AGENTS.md)

This repository is a public-welfare (non-profit) static job aggregation project:
1. **Public Institution Jobs (事业编/国央企公开招聘)**: Pilot in **Henan Province (河南省)**.
2. **High-Benefit Private Enterprises (高福利民企白名单)**: Verified weekend rest / benefit claims only.

All AI models (`Gemini 4 Argon`, `Gemini 4 Hydrogen`) working in this repository MUST follow these rules:

## 1. Non-Negotiable Engineering Rules
- **Schema Lock**: `schemas/job.schema.json`, `schemas/company.schema.json`, and `schemas/source.schema.json` are the single source of truth. Do not invent ad-hoc JSON keys in `data/*.json`.
- **Zero Fake Data**: NEVER insert fabricated/mock job listings into `data/jobs.json` or `data/companies.json`. Test fixtures belong strictly under `tests/fixtures/`.
- **Objective Evidence Only**:
  - For private companies (`enterprise` category), every benefit claim (`benefits_verified` / `benefit_claims`) MUST include a verbatim `evidence_quote` copied from an official page/JD and its `source_url` / `evidence_url`.
  - Do NOT create negative blacklists, unverified rumors, or subjective insults against any company.
- **Keep Official Links**: Every job record MUST carry a valid, clickable `source_url` pointing to the official announcement or official company career page.
- **File Ownership**:
  - `scrapers/` — Python scraping, attachment extraction, hashing, archiving scripts.
  - `site/` — Static frontend (Astro + Tailwind CSS) built for Cloudflare Pages.
  - `data/` — Validated structured JSON datasets (`jobs.json`, `companies.json`, `archive/`).
  - `scripts/` — Data validation and build-prep utilities.
  - `raw_inbox/` — Ignored temporary plain-text files waiting for AI structured extraction.

## 2. Quota & Cost Discipline
- Deterministic scraping, SaaS JSON APIs (Moka / Beisen / Feishu), and HTML/Word/Excel/PDF text extraction MUST be handled in Python (`scrapers/`).
- AI extraction only processes deduplicated, newly added plain-text files inside `raw_inbox/`. Never feed raw full HTML pages to an AI model repeatedly.

## 3. Mandatory Verification Gate
Before reporting any task complete:
- Data/Schema changes: run `python scripts/validate_data.py` and `python -m unittest discover -s tests`.
- Scraper changes: run unit tests + a bounded dry-run against real target URLs (with explicit output summary).
- Site changes: run production static build (`npm run build` inside `site/`) and verify zero build errors.
