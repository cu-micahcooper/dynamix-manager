from __future__ import annotations

from html import escape
from pathlib import Path

import pandas as pd

from dynamix_manager.metrics import summarize_survey_health, summarize_ticket_health


_SURVEY_JSON_COLUMNS = [
    "response_id",
    "survey_completed_at",
    "satisfaction_label",
    "team_name",
    "ticket_linked",
    "comment_text",
]


def _serialize_survey_rows(frame: pd.DataFrame) -> str:
    """Return the known survey columns from *frame* as a JSON records string, safe for <script> embedding."""
    columns = [c for c in _SURVEY_JSON_COLUMNS if c in frame.columns]
    raw = frame[columns].to_json(orient="records", date_format="iso")
    # Escape </ so </script> in data cannot break out of the enclosing <script> block.
    return raw.replace("</", r"<\/")


def _ticket_completion_rows(
    items: list[dict[str, str | int]],
    columns: list[tuple[str, str]],
    empty_message: str,
) -> str:
    if not items:
        return f'<tr><td class="px-4 py-3 text-stone-500" colspan="{len(columns)}">{empty_message}</td></tr>'
    return "".join(
        "<tr class=\"border-b border-stone-200\">"
        + "".join(
            f'<td class="px-4 py-3 text-sm text-stone-700">{escape(str(item.get(key) or ""))}</td>'
            for key, _label in columns
        )
        + "</tr>"
        for item in items
    )


def _ticket_quality_rows(
    items: list[dict[str, object]],
    columns: list[tuple[str, str]],
    empty_message: str,
) -> str:
    if not items:
        return f'<tr><td class="px-4 py-3 text-stone-500" colspan="{len(columns)}">{empty_message}</td></tr>'
    return "".join(
        "<tr class=\"border-b border-stone-200\">"
        + "".join(
            f'<td class="px-4 py-3 text-sm text-stone-700">{escape(str(item.get(key) or ""))}</td>'
            for key, _label in columns
        )
        + "</tr>"
        for item in items
    )


