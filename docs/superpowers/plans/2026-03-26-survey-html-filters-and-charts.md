# Survey HTML Filters and Charts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add five period-filter buttons and three Plotly charts (satisfaction mix, monthly trend, team volume) to `reports/survey_health.html`, with all filtering done client-side in vanilla JS from an embedded JSON data blob.

**Architecture:** `render_survey_health_html(frame)` in `reporting.py` is updated to (1) serialize six survey columns to a `window.SURVEY_DATA` JSON blob, (2) add Plotly.js CDN and period buttons to the HTML, and (3) embed a vanilla JS block that filters the data and re-renders charts and stat cards on button click. No new Python dependencies. No other source files change.

**Tech Stack:** Python 3.13, pandas, Plotly.js 3.4.0 (CDN), vanilla JS, Tailwind CDN

---

### Task 1: Add `_serialize_survey_rows` helper

**Files:**
- Modify: `src/dynamix_manager/reporting.py`
- Modify: `tests/test_reporting.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_reporting.py`:

```python
from dynamix_manager.reporting import _serialize_survey_rows
import json

def test_serialize_survey_rows_emits_required_columns():
    frame = pd.DataFrame([
        {
            "response_id": 1,
            "survey_completed_at": pd.Timestamp("2026-01-15", tz="UTC"),
            "satisfaction_label": "Very Satisfied",
            "team_name": "Client Services",
            "ticket_linked": True,
            "comment_text": "Great service",
        },
        {
            "response_id": 2,
            "survey_completed_at": None,
            "satisfaction_label": "Dissatisfied",
            "team_name": None,
            "ticket_linked": False,
            "comment_text": None,
        },
    ])
    result = _serialize_survey_rows(frame)
    rows = json.loads(result)
    assert len(rows) == 2
    assert rows[0]["response_id"] == 1
    assert rows[0]["satisfaction_label"] == "Very Satisfied"
    assert "survey_completed_at" in rows[0]
    assert "team_name" in rows[0]
    assert "ticket_linked" in rows[0]
    assert "comment_text" in rows[0]
    # Columns not in frame should not appear
    assert "created_at" not in rows[0]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
.venv/bin/python -m pytest tests/test_reporting.py::test_serialize_survey_rows_emits_required_columns -v
```

Expected: FAIL with `ImportError` (function not yet defined)

- [ ] **Step 3: Write minimal implementation**

Add to `src/dynamix_manager/reporting.py` (before `render_survey_health_html`):

```python
_SURVEY_JSON_COLUMNS = [
    "response_id",
    "survey_completed_at",
    "satisfaction_label",
    "team_name",
    "ticket_linked",
    "comment_text",
]


def _serialize_survey_rows(frame: pd.DataFrame) -> str:
    columns = [c for c in _SURVEY_JSON_COLUMNS if c in frame.columns]
    return frame[columns].to_json(orient="records", date_format="iso")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/python -m pytest tests/test_reporting.py::test_serialize_survey_rows_emits_required_columns -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dynamix_manager/reporting.py tests/test_reporting.py
git commit -m "feat: add survey row serializer for html json blob"
```

---

### Task 2: Update `render_survey_health_html` with Plotly CDN, period buttons, chart divs, and JS

**Files:**
- Modify: `src/dynamix_manager/reporting.py`
- Modify: `tests/test_reporting.py`

The existing `render_survey_health_html(frame)` function returns a plain HTML string. This task rewrites the HTML template inside that function to embed the JSON blob, add Plotly CDN, add period filter buttons, add chart `<div>` containers, add `id` attributes to stat card values, add `id` attributes to list `<ul>` elements, and append a `<script>` block with all filtering and rendering logic.

- [ ] **Step 1: Write the failing tests**

Update `tests/test_reporting.py` — replace the existing `test_render_survey_health_html_includes_tailwind_and_summary_metrics` and add two new tests:

