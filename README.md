# Singapore Watch Radar

Responsive watch-release tracker for **Citizen, Orient, and Orient Star**. The site combines official release announcements and product catalogues, verified official model imagery where accessible, Singapore-dollar prices only from official Singapore listings, and a dedicated limited-editions view.

Live site: https://angjwjames-source.github.io/-singapore-watch-radar/

## Current catalogue

The generated feed lives in `data/releases.json`. After image recovery and cleanup on 16 October 2026, it contains 138 identifiable watch records:

- Citizen: 61 records
- Orient: 40 records
- Orient Star: 37 records

All 138 feed records currently have a non-fallback image marked verified, with no duplicate image URLs detected in the feed. The main site is designed to display only records with verified, non-fallback imagery. Records without a confirmed Singapore source must not be interpreted as available locally.

The feed's `last_checked_at` timestamp records the most recent completed data collection. Check the workflow before assuming a refresh succeeded.

## Daily refresh workflow

`.github/workflows/daily-watch-sync.yml` runs daily at 09:15 Singapore time and can also be run manually from the Actions tab. `scripts/update_releases.py` checks official Citizen global announcements and Singapore pages, plus Orient and Orient Star announcements and catalogues. It writes normalized records to `data/releases.json`.

Workflow runs: https://github.com/angjwjames-source/-singapore-watch-radar/actions

Official source HTML can change or block automated requests. After a scraper change, inspect the Actions result and feed timestamp, check model references and image uniqueness, and verify the live page.

## Data integrity

- Global launch news is not evidence of Singapore stock, allocation, or local pricing.
- SGD prices should be displayed only when sourced from an official Singapore listing.
- Edition quantities should appear only when stated by an official source.
- Product images are remotely hosted and may stop loading if a source changes its URL or blocks hotlinking.
- Keep official source links on cards and independently verify availability before purchase.
- The main catalogue intentionally hides records without verified model imagery rather than showing generic placeholders.
- Broad announcement summaries without an identifiable model reference are excluded from the Orient and Orient Star watch catalogue.

## Run locally

```bash
python -m pip install -r requirements.txt
python scripts/update_releases.py
python -m http.server 8000
```

Open `http://localhost:8000`. Opening the HTML directly with `file://` may block loading `data/releases.json`, so use a local server.