def _stale_ticket_rows(
    items: list[dict[str, object]],
    tdx_base_url: str | None,
    empty_message: str,
) -> str:
    if not items:
        return f'<tr><td class="px-4 py-3 text-stone-500" colspan="4">{empty_message}</td></tr>'
    rows = []
    for item in items:
        ticket_id = item.get("ticket_id") or ""
        ticket_app_id = item.get("ticket_app_id")
        if tdx_base_url and ticket_id and ticket_app_id:
            org_root = str(tdx_base_url).rstrip("/").removesuffix("/TDWebApi")
            url = f"{org_root}/TDNext/Apps/{int(ticket_app_id)}/Tickets/TicketDet?TicketID={int(ticket_id)}"
            id_cell = f'<td class="px-4 py-3 text-sm"><a class="text-blue-600 underline hover:text-blue-800" href="{escape(url)}" target="_blank" rel="noopener">{escape(str(int(ticket_id)))}</a></td>'
        else:
            id_cell = f'<td class="px-4 py-3 text-sm text-stone-700">{escape(str(ticket_id))}</td>'
        rows.append(
            "<tr class=\"border-b border-stone-200\">"
            + id_cell
            + f'<td class="px-4 py-3 text-sm text-stone-700">{escape(str(item.get("ticket_title") or ""))}</td>'
            + f'<td class="px-4 py-3 text-sm text-stone-700">{escape(str(item.get("team_name") or ""))}</td>'
            + f'<td class="px-4 py-3 text-sm text-stone-700">{escape(str(item.get("stale_business_days") or ""))}</td>'
            + "</tr>"
        )
    return "".join(rows)


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

      function esc(s) {{
        return String(s == null ? "" : s)
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;");
      }}

      function filterData(days) {{
        if (days == null) return window.SURVEY_DATA;
        var dates = window.SURVEY_DATA
          .map(function(r) {{ return r.survey_completed_at ? new Date(r.survey_completed_at) : null; }})
          .filter(Boolean);
        if (!dates.length) return [];
        var maxDate = new Date(Math.max.apply(null, dates));
        var cutoff = new Date(maxDate);
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
          return '<li class="flex items-center justify-between border-b border-stone-200 py-2"><span>' + esc(l) + '</span><span class="font-semibold">' + counts[l] + '</span></li>';
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
          return '<li class="flex items-center justify-between border-b border-stone-200 py-2"><span>' + esc(e[0]) + '</span><span class="font-semibold">' + e[1] + '</span></li>';
        }}).join("") || '<li class="py-2 text-stone-500">No linked team data found.</li>';
      }}

      function renderComments(rows) {{
        var items = rows
          .filter(function (r) {{ return r.comment_text && String(r.comment_text).trim(); }})
          .sort(function (a, b) {{
            var ta = b.survey_completed_at || "";
            var tb = a.survey_completed_at || "";
            return ta > tb ? 1 : ta < tb ? -1 : 0;
          }})
          .slice(0, 5);
        var el = document.getElementById("comment-list");
        el.innerHTML = items.map(function (r) {{
          return '<li class="border-b border-stone-200 py-3">'
            + '<p class="font-medium text-stone-800">' + esc(r.satisfaction_label || "Unrated") + '</p>'
            + '<p class="text-sm text-stone-500">' + esc(r.team_name || "Unassigned team") + '</p>'
            + '<p class="mt-2 text-sm text-stone-700">' + esc(r.comment_text || "") + '</p>'
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


def write_survey_health_report(frame: pd.DataFrame, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_survey_health_html(frame))
    return output_path


def render_ticket_health_html(
    frame: pd.DataFrame,
    days_off: pd.DataFrame | None = None,
    quality_flags: pd.DataFrame | None = None,
    interactions: pd.DataFrame | None = None,
    tdx_base_url: str | None = None,
) -> str:
    summary = summarize_ticket_health(
        frame,
        days_off=days_off,
        quality_flags=quality_flags,
        interactions=interactions,
    )
    team_items = "".join(
        f'<li class="flex items-center justify-between border-b border-stone-200 py-2"><span>{escape(label)}</span><span class="font-semibold">{count}</span></li>'
        for label, count in summary["top_teams"]
    ) or '<li class="py-2 text-stone-500">No team data found.</li>'
    service_items = "".join(
        f'<li class="flex items-center justify-between border-b border-stone-200 py-2"><span>{escape(label)}</span><span class="font-semibold">{count}</span></li>'
        for label, count in summary["top_services"]
    ) or '<li class="py-2 text-stone-500">No service data found.</li>'
    daily_team_rows = _ticket_completion_rows(
        summary["daily_team_completion"][:14],
        [
            ("resolved_date", "Date"),
            ("team_name", "Team"),
            ("completed_tickets", "Completed"),
        ],
        "No resolved tickets found.",
    )
    daily_member_rows = _ticket_completion_rows(
        summary["daily_member_completion"][:20],
        [
            ("resolved_date", "Date"),
            ("team_name", "Team"),
            ("assignee_name", "Member"),
            ("completed_tickets", "Completed"),
        ],
        "No resolved member data found.",
    )
    backlog_age_rows = _ticket_quality_rows(
        summary["backlog_age_buckets"],
        [("bucket", "Bucket"), ("tickets", "Tickets")],
        "No open backlog buckets found.",
    )
    high_touch_rows = _ticket_quality_rows(
        summary["high_touch_tickets"][:10],
        [
            ("ticket_id", "Ticket"),
            ("ticket_title", "Title"),
            ("team_name", "Team"),
            ("touch_count", "Touches"),
        ],
        "No touch-count data found.",
    )
    recurring_rows = _ticket_quality_rows(
        summary["top_recurrent_titles"][:10],
        [
            ("ticket_title", "Title"),
            ("service_name", "Service"),
            ("tickets", "Tickets"),
        ],
        "No recurring title patterns found.",
    )
    hotspot_rows = _ticket_quality_rows(
        summary["team_quality_hotspots"][:10],
        [
            ("team_name", "Team"),
            ("client_last_interaction", "Client Last"),
            ("repeated_it_followup", "IT Follow-Up"),
            ("stale_public_updates", "Stale Public"),
            ("private_activity_since_last_public", "Private Activity"),
            ("average_interaction_count", "Avg Touches"),
        ],
        "No team quality hotspot data found.",
    )
    stale_rows = _stale_ticket_rows(
        summary["stale_open_tickets"][:10],
        tdx_base_url,
        "No stale open tickets found.",
    )
    sla_hotspot_rows = _ticket_quality_rows(
        summary["sla_hotspots"][:10],
        [
            ("team_name", "Team"),
            ("covered_tickets", "Covered"),
            ("violated_tickets", "Violated"),
            ("resolve_breached", "Resolve Breached"),
        ],
        "No SLA data found.",
    )
    hygiene_rows = _ticket_quality_rows(
        summary["hygiene_tickets"][:10],
        [
            ("ticket_id", "Ticket"),
            ("ticket_title", "Title"),
            ("team_name", "Team"),
            ("assignee_name", "Assignee"),
            ("issues", "Issues"),
        ],
        "No hygiene gaps found.",
    )
    member_backlog_rows = _ticket_quality_rows(
        summary["member_backlog_hotspots"][:10],
        [
            ("team_name", "Team"),
            ("assignee_name", "Member"),
            ("backlog_tickets", "Backlog"),
            ("average_backlog_age", "Avg Age"),
        ],
        "No open backlog hotspots found.",
    )

    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Ticket Health</title>
    <script src="https://cdn.tailwindcss.com"></script>
  </head>
  <body class="min-h-screen bg-stone-100 text-stone-900">
    <main class="mx-auto max-w-6xl px-6 py-10">
      <section class="mb-8">
        <p class="text-sm uppercase tracking-[0.3em] text-stone-500">TeamDynamix Analytics</p>
        <h1 class="mt-2 text-4xl font-semibold">Ticket Health</h1>
        <p class="mt-3 max-w-2xl text-stone-600">Balanced operational view of ticket flow, service quality, hygiene, SLA health, and ownership concentration.</p>
      </section>
      <section class="grid gap-4 md:grid-cols-3">
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">Tickets</p><p class="mt-3 text-3xl font-semibold">{summary["total_tickets"]}</p></article>
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">Resolved</p><p class="mt-3 text-3xl font-semibold">{summary["resolved_tickets"]}</p></article>
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">Backlog</p><p class="mt-3 text-3xl font-semibold">{summary["backlog_tickets"]}</p></article>
      </section>
      <section class="mt-8 grid gap-4 md:grid-cols-2">
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">Average response</p><p class="mt-3 text-3xl font-semibold">{summary["average_response_hours"] if summary["average_response_hours"] is not None else "N/A"}</p></article>
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">Average resolution</p><p class="mt-3 text-3xl font-semibold">{summary["average_resolution_hours"] if summary["average_resolution_hours"] is not None else "N/A"}</p></article>
      </section>
      <section class="mt-8 grid gap-4 md:grid-cols-3 xl:grid-cols-6">
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">Quality Scorecard</p><p class="mt-3 text-3xl font-semibold">{summary["quality_counts"]["client_last_interaction"] + summary["quality_counts"]["repeated_it_followup"] + summary["quality_counts"]["stale_public_updates"] + summary["quality_counts"]["private_activity_since_last_public"]}</p></article>
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">Client Last</p><p class="mt-3 text-3xl font-semibold">{summary["quality_counts"]["client_last_interaction"]}</p></article>
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">Repeated IT Follow-Up</p><p class="mt-3 text-3xl font-semibold">{summary["quality_counts"]["repeated_it_followup"]}</p></article>
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">Stale Public Update</p><p class="mt-3 text-3xl font-semibold">{summary["quality_counts"]["stale_public_updates"]}</p></article>
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">Private Activity Since Last Public Update</p><p class="mt-3 text-3xl font-semibold">{summary["quality_counts"]["private_activity_since_last_public"]}</p></article>
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">Avg Touches</p><p class="mt-3 text-3xl font-semibold">{summary["average_touches_per_ticket"] if summary["average_touches_per_ticket"] is not None else "N/A"}</p></article>
      </section>
      <section class="mt-8 grid gap-4 md:grid-cols-4">
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">SLA Coverage</p><p class="mt-3 text-3xl font-semibold">{summary["sla_summary"]["covered_tickets"]}</p><p class="mt-2 text-sm text-stone-500">{summary["sla_summary"]["coverage_rate"]:.1%} of tickets</p></article>
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">SLA Violations</p><p class="mt-3 text-3xl font-semibold">{summary["sla_summary"]["violated_tickets"]}</p><p class="mt-2 text-sm text-stone-500">{summary["sla_summary"]["violation_rate"]:.1%} of covered tickets</p></article>
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">Near Resolve Due</p><p class="mt-3 text-3xl font-semibold">{summary["sla_summary"]["open_near_resolve_due"]}</p></article>
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">Open Unassigned</p><p class="mt-3 text-3xl font-semibold">{summary["hygiene_counts"]["open_unassigned"]}</p></article>
      </section>
      <section class="mt-8 grid gap-6 xl:grid-cols-2">
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Response And Resolution Percentiles</h2>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full divide-y divide-stone-200">
              <thead>
                <tr class="text-left text-xs uppercase tracking-[0.2em] text-stone-500">
                  <th class="px-4 py-3">Metric</th>
                  <th class="px-4 py-3">P50</th>
                  <th class="px-4 py-3">P75</th>
                  <th class="px-4 py-3">P90</th>
                </tr>
              </thead>
              <tbody>
                <tr class="border-b border-stone-200"><td class="px-4 py-3 text-sm text-stone-700">Response Hours</td><td class="px-4 py-3 text-sm text-stone-700">{summary["response_percentiles_hours"]["p50"] if summary["response_percentiles_hours"]["p50"] is not None else "N/A"}</td><td class="px-4 py-3 text-sm text-stone-700">{summary["response_percentiles_hours"]["p75"] if summary["response_percentiles_hours"]["p75"] is not None else "N/A"}</td><td class="px-4 py-3 text-sm text-stone-700">{summary["response_percentiles_hours"]["p90"] if summary["response_percentiles_hours"]["p90"] is not None else "N/A"}</td></tr>
                <tr class="border-b border-stone-200"><td class="px-4 py-3 text-sm text-stone-700">Resolution Hours</td><td class="px-4 py-3 text-sm text-stone-700">{summary["resolution_percentiles_hours"]["p50"] if summary["resolution_percentiles_hours"]["p50"] is not None else "N/A"}</td><td class="px-4 py-3 text-sm text-stone-700">{summary["resolution_percentiles_hours"]["p75"] if summary["resolution_percentiles_hours"]["p75"] is not None else "N/A"}</td><td class="px-4 py-3 text-sm text-stone-700">{summary["resolution_percentiles_hours"]["p90"] if summary["resolution_percentiles_hours"]["p90"] is not None else "N/A"}</td></tr>
              </tbody>
            </table>
          </div>
        </article>
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Backlog Aging</h2>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full divide-y divide-stone-200">
              <thead>
                <tr class="text-left text-xs uppercase tracking-[0.2em] text-stone-500">
                  <th class="px-4 py-3">Bucket</th>
                  <th class="px-4 py-3">Tickets</th>
                </tr>
              </thead>
              <tbody>{backlog_age_rows}</tbody>
            </table>
          </div>
        </article>
      </section>
      <section class="mt-8 grid gap-6 lg:grid-cols-2">
        <article class="rounded-2xl bg-white p-6 shadow-sm"><h2 class="text-lg font-semibold">Top Teams</h2><ul class="mt-4">{team_items}</ul></article>
        <article class="rounded-2xl bg-white p-6 shadow-sm"><h2 class="text-lg font-semibold">Top Services</h2><ul class="mt-4">{service_items}</ul></article>
      </section>
      <section class="mt-8 grid gap-6 xl:grid-cols-2">
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Business-Day Team Completions</h2>
          <p class="mt-2 text-sm text-stone-500">These counts exclude weekends and TeamDynamix days off when that calendar is cached locally.</p>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full divide-y divide-stone-200">
              <thead>
                <tr class="text-left text-xs uppercase tracking-[0.2em] text-stone-500">
                  <th class="px-4 py-3">Date</th>
                  <th class="px-4 py-3">Team</th>
                  <th class="px-4 py-3">Completed</th>
                </tr>
              </thead>
              <tbody>{daily_team_rows}</tbody>
            </table>
          </div>
        </article>
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Business-Day Member Completions</h2>
          <p class="mt-2 text-sm text-stone-500">Member comparisons stay within business days so cross-team pacing is not distorted by weekends or holidays.</p>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full divide-y divide-stone-200">
              <thead>
                <tr class="text-left text-xs uppercase tracking-[0.2em] text-stone-500">
                  <th class="px-4 py-3">Date</th>
                  <th class="px-4 py-3">Team</th>
                  <th class="px-4 py-3">Member</th>
                  <th class="px-4 py-3">Completed</th>
                </tr>
              </thead>
              <tbody>{daily_member_rows}</tbody>
            </table>
          </div>
        </article>
      </section>
      <section class="mt-8 grid gap-6 xl:grid-cols-2">
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">SLA Health</h2>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full divide-y divide-stone-200">
              <thead>
                <tr class="text-left text-xs uppercase tracking-[0.2em] text-stone-500">
                  <th class="px-4 py-3">Metric</th>
                  <th class="px-4 py-3">Value</th>
                </tr>
              </thead>
              <tbody>
                <tr class="border-b border-stone-200"><td class="px-4 py-3 text-sm text-stone-700">Respond Breached</td><td class="px-4 py-3 text-sm text-stone-700">{summary["sla_summary"]["respond_breached"]}</td></tr>
                <tr class="border-b border-stone-200"><td class="px-4 py-3 text-sm text-stone-700">Resolve Breached</td><td class="px-4 py-3 text-sm text-stone-700">{summary["sla_summary"]["resolve_breached"]}</td></tr>
                <tr class="border-b border-stone-200"><td class="px-4 py-3 text-sm text-stone-700">Near Respond Due</td><td class="px-4 py-3 text-sm text-stone-700">{summary["sla_summary"]["open_near_respond_due"]}</td></tr>
                <tr class="border-b border-stone-200"><td class="px-4 py-3 text-sm text-stone-700">Near Resolve Due</td><td class="px-4 py-3 text-sm text-stone-700">{summary["sla_summary"]["open_near_resolve_due"]}</td></tr>
              </tbody>
            </table>
          </div>
        </article>
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Ticket Hygiene</h2>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full divide-y divide-stone-200">
              <thead>
                <tr class="text-left text-xs uppercase tracking-[0.2em] text-stone-500">
                  <th class="px-4 py-3">Metric</th>
                  <th class="px-4 py-3">Value</th>
                </tr>
              </thead>
              <tbody>
                <tr class="border-b border-stone-200"><td class="px-4 py-3 text-sm text-stone-700">Missing Title</td><td class="px-4 py-3 text-sm text-stone-700">{summary["hygiene_counts"]["missing_title"]}</td></tr>
                <tr class="border-b border-stone-200"><td class="px-4 py-3 text-sm text-stone-700">Missing Service</td><td class="px-4 py-3 text-sm text-stone-700">{summary["hygiene_counts"]["missing_service"]}</td></tr>
                <tr class="border-b border-stone-200"><td class="px-4 py-3 text-sm text-stone-700">Missing Team</td><td class="px-4 py-3 text-sm text-stone-700">{summary["hygiene_counts"]["missing_team"]}</td></tr>
                <tr class="border-b border-stone-200"><td class="px-4 py-3 text-sm text-stone-700">Missing Priority</td><td class="px-4 py-3 text-sm text-stone-700">{summary["hygiene_counts"]["missing_priority"]}</td></tr>
              </tbody>
            </table>
          </div>
        </article>
      </section>
      <section class="mt-8 grid gap-6 xl:grid-cols-2">
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">High-Touch Tickets</h2>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full divide-y divide-stone-200">
              <thead>
                <tr class="text-left text-xs uppercase tracking-[0.2em] text-stone-500">
                  <th class="px-4 py-3">Ticket</th>
                  <th class="px-4 py-3">Title</th>
                  <th class="px-4 py-3">Team</th>
                  <th class="px-4 py-3">Touches</th>
                </tr>
              </thead>
              <tbody>{high_touch_rows}</tbody>
            </table>
          </div>
        </article>
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Stale Open Tickets</h2>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full divide-y divide-stone-200">
              <thead>
                <tr class="text-left text-xs uppercase tracking-[0.2em] text-stone-500">
                  <th class="px-4 py-3">Ticket</th>
                  <th class="px-4 py-3">Title</th>
                  <th class="px-4 py-3">Team</th>
                  <th class="px-4 py-3">Business Days</th>
                </tr>
              </thead>
              <tbody>{stale_rows}</tbody>
            </table>
          </div>
        </article>
      </section>
      <section class="mt-8 grid gap-6 xl:grid-cols-2">
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Quality-Adjusted SLA</h2>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full divide-y divide-stone-200">
              <thead>
                <tr class="text-left text-xs uppercase tracking-[0.2em] text-stone-500">
                  <th class="px-4 py-3">Metric</th>
                  <th class="px-4 py-3">Value</th>
                </tr>
              </thead>
              <tbody>
                <tr class="border-b border-stone-200"><td class="px-4 py-3 text-sm text-stone-700">Breached And High Touch</td><td class="px-4 py-3 text-sm text-stone-700">{summary["quality_adjusted_sla"]["breached_and_high_touch"]}</td></tr>
                <tr class="border-b border-stone-200"><td class="px-4 py-3 text-sm text-stone-700">Breached And Client Waiting</td><td class="px-4 py-3 text-sm text-stone-700">{summary["quality_adjusted_sla"]["breached_and_client_waiting"]}</td></tr>
                <tr class="border-b border-stone-200"><td class="px-4 py-3 text-sm text-stone-700">Breached And Repeated IT Follow-Up</td><td class="px-4 py-3 text-sm text-stone-700">{summary["quality_adjusted_sla"]["breached_and_repeated_it_followup"]}</td></tr>
              </tbody>
            </table>
          </div>
        </article>
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Team Quality Hotspots</h2>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full divide-y divide-stone-200">
              <thead>
                <tr class="text-left text-xs uppercase tracking-[0.2em] text-stone-500">
                  <th class="px-4 py-3">Team</th>
                  <th class="px-4 py-3">Client Last</th>
                  <th class="px-4 py-3">IT Follow-Up</th>
                  <th class="px-4 py-3">Stale Public</th>
                  <th class="px-4 py-3">Private Activity</th>
                  <th class="px-4 py-3">Avg Touches</th>
                </tr>
              </thead>
              <tbody>{hotspot_rows}</tbody>
            </table>
          </div>
        </article>
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Recurring Issue Candidates</h2>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full divide-y divide-stone-200">
              <thead>
                <tr class="text-left text-xs uppercase tracking-[0.2em] text-stone-500">
                  <th class="px-4 py-3">Title</th>
                  <th class="px-4 py-3">Service</th>
                  <th class="px-4 py-3">Tickets</th>
                </tr>
              </thead>
              <tbody>{recurring_rows}</tbody>
            </table>
          </div>
        </article>
      </section>
      <section class="mt-8 grid gap-6 xl:grid-cols-2">
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">SLA Hotspots</h2>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full divide-y divide-stone-200">
              <thead>
                <tr class="text-left text-xs uppercase tracking-[0.2em] text-stone-500">
                  <th class="px-4 py-3">Team</th>
                  <th class="px-4 py-3">Covered</th>
                  <th class="px-4 py-3">Violated</th>
                  <th class="px-4 py-3">Resolve Breached</th>
                </tr>
              </thead>
              <tbody>{sla_hotspot_rows}</tbody>
            </table>
          </div>
        </article>
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Backlog Load Hotspots</h2>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full divide-y divide-stone-200">
              <thead>
                <tr class="text-left text-xs uppercase tracking-[0.2em] text-stone-500">
                  <th class="px-4 py-3">Team</th>
                  <th class="px-4 py-3">Member</th>
                  <th class="px-4 py-3">Backlog</th>
                  <th class="px-4 py-3">Avg Age</th>
                </tr>
              </thead>
              <tbody>{member_backlog_rows}</tbody>
            </table>
          </div>
        </article>
      </section>
      <section class="mt-8">
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Hygiene Gaps</h2>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full divide-y divide-stone-200">
              <thead>
                <tr class="text-left text-xs uppercase tracking-[0.2em] text-stone-500">
                  <th class="px-4 py-3">Ticket</th>
                  <th class="px-4 py-3">Title</th>
                  <th class="px-4 py-3">Team</th>
                  <th class="px-4 py-3">Assignee</th>
                  <th class="px-4 py-3">Issues</th>
                </tr>
              </thead>
              <tbody>{hygiene_rows}</tbody>
            </table>
          </div>
        </article>
      </section>
    </main>
  </body>
</html>
"""


def write_ticket_health_report(
    frame: pd.DataFrame,
    output_path: Path,
    days_off: pd.DataFrame | None = None,
    quality_flags: pd.DataFrame | None = None,
    interactions: pd.DataFrame | None = None,
    tdx_base_url: str | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_ticket_health_html(
            frame,
            days_off=days_off,
            quality_flags=quality_flags,
            interactions=interactions,
            tdx_base_url=tdx_base_url,
        )
    )
    return output_path


def render_ticket_quality_html(frame: pd.DataFrame) -> str:
    frame = frame.copy()
    for column, default in {
        "client_last_interaction_flag": False,
        "it_follow_up_without_client_response_flag": False,
        "stale_public_update_flag": False,
        "private_activity_since_last_public_flag": False,
        "stale_public_update_business_days": 0,
        "last_private_interaction_at": None,
        "last_private_interaction_by": None,
    }.items():
        if column not in frame.columns:
            frame[column] = default
    client_last = frame.loc[frame["client_last_interaction_flag"]].sort_values(
        "last_public_interaction_at",
        ascending=False,
    )
    it_follow_up = frame.loc[frame["it_follow_up_without_client_response_flag"]].sort_values(
        ["it_follow_up_streak", "last_public_interaction_at"],
        ascending=[False, False],
    )
    client_last_rows = _ticket_quality_rows(
        client_last.head(25).to_dict(orient="records"),
        [
            ("ticket_id", "Ticket"),
            ("ticket_title", "Title"),
            ("team_name", "Team"),
            ("requestor_name", "Requestor"),
            ("last_public_interaction_by", "Last By"),
        ],
        "No tickets currently end with a client interaction.",
    )
    it_follow_up_rows = _ticket_quality_rows(
        it_follow_up.head(25).to_dict(orient="records"),
        [
            ("ticket_id", "Ticket"),
            ("ticket_title", "Title"),
            ("team_name", "Team"),
            ("it_follow_up_streak", "IT Follow-Ups"),
            ("last_public_interaction_by", "Last By"),
        ],
        "No tickets currently exceed the IT follow-up threshold.",
    )
    stale_public = frame.loc[frame["stale_public_update_flag"]].sort_values(
        ["stale_public_update_business_days", "last_public_interaction_at"],
        ascending=[False, False],
    )
    stale_public_rows = _ticket_quality_rows(
        stale_public.head(25).to_dict(orient="records"),
        [
            ("ticket_id", "Ticket"),
            ("ticket_title", "Title"),
            ("team_name", "Team"),
            ("stale_public_update_business_days", "Business Days"),
            ("last_public_interaction_by", "Last Public By"),
        ],
        "No tickets currently have stale public communication.",
    )
    private_activity = frame.loc[frame["private_activity_since_last_public_flag"]].sort_values(
        ["last_private_interaction_at", "ticket_id"],
        ascending=[False, True],
    )
    private_activity_rows = _ticket_quality_rows(
        private_activity.head(25).to_dict(orient="records"),
        [
            ("ticket_id", "Ticket"),
            ("ticket_title", "Title"),
            ("team_name", "Team"),
            ("last_private_interaction_by", "Private By"),
            ("last_public_interaction_by", "Last Public By"),
        ],
        "No tickets currently show private-only activity after the last public update.",
    )

    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Ticket Quality</title>
    <script src="https://cdn.tailwindcss.com"></script>
  </head>
  <body class="min-h-screen bg-stone-100 text-stone-900">
    <main class="mx-auto max-w-6xl px-6 py-10">
      <section class="mb-8">
        <p class="text-sm uppercase tracking-[0.3em] text-stone-500">TeamDynamix Analytics</p>
        <h1 class="mt-2 text-4xl font-semibold">Ticket Quality</h1>
        <p class="mt-3 max-w-2xl text-stone-600">Quality checks over public ticket interactions to highlight tickets waiting on IT response discipline versus client response discipline.</p>
      </section>
      <section class="grid gap-4 md:grid-cols-3">
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">Tickets Reviewed</p><p class="mt-3 text-3xl font-semibold">{len(frame)}</p></article>
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">Client Last Interaction</p><p class="mt-3 text-3xl font-semibold">{int(frame["client_last_interaction_flag"].sum()) if not frame.empty else 0}</p></article>
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">Repeated IT Follow-Up</p><p class="mt-3 text-3xl font-semibold">{int(frame["it_follow_up_without_client_response_flag"].sum()) if not frame.empty else 0}</p></article>
      </section>
      <section class="mt-4 grid gap-4 md:grid-cols-2">
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">Stale Public Update</p><p class="mt-3 text-3xl font-semibold">{int(frame["stale_public_update_flag"].sum()) if "stale_public_update_flag" in frame.columns and not frame.empty else 0}</p></article>
        <article class="rounded-2xl bg-white p-6 shadow-sm"><p class="text-sm text-stone-500">Private Activity Since Last Public Update</p><p class="mt-3 text-3xl font-semibold">{int(frame["private_activity_since_last_public_flag"].sum()) if "private_activity_since_last_public_flag" in frame.columns and not frame.empty else 0}</p></article>
      </section>
      <section class="mt-8 grid gap-6 xl:grid-cols-2">
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Client Last Interaction</h2>
          <p class="mt-2 text-sm text-stone-500">These tickets are most likely waiting on IT because the last meaningful public interaction came from the client.</p>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full divide-y divide-stone-200">
              <thead>
                <tr class="text-left text-xs uppercase tracking-[0.2em] text-stone-500">
                  <th class="px-4 py-3">Ticket</th>
                  <th class="px-4 py-3">Title</th>
                  <th class="px-4 py-3">Team</th>
                  <th class="px-4 py-3">Requestor</th>
                  <th class="px-4 py-3">Last By</th>
                </tr>
              </thead>
              <tbody>{client_last_rows}</tbody>
            </table>
          </div>
        </article>
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Repeated IT Follow-Up</h2>
          <p class="mt-2 text-sm text-stone-500">These tickets have more than three consecutive IT follow-ups without a client response.</p>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full divide-y divide-stone-200">
              <thead>
                <tr class="text-left text-xs uppercase tracking-[0.2em] text-stone-500">
                  <th class="px-4 py-3">Ticket</th>
                  <th class="px-4 py-3">Title</th>
                  <th class="px-4 py-3">Team</th>
                  <th class="px-4 py-3">IT Follow-Ups</th>
                  <th class="px-4 py-3">Last By</th>
                </tr>
              </thead>
              <tbody>{it_follow_up_rows}</tbody>
            </table>
          </div>
        </article>
      </section>
      <section class="mt-8 grid gap-6 xl:grid-cols-2">
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Stale Public Update</h2>
          <p class="mt-2 text-sm text-stone-500">These open tickets have gone more than three business days without a customer-visible public update.</p>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full divide-y divide-stone-200">
              <thead>
                <tr class="text-left text-xs uppercase tracking-[0.2em] text-stone-500">
                  <th class="px-4 py-3">Ticket</th>
                  <th class="px-4 py-3">Title</th>
                  <th class="px-4 py-3">Team</th>
                  <th class="px-4 py-3">Business Days</th>
                  <th class="px-4 py-3">Last Public By</th>
                </tr>
              </thead>
              <tbody>{stale_public_rows}</tbody>
            </table>
          </div>
        </article>
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Private Activity Since Last Public Update</h2>
          <p class="mt-2 text-sm text-stone-500">These tickets show internal-only work after the last customer-visible update, which often means the client has lost visibility into progress.</p>
          <div class="mt-4 overflow-x-auto">
            <table class="min-w-full divide-y divide-stone-200">
              <thead>
                <tr class="text-left text-xs uppercase tracking-[0.2em] text-stone-500">
                  <th class="px-4 py-3">Ticket</th>
                  <th class="px-4 py-3">Title</th>
                  <th class="px-4 py-3">Team</th>
                  <th class="px-4 py-3">Private By</th>
                  <th class="px-4 py-3">Last Public By</th>
                </tr>
              </thead>
              <tbody>{private_activity_rows}</tbody>
            </table>
          </div>
        </article>
      </section>
    </main>
  </body>
</html>
"""


def write_ticket_quality_report(frame: pd.DataFrame, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_ticket_quality_html(frame))
    return output_path