```python
def test_render_survey_health_html_includes_tailwind_and_summary_metrics():
    frame = pd.DataFrame(
        [
            {
                "response_id": 1,
                "ticket_linked": True,
                "satisfaction_label": "Very Satisfied",
                "comment_text": "Helpful support",
                "team_name": "Client Services",
                "survey_completed_at": pd.Timestamp("2026-01-15", tz="UTC"),
            },
            {
                "response_id": 2,
                "ticket_linked": False,
                "satisfaction_label": "Dissatisfied",
                "comment_text": None,
                "team_name": None,
                "survey_completed_at": pd.Timestamp("2026-02-01", tz="UTC"),
            },
        ]
    )

    html = render_survey_health_html(frame)

    assert "https://cdn.tailwindcss.com" in html
    assert "Survey Health" in html
    assert "Average score" in html
    assert "Negative response rate" in html
    # Plotly CDN present
    assert "cdn.plot.ly" in html
    # JSON data blob present
    assert "SURVEY_DATA" in html
    # Period filter buttons present
    assert "Last 30" in html
    assert "All time" in html
    # Chart containers present
    assert 'id="chart-satisfaction-mix"' in html
    assert 'id="chart-monthly-trend"' in html
    assert 'id="chart-team-volume"' in html


def test_render_survey_health_html_stat_cards_have_js_ids():
    frame = pd.DataFrame([{
        "response_id": 1,
        "ticket_linked": True,
        "satisfaction_label": "Very Satisfied",
        "comment_text": "Good",
        "team_name": "Help Desk",
        "survey_completed_at": pd.Timestamp("2026-01-15", tz="UTC"),
    }])
    html = render_survey_health_html(frame)
    assert 'id="stat-total"' in html
    assert 'id="stat-linked"' in html
    assert 'id="stat-comments"' in html
    assert 'id="stat-avg-score"' in html
    assert 'id="stat-neg-rate"' in html
    assert 'id="satisfaction-list"' in html
    assert 'id="team-list"' in html
    assert 'id="comment-list"' in html
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/test_reporting.py::test_render_survey_health_html_includes_tailwind_and_summary_metrics tests/test_reporting.py::test_render_survey_health_html_stat_cards_have_js_ids -v
```

Expected: FAIL (missing CDN, SURVEY_DATA, period buttons, chart divs, stat IDs)

- [ ] **Step 3: Rewrite `render_survey_health_html` in `src/dynamix_manager/reporting.py`**

Replace the entire `render_survey_health_html` function with the following:

