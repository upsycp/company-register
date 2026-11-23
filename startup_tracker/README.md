# Indian Startup Tracker

Turns MCA's monthly list of newly incorporated companies into a shortlist of likely early-stage startups,
with founders, website and LinkedIn.

```
MCA monthly .xlsx ─► ingest ─► score/filter ─► enrich ─► output/early_stage_startups_<date>.xlsx
   (inbox/)          (SQLite: tracker.db remembers everything, so each run only reports new companies)
```

## Monthly workflow

1. Download the sheet: mca.gov.in → Data & Reports → Company/LLP Information → **Incorporated Or Closed
   During The Month** → *Master Details of Companies Registered*.
   (MCA blocks scripted downloads, so this one step is manual.)
2. Drop the file in `inbox/`.
3. Run:
   ```bash
   .venv/bin/python tracker.py run
   ```
4. Open the new file in `output/`.

Other commands: `ingest FILE`, `enrich --limit 50`, `export --all`, `import-directors directors.csv`.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # add API keys
```

## How "startup-like" is scored

The CIN itself encodes the industry (NIC code), state, year and company class, e.g.
`U62099KA2026PTC201234` = software, Karnataka, 2026, private limited. The score adds up:

| Signal | Points |
|---|---|
| Tech NIC code (62 software, 631 data/hosting, 582 software publishing, 72 R&D/biotech) | +4 |
| Other new-economy NIC (electronics, EV, med-devices, e-commerce, edtech, fintech) | +1 to +3 |
| Name keyword: labs, AI, tech, robotics, analytics, fintech… | +4 |
| Weaker keywords: digital, data, cloud, health, ventures… | +2 |
| Private Ltd | +1 |
| Paid-up capital ≤ ₹10L | +1 |
| Startup-hub state | +1 |
| Name has traders / constructions / realty / textiles / enterprises… | −4 |
| Listed, Section 8 / non-profit, government company | excluded |

Companies with a score of `SCORE_THRESHOLD` (default 4) or more are shortlisted. Tune the lists at the top of `tracker.py`.

## Enrichment sources

| Field | Source | Needs |
|---|---|---|
| Website | 1) company email domain from the MCA sheet, 2) web search, 3) guessed domains (`name.com/.in/.ai/.io…`), accepted only if the page title matches and it isn't a parked domain | Search is optional |
| Company LinkedIn | Google search: `site:linkedin.com/company` | `SERPER_API_KEY` |
| Founders/directors | Licensed company-data API by CIN | `DIRECTORS_API_*` |
| Founder LinkedIn | Google search: director name + company | `SERPER_API_KEY` |

**Directors:** the monthly MCA sheet does not list directors, and MCA's own lookup is captcha-protected,
so connect a provider such as Probe42, Surepass, Signzy, Attestr or Tofler. Set the URL template and response
field mapping in `.env`. No code changes are needed. You can also import a CSV (`cin,name,din`) with
`import-directors`.

**Caveat:** website matches from a guessed domain or from search are found by name only. Two companies can share
a name, so check the `Website` column before outreach. The `How found` column shows where each website came from.

## Automating

Once keys are set, a monthly cron (or a scheduled task) can run `tracker.py run`. It does nothing if `inbox/` is empty.
