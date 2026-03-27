# Survey HTML Report: Filters and Charts

**Date:** 2026-03-26
**Scope:** Add interactive period filters and Plotly charts to `reports/survey_health.html`

---

## Goal

The static survey health HTML report currently shows summary stat cards and text lists. This spec adds five period-filter buttons and three Plotly charts so the CIO can slice the data by time window and see satisfaction, trend, and team patterns visually.

---

## Architecture

### Approach: Embedded JSON + vanilla JS re-render

The full survey frame is serialized to a compact JSON blob and embedded in the HTML at report-write time. All filtering and chart rendering happens client-side in vanilla JS with Plotly.js loaded from CDN.

This keeps the report fully self-contained (single `.html` file, no server) and makes the JS/Python boundary clean: Python owns data serialization, JS owns filtering and rendering.

---

## Data Layer

**Serialized columns** (embedded as `window.SURVEY_DATA` JSON array):

| Column | Type | Purpose |
|---|---|---|
| `survey_completed_at` | ISO 8601 string | Period filter anchor |
| `satisfaction_label` | string | Satisfaction mix + score |
| `team_name` | string | Team chart + team list |
| `response_id` | int | Response count |
| `ticket_linked` | bool | Linked response count |
| `comment_text` | string | Recent comments list |

No other columns are included — keeps the blob small and avoids unnecessary data exposure.

---

## Filter Layer

**Five period buttons** rendered as a button group in the report header:

- Last 30 days
- Last 60 days
- Last 90 days
- Last 6 months
- All time *(default)*

Filter anchor: `survey_completed_at`. On click, vanilla JS:
1. Computes a cutoff date from the selected period
2. Filters `window.SURVEY_DATA` rows where `survey_completed_at >= cutoff`
3. Recomputes all summary values (response counts, average score, negative rate, comment rate)
4. Updates stat card DOM values in place
5. Updates satisfaction list, team list, and recent comments table
6. Re-renders all three Plotly charts via `Plotly.react()`

---

## Charts

All charts placed between the stat cards and the existing lists. Loaded via Plotly.js CDN. Re-render on every period change.

### 1. Satisfaction Mix
- **Type:** Horizontal bar
- **X-axis:** Response count
- **Y-axis:** Satisfaction label, ordered positive → negative (Very Satisfied, Satisfied, Dissatisfied, Very Dissatisfied)
- **Purpose:** Immediate read on sentiment distribution for selected period

### 2. Monthly Trend
- **Type:** Dual-axis line
- **Left y-axis:** Response volume (count per month)
- **Right y-axis:** Average satisfaction score (1–5 scale)
- **X-axis:** Month (one point per calendar month)
- **Purpose:** Track volume and sentiment trajectory over time

### 3. Team Volume
- **Type:** Vertical bar
- **X-axis:** Team name (top 10 by response count)
- **Y-axis:** Response count
- **Color:** Average satisfaction score, diverging color scale
- **Purpose:** Identify which teams receive the most survey feedback and how they score

---

## Implementation

### Files changed

**`src/dynamix_manager/reporting.py`** — only file modified:

- `render_survey_health_html(frame)` updated to:
  - Serialize the six columns to a JSON array via `frame[columns].to_json(orient="records", date_format="iso")`
  - Add Plotly.js CDN script tag in `<head>` (latest stable 2.x, version pinned in the tag for reproducibility)
  - Add period filter button group in the report header
  - Add three `<div id="chart-*">` containers between stat cards and lists
  - Add a `<script>` block (~80–100 lines) containing:
    - `filterData(cutoffDate)` — returns filtered row array
    - `computeSummary(rows)` — recomputes stat card values
    - `renderCharts(rows)` — calls `Plotly.react()` for each chart
    - `renderLists(rows)` — updates satisfaction list, team list, recent comments
    - `applyPeriod(days | null)` — orchestrates the above; `null` = All time
    - Event listeners on period buttons
    - Initial `applyPeriod(null)` call on page load

No changes to `write_survey_health_report()` signature or callers. No changes to `notebooks.py`, `pipeline.py`, `cli.py`, or any other module.

### Python package dependencies

No new dependencies. `plotly` Python package is not used in `reporting.py` — charts are constructed entirely in JS from the embedded data. Plotly.js is loaded from CDN.

---

## Tests

**`tests/test_reporting.py`** — two changes:

1. **Update existing test** `test_write_survey_report_html_creates_artifact`: add assertion that `"plotly"` appears in the output HTML.
2. **New test** `test_render_survey_health_html_embeds_json_data`: verify that `"SURVEY_DATA"` appears in the rendered HTML and that the response count from the data is present.

---

## Constraints

- Report remains a single self-contained `.html` file
- No new Python dependencies
- `render_survey_health_html()` and `write_survey_health_report()` signatures unchanged
- All filtering and chart rendering is client-side only
- Plotly.js CDN version pinned (latest stable 2.x at implementation time) for reproducibility
