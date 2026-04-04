# Project Context

## Purpose

This repo is a notebook-first executive analytics workspace for Cedarville University's TeamDynamix environment. It is designed for CIO-only use, with local caching, Jupyter notebooks for analysis, and HTML artifacts for review.

## Current Data Scope

- TeamDynamix tenant access is configured from `.env`.
- The primary ticketing app for this work is `InfoTech Tickets` with app ID `634`.
- Survey report ID `100482` is the current survey source.
- Survey analysis is constrained to survey results linked to `InfoTech Tickets` tickets.

## Local Storage And Outputs

- Local analytics cache: [analytics.duckdb](/Users/micahcooper/dynamix-manager/data/analytics.duckdb)
- Survey notebook: [survey_health.ipynb](/Users/micahcooper/dynamix-manager/notebooks/survey_health.ipynb)
- Ticket notebook: [ticket_health.ipynb](/Users/micahcooper/dynamix-manager/notebooks/ticket_health.ipynb)
- Ticket quality notebook: [ticket_quality.ipynb](/Users/micahcooper/dynamix-manager/notebooks/ticket_quality.ipynb)
- Survey HTML report: [survey_health.html](/Users/micahcooper/dynamix-manager/reports/survey_health.html)
- Ticket HTML report: [ticket_health.html](/Users/micahcooper/dynamix-manager/reports/ticket_health.html)
- Ticket quality HTML report: [ticket_quality.html](/Users/micahcooper/dynamix-manager/reports/ticket_quality.html)

## Implemented Analytics

### Survey

- Ticket-linked survey model with response and resolution timing.
- `InfoTech Tickets` filtering at the materialized survey model layer.
- Plotly-backed notebook charts for satisfaction mix, monthly trend, and team response volume.
- Survey comments and low-score review sections.

### Ticket Health

- Business-day completion comparisons across teams and members.
- Balanced scorecard sections for:
  - backlog aging
  - response and resolution percentiles
  - SLA health
  - ticket hygiene
  - high-touch tickets
  - stale open tickets
  - team quality hotspots
  - backlog load hotspots
  - recurring issue candidates
  - quality-adjusted SLA
- Plotly-backed notebook charts for status mix, team volume, and team completions per business day.

### Ticket Quality

The open-ticket quality checker currently flags:

- last meaningful public interaction is the client
- IT has followed up more than 3 times without a client response
- stale public update
- private activity since the last public update

The quality notebooks and HTML report include these flags and summary counts.

## Calendar Logic

Business-day calculations exclude:

- weekends
- TeamDynamix `days off`
- planned future holidays currently overlaid through `2027-01-01`

## Current Live Cache State

At the last confirmed refresh:

- cached tickets: `341`
- raw survey rows: `6294`
- `InfoTech Tickets`-linked survey rows used for analysis: `341`

The current bounded live open-ticket quality slice returned `0` rows, so quality artifacts are structurally current but may be empty until a later refresh finds open tickets in scope.

## Technical Constraints

- TeamDynamix rate limiting is the main live-ingestion bottleneck.
- Full ticket-detail enrichment should continue in paced batches when richer native ticket fields are needed broadly.
- DuckDB file locking can occur if another Python process has the DB open for writing; temp-file swap is the safe workaround already used in this repo.

## Verification Standard

Before claiming notebook/report changes complete:

- run `pytest`
- run `ruff`
- execute the real notebook artifacts with `jupyter nbconvert --execute`

Recent full verification status:

- `57 passed`
- Ruff clean
- survey, ticket, and ticket-quality notebooks executed successfully end to end

