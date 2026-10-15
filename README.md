# Singapore Watch Radar

Responsive watch-release tracker for Citizen, Orient, Orient Star and Tissot. It uses official release announcements and catalogue pages, official product images where accessible, Singapore-dollar pricing only from official Singapore listings, and a dedicated limited-editions view.

## Current data in this package

`data/releases.json` contains an initial hand-checked snapshot of real official announcements as of 16 October 2026. It includes confirmed global releases and explicitly keeps Singapore availability unconfirmed where the source is global. Tissot has a link to its live Singapore novelties catalogue because its catalogue is dynamic and model-level URLs/prices can change.

This snapshot is not a guarantee that the scheduled workflow has run. The timestamp records the snapshot's source-check date; a successful GitHub Actions run will replace it with the execution time.

## Daily refresh workflow

`.github/workflows/daily-watch-sync.yml` is configured to run daily at 09:15 Singapore time and manually from the Actions tab. `scripts/update_releases.py` checks official Citizen global announcements and Singapore pages, Orient/Orient Star announcements, and Tissot Singapore's novelties catalogue. It writes normalized records to `data/releases.json`.

The source HTML changes from time to time and may block automated requests. The collector is intentionally conservative but must be reviewed after its first scheduled run. Always check failed GitHub Actions runs; a missing record does not prove the brand had no release that day.

## Publish free via GitHub Pages

1. Create a repository named `singapore-watch-radar` under your GitHub account and upload all files in this folder, including hidden `.github` folder.
2. In repository **Settings → Pages**, choose **Deploy from a branch**, branch `main`, folder `/ (root)`.
3. In **Actions**, run **Daily watch release sync** once to test it, then check its logs and verify the generated feed.
4. Open the GitHub Pages URL shown under **Settings → Pages**.

Note: GitHub connector currently shows no repositories for the connected account and doesn't expose a create-repository action, so this project bundle cannot be pushed or published by the connector at this time.

## Data integrity

- Global launch news is not evidence of Singapore stock or allocation.
- SGD prices are shown only when parsed from an official Singapore listing.
- Edition quantities appear only if an official announcement states them.
- Official product images are linked remotely; a source might block hotlinking or change paths.
- Keep official source links on cards, and independently verify current availability before purchase.

## Run locally

```bash
python -m pip install -r requirements.txt
python scripts/update_releases.py
python -m http.server 8000
```

Open `http://localhost:8000`. A static `file://` open may block `fetch()` from `data/releases.json`, so use the local server.