```python
def render_survey_health_html(frame: pd.DataFrame) -> str:
    summary = summarize_survey_health(frame)
    total_responses = summary["total_responses"]
    linked_responses = summary["linked_responses"]
    comment_count = summary["comment_count"]
    average_score = summary["average_score"]
    negative_response_rate = summary["negative_response_rate"]

    survey_json = _serialize_survey_rows(frame)
    avg_score_display = f"{average_score:.2f}" if average_score is not None else "N/A"

    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Survey Health</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script src="https://cdn.plot.ly/plotly-3.4.0.min.js"></script>
  </head>
  <body class="min-h-screen bg-stone-100 text-stone-900">
    <main class="mx-auto max-w-6xl px-6 py-10">
      <section class="mb-6">
        <p class="text-sm uppercase tracking-[0.3em] text-stone-500">TeamDynamix Analytics</p>
        <h1 class="mt-2 text-4xl font-semibold">Survey Health</h1>
        <p class="mt-3 max-w-2xl text-stone-600">Ticket-linked survey pulse with a compact executive summary and high-signal breakdowns.</p>
      </section>

      <section class="mb-8 flex flex-wrap gap-2">
        <button data-period="30"  class="rounded-lg border border-stone-300 bg-white px-4 py-2 text-sm font-medium text-stone-700 hover:bg-stone-50">Last 30 days</button>
        <button data-period="60"  class="rounded-lg border border-stone-300 bg-white px-4 py-2 text-sm font-medium text-stone-700 hover:bg-stone-50">Last 60 days</button>
        <button data-period="90"  class="rounded-lg border border-stone-300 bg-white px-4 py-2 text-sm font-medium text-stone-700 hover:bg-stone-50">Last 90 days</button>
        <button data-period="180" class="rounded-lg border border-stone-300 bg-white px-4 py-2 text-sm font-medium text-stone-700 hover:bg-stone-50">Last 6 months</button>
        <button data-period=""    class="rounded-lg border border-indigo-600 bg-indigo-600 px-4 py-2 text-sm font-medium text-white">All time</button>
      </section>

      <section class="grid gap-4 md:grid-cols-3">
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <p class="text-sm text-stone-500">Responses</p>
          <p id="stat-total" class="mt-3 text-3xl font-semibold">{total_responses}</p>
        </article>
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <p class="text-sm text-stone-500">Ticket-linked</p>
          <p id="stat-linked" class="mt-3 text-3xl font-semibold">{linked_responses}</p>
        </article>
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <p class="text-sm text-stone-500">Comments</p>
          <p id="stat-comments" class="mt-3 text-3xl font-semibold">{comment_count}</p>
        </article>
      </section>
      <section class="mt-4 grid gap-4 md:grid-cols-2">
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <p class="text-sm text-stone-500">Average score</p>
          <p id="stat-avg-score" class="mt-3 text-3xl font-semibold">{avg_score_display}</p>
        </article>
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <p class="text-sm text-stone-500">Negative response rate</p>
          <p id="stat-neg-rate" class="mt-3 text-3xl font-semibold">{negative_response_rate:.1%}</p>
        </article>
      </section>

      <section class="mt-8 grid gap-6 lg:grid-cols-2">
        <article class="rounded-2xl bg-white p-4 shadow-sm">
          <div id="chart-satisfaction-mix" style="height:280px"></div>
        </article>
        <article class="rounded-2xl bg-white p-4 shadow-sm">
          <div id="chart-monthly-trend" style="height:280px"></div>
        </article>
      </section>
      <section class="mt-6">
        <article class="rounded-2xl bg-white p-4 shadow-sm">
          <div id="chart-team-volume" style="height:300px"></div>
        </article>
      </section>

      <section class="mt-8 grid gap-6 lg:grid-cols-2">
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Top Satisfaction Labels</h2>
          <ul id="satisfaction-list" class="mt-4"></ul>
        </article>
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Top Linked Teams</h2>
          <ul id="team-list" class="mt-4"></ul>
        </article>
      </section>
      <section class="mt-6">
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Recent Comments</h2>
          <ul id="comment-list" class="mt-4"></ul>
        </article>
      </section>
    </main>

    <script>
      window.SURVEY_DATA = {survey_json};
    </script>
    <script>
    (function () {{
      var SCORE_MAP = {{"Very Satisfied": 5, "Satisfied": 4, "Dissatisfied": 2, "Very Dissatisfied": 1}};
      var NEGATIVE = new Set(["Very Dissatisfied", "Dissatisfied"]);
      var LABEL_ORDER = ["Very Satisfied", "Satisfied", "Dissatisfied", "Very Dissatisfied"];

      function filterData(days) {{
        if (!days) return window.SURVEY_DATA;
        var cutoff = new Date();
        cutoff.setDate(cutoff.getDate() - days);
        return window.SURVEY_DATA.filter(function (r) {{
          return r.survey_completed_at && new Date(r.survey_completed_at) >= cutoff;
        }});
      }}

      function computeSummary(rows) {{
        var total = rows.length;
        var linked = rows.filter(function (r) {{ return r.ticket_linked; }}).length;
        var comments = rows.filter(function (r) {{ return r.comment_text && String(r.comment_text).trim(); }}).length;
        var scores = rows.map(function (r) {{ return SCORE_MAP[r.satisfaction_label]; }}).filter(function (s) {{ return s !== undefined; }});
        var avgScore = scores.length ? scores.reduce(function (a, b) {{ return a + b; }}, 0) / scores.length : null;
        var negRate = total ? rows.filter(function (r) {{ return NEGATIVE.has(r.satisfaction_label); }}).length / total : 0;
        return {{ total: total, linked: linked, comments: comments, avgScore: avgScore, negRate: negRate }};
      }}

      function renderStatCards(s) {{
        document.getElementById("stat-total").textContent = s.total;
        document.getElementById("stat-linked").textContent = s.linked;
        document.getElementById("stat-comments").textContent = s.comments;
        document.getElementById("stat-avg-score").textContent = s.avgScore !== null ? s.avgScore.toFixed(2) : "N/A";
        document.getElementById("stat-neg-rate").textContent = (s.negRate * 100).toFixed(1) + "%";
      }}

      function renderSatisfactionMix(rows) {{
        var counts = {{}};
        LABEL_ORDER.forEach(function (l) {{ counts[l] = 0; }});
        rows.forEach(function (r) {{ if (r.satisfaction_label in counts) counts[r.satisfaction_label]++; }});
        var labels = LABEL_ORDER.filter(function (l) {{ return counts[l] > 0; }});
        Plotly.react("chart-satisfaction-mix", [{{
          type: "bar", orientation: "h",
          x: labels.map(function (l) {{ return counts[l]; }}),
          y: labels,
          marker: {{ color: "#6366f1" }}
        }}], {{
          margin: {{ l: 140, r: 20, t: 30, b: 30 }},
          title: {{ text: "Satisfaction Mix", font: {{ size: 14 }} }},
          xaxis: {{ title: "Responses" }},
          yaxis: {{ autorange: "reversed" }}
        }}, {{ responsive: true }});
      }}

      function renderMonthlyTrend(rows) {{
        var monthly = {{}};
        rows.forEach(function (r) {{
          if (!r.survey_completed_at) return;
          var d = new Date(r.survey_completed_at);
          var key = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0");
          if (!monthly[key]) monthly[key] = {{ count: 0, scores: [] }};
          monthly[key].count++;
          var s = SCORE_MAP[r.satisfaction_label];
          if (s !== undefined) monthly[key].scores.push(s);
        }});
        var keys = Object.keys(monthly).sort();
        var counts = keys.map(function (k) {{ return monthly[k].count; }});
        var avgs = keys.map(function (k) {{
          var sc = monthly[k].scores;
          return sc.length ? sc.reduce(function (a, b) {{ return a + b; }}, 0) / sc.length : null;
        }});
        Plotly.react("chart-monthly-trend", [
          {{ x: keys, y: counts, type: "scatter", mode: "lines+markers", name: "Responses", yaxis: "y1" }},
          {{ x: keys, y: avgs, type: "scatter", mode: "lines+markers", name: "Avg Score", yaxis: "y2", line: {{ dash: "dot" }} }}
        ], {{
          margin: {{ l: 50, r: 60, t: 30, b: 50 }},
          title: {{ text: "Monthly Trend", font: {{ size: 14 }} }},
          yaxis: {{ title: "Responses" }},
          yaxis2: {{ title: "Avg Score", overlaying: "y", side: "right", range: [1, 5] }},
          legend: {{ orientation: "h", y: -0.2 }}
        }}, {{ responsive: true }});
      }}

      function renderTeamVolume(rows) {{
        var teams = {{}};
        rows.filter(function (r) {{ return r.ticket_linked; }}).forEach(function (r) {{
          var t = r.team_name || "Unassigned";
          if (!teams[t]) teams[t] = {{ count: 0, scores: [] }};
          teams[t].count++;
          var s = SCORE_MAP[r.satisfaction_label];
          if (s !== undefined) teams[t].scores.push(s);
        }});
        var sorted = Object.entries(teams).sort(function (a, b) {{ return b[1].count - a[1].count; }}).slice(0, 10);
        var labels = sorted.map(function (e) {{ return e[0]; }});
        var counts = sorted.map(function (e) {{ return e[1].count; }});
        var avgs = sorted.map(function (e) {{
          var sc = e[1].scores;
          return sc.length ? sc.reduce(function (a, b) {{ return a + b; }}, 0) / sc.length : null;
        }});
        Plotly.react("chart-team-volume", [{{
          type: "bar", x: labels, y: counts,
          marker: {{ color: avgs, colorscale: "RdYlGn", cmin: 1, cmax: 5, showscale: true, colorbar: {{ title: "Avg Score", thickness: 15 }} }}
        }}], {{
          margin: {{ l: 50, r: 80, t: 30, b: 120 }},
          title: {{ text: "Team Volume (top 10 by responses)", font: {{ size: 14 }} }},
          xaxis: {{ tickangle: -30 }},
          yaxis: {{ title: "Responses" }}
        }}, {{ responsive: true }});
      }}

      function renderSatisfactionList(rows) {{
        var counts = {{}};
        LABEL_ORDER.forEach(function (l) {{ counts[l] = 0; }});
        rows.forEach(function (r) {{ if (r.satisfaction_label in counts) counts[r.satisfaction_label]++; }});
        var el = document.getElementById("satisfaction-list");
        el.innerHTML = LABEL_ORDER.filter(function (l) {{ return counts[l] > 0; }}).map(function (l) {{
          return '<li class="flex items-center justify-between border-b border-stone-200 py-2"><span>' + l + '</span><span class="font-semibold">' + counts[l] + '</span></li>';
        }}).join("") || '<li class="py-2 text-stone-500">No satisfaction labels found.</li>';
      }}

      function renderTeamList(rows) {{
        var counts = {{}};
        rows.filter(function (r) {{ return r.ticket_linked; }}).forEach(function (r) {{
          var t = r.team_name || "Unassigned";
          counts[t] = (counts[t] || 0) + 1;
        }});
        var sorted = Object.entries(counts).sort(function (a, b) {{ return b[1] - a[1]; }}).slice(0, 5);
        var el = document.getElementById("team-list");
        el.innerHTML = sorted.map(function (e) {{
          return '<li class="flex items-center justify-between border-b border-stone-200 py-2"><span>' + e[0] + '</span><span class="font-semibold">' + e[1] + '</span></li>';
        }}).join("") || '<li class="py-2 text-stone-500">No linked team data found.</li>';
      }}

      function renderComments(rows) {{
        var items = rows
          .filter(function (r) {{ return r.comment_text && String(r.comment_text).trim(); }})
          .sort(function (a, b) {{ return (b.survey_completed_at || "") > (a.survey_completed_at || "") ? 1 : -1; }})
          .slice(0, 5);
        var el = document.getElementById("comment-list");
        el.innerHTML = items.map(function (r) {{
          return '<li class="border-b border-stone-200 py-3">'
            + '<p class="font-medium text-stone-800">' + (r.satisfaction_label || "Unrated") + '</p>'
            + '<p class="text-sm text-stone-500">' + (r.team_name || "Unassigned team") + '</p>'
            + '<p class="mt-2 text-sm text-stone-700">' + (r.comment_text || "") + '</p>'
            + '</li>';
        }}).join("") || '<li class="py-2 text-stone-500">No recent comments found.</li>';
      }}

      function applyPeriod(days) {{
        var rows = filterData(days);
        var summary = computeSummary(rows);
        renderStatCards(summary);
        renderSatisfactionMix(rows);
        renderMonthlyTrend(rows);
        renderTeamVolume(rows);
        renderSatisfactionList(rows);
        renderTeamList(rows);
        renderComments(rows);
      }}

      document.querySelectorAll("[data-period]").forEach(function (btn) {{
        btn.addEventListener("click", function () {{
          document.querySelectorAll("[data-period]").forEach(function (b) {{
            b.classList.remove("bg-indigo-600", "text-white", "border-indigo-600");
            b.classList.add("bg-white", "text-stone-700", "border-stone-300");
          }});
          btn.classList.add("bg-indigo-600", "text-white", "border-indigo-600");
          btn.classList.remove("bg-white", "text-stone-700", "border-stone-300");
          var d = btn.dataset.period;
          applyPeriod(d ? parseInt(d, 10) : null);
        }});
      }});

      applyPeriod(null);
    }})();
    </script>
  </body>
</html>
"""
```

> **Note on f-string escaping:** The JS block uses `{{` and `}}` throughout (Python f-string escape for literal braces). The JS object literals like `{{"Very Satisfied": 5}}` must use double-braces in the f-string source.

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/test_reporting.py -v
```

Expected: all reporting tests PASS (including the two updated/new tests)

- [ ] **Step 5: Run the full test suite**

```bash
.venv/bin/python -m pytest -v
```

Expected: all tests pass

- [ ] **Step 6: Run Ruff**

```bash
.venv/bin/python -m ruff check src/ tests/
```

Expected: `All checks passed!`

- [ ] **Step 7: Regenerate the live report to verify visually**

```bash
.venv/bin/python -m dynamix_manager.cli refresh-surveys
open reports/survey_health.html
```

Expected: HTML opens in browser showing five period buttons and three Plotly charts (satisfaction mix horizontal bar, monthly trend dual-axis line, team volume colored bar). Clicking period buttons updates all charts and stat cards without page reload.

- [ ] **Step 8: Commit**

```bash
git add src/dynamix_manager/reporting.py tests/test_reporting.py
git commit -m "feat: add period filters and plotly charts to survey health report"
```
