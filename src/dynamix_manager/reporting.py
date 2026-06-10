from __future__ import annotations

import base64
from html import escape
from pathlib import Path
import subprocess
import tempfile

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

_HEADER_BURST_FONT_STACK = (
    "Georgia, Hoefler Text, Times New Roman, serif"
)
_HEADER_BURST_FONT_PATHS = [
    Path("/System/Library/Fonts/Supplemental/Georgia Bold.ttf"),
    Path("/System/Library/Fonts/Supplemental/Hoefler Text.ttc"),
    Path("/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf"),
]
_LIGHT_PANEL_PIXEL_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAIGNIUk0AAHomAACAhAAA+gAAAIDoAAB1MAAA6mAAADqYAAAXcJy6UTwAAAAGYktHRAD/AP8A/6C9p5MAAAAHdElNRQfqBBcMDx2uwdLQAAAAJXRFWHRkYXRlOmNyZWF0ZQAyMDI2LTA0LTIzVDEyOjE1OjI5KzAwOjAwZ7gXsgAAACV0RVh0ZGF0ZTptb2RpZnkAMjAyNi0wNC0yM1QxMjoxNToyOSswMDowMBblrw4AAAAodEVYdGRhdGU6dGltZXN0YW1wADIwMjYtMDQtMjNUMTI6MTU6MjkrMDA6MDBB8I7RAAAADUlEQVQI12P4/ev7fwAJzAPsuF9sUwAAAABJRU5ErkJggg=="
)


def _burst_line_layout(text: str) -> list[str]:
    clean = " ".join(text.strip().split())
    words = clean.split()
    if not words:
        return ["Watch This Space"]
    if len(clean) <= 14 or len(words) == 1:
        return [" ".join(words)]

    target_lines = 2 if len(clean) <= 20 else 3

    def _score(lines: list[str]) -> tuple[int, int, int]:
        lengths = [len(line) for line in lines]
        return (max(lengths), max(lengths) - min(lengths), sum(lengths))

    if target_lines == 2 and len(words) >= 2:
        options = [
            [" ".join(words[:idx]), " ".join(words[idx:])]
            for idx in range(1, len(words))
        ]
        return min(options, key=_score)

    if target_lines == 3 and len(words) >= 3:
        options = [
            [
                " ".join(words[:first]),
                " ".join(words[first:second]),
                " ".join(words[second:]),
            ]
            for first in range(1, len(words) - 1)
            for second in range(first + 1, len(words))
        ]
        return min(options, key=_score)

    midpoint = max(1, len(words) // 2)
    return [" ".join(words[:midpoint]), " ".join(words[midpoint:])]


def _build_header_burst_svg(tagline: str) -> str:
    burst_lines = _burst_line_layout(tagline)
    if len(burst_lines) == 1:
        burst_specs = [(72, 17, burst_lines[0])]
    elif len(burst_lines) == 2:
        burst_specs = [(62, 14, burst_lines[0]), (82, 16, burst_lines[1])]
    else:
        burst_specs = [(56, 12, burst_lines[0]), (73, 16, burst_lines[1]), (91, 12, burst_lines[2])]

    burst_text_layers = ""
    for y, size, line in burst_specs:
        burst_text_layers += (
            '<text x="90" y="'
            + str(y + 3)
            + '" text-anchor="middle" font-family="'
            + _HEADER_BURST_FONT_STACK
            + '" font-size="'
            + str(size)
            + '" font-weight="700" letter-spacing="0.3" fill="#7d5b13" opacity="0.24">'
            + escape(line)
            + "</text>"
            '<text x="90" y="'
            + str(y)
            + '" text-anchor="middle" font-family="'
            + _HEADER_BURST_FONT_STACK
            + '" font-size="'
            + str(size)
            + '" font-weight="700" letter-spacing="0.3" fill="#003963">'
            + escape(line)
            + "</text>"
        )

    return (
        '<svg width="180" height="130" viewBox="0 0 180 130" xmlns="http://www.w3.org/2000/svg" '
        'role="img" aria-label="Weekly tagline">'
        '<polygon points="90,8 108,22 134,14 138,40 167,39 157,63 180,78 160,94 167,122 138,118 '
        '134,114 108,106 90,122 72,106 46,114 42,88 13,89 23,65 0,50 20,35 13,8 42,12 46,16 72,22" '
        'fill="#FBB93A" stroke="#ffffff" stroke-width="4"/>'
        '<polygon points="90,20 106,32 128,25 132,47 156,47 148,67 168,79 149,92 156,112 132,109 '
        '128,105 106,98 90,112 74,98 52,105 48,83 24,84 32,64 12,53 31,40 24,20 48,23 52,27 74,32" '
        'fill="#ffe8ad" opacity="0.55"/>'
        '<g transform="rotate(12 90 72)">'
        + burst_text_layers
        + "</g></svg>"
    )


def _header_burst_font_path() -> Path:
    for candidate in _HEADER_BURST_FONT_PATHS:
        if candidate.exists():
            return candidate
    raise FileNotFoundError("No usable header burst font file found")


def _magick_annotate_text(line: str) -> str:
    return line.replace("\\", "\\\\").replace('"', r"\"")


def render_header_burst_png(tagline: str) -> bytes:
    burst_lines = _burst_line_layout(tagline)
    if len(burst_lines) == 1:
        burst_specs = [(0, 17, burst_lines[0])]
    elif len(burst_lines) == 2:
        burst_specs = [(-10, 14, burst_lines[0]), (12, 16, burst_lines[1])]
    else:
        burst_specs = [(-17, 12, burst_lines[0]), (0, 16, burst_lines[1]), (17, 12, burst_lines[2])]

    font_path = _header_burst_font_path()
    with tempfile.TemporaryDirectory() as tmpdir:
        png_path = Path(tmpdir) / "header_burst.png"
        command = [
            "/opt/homebrew/bin/magick",
            "-size",
            "180x130",
            "canvas:none",
            "-fill",
            "#FBB93A",
            "-stroke",
            "#ffffff",
            "-strokewidth",
            "4",
            "-draw",
            "polygon 90,8 108,22 134,14 138,40 167,39 157,63 180,78 160,94 167,122 138,118 "
            "134,114 108,106 90,122 72,106 46,114 42,88 13,89 23,65 0,50 20,35 13,8 42,12 46,16 72,22",
            "-fill",
            "#ffe8ad",
            "-stroke",
            "none",
            "-draw",
            "polygon 90,20 106,32 128,25 132,47 156,47 148,67 168,79 149,92 156,112 132,109 "
            "128,105 106,98 90,112 74,98 52,105 48,83 24,84 32,64 12,53 31,40 24,20 48,23 52,27 74,32",
            "-font",
            str(font_path),
            "-gravity",
            "center",
        ]
        for y_offset, size, line in burst_specs:
            text = _magick_annotate_text(line)
            command.extend(
                [
                    "-fill",
                    "#7d5b13",
                    "-stroke",
                    "none",
                    "-pointsize",
                    str(size),
                    "-annotate",
                    f"12x12+0{y_offset + 2:+d}",
                    text,
                    "-fill",
                    "#003963",
                    "-stroke",
                    "none",
                    "-pointsize",
                    str(size),
                    "-annotate",
                    f"12x12+0{y_offset:+d}",
                    text,
                ]
            )
        command.append(f"PNG32:{png_path}")
        subprocess.run(command, check=True, capture_output=True)
        return png_path.read_bytes()


def header_burst_png_data_uri(tagline: str) -> str:
    return "data:image/png;base64," + base64.b64encode(render_header_burst_png(tagline)).decode("ascii")


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


def _executive_kpi_card(label: str, value: str, sub: str = "", sub2: str = "", detail_html: str = "") -> str:
    # label, sub, sub2 are developer-controlled strings; value is escaped since it may contain arbitrary data
    sub_html = (
        f'<p style="font-family:\'myriad-pro\',sans-serif; font-size:0.76rem; '
        f'color:#6b7c93; margin-top:0.3rem; letter-spacing:0.01em;">{sub}</p>'
    ) if sub else ""
    sub_html += (
        f'<p style="font-family:\'myriad-pro\',sans-serif; font-size:0.73rem; '
        f'color:#8a9bb0; margin-top:0.1rem;">{sub2}</p>'
    ) if sub2 else ""
    detail = (
        f'<details style="margin-top:0.8rem;">'
        f'<summary style="cursor:pointer; font-family:\'myriad-pro\',sans-serif; font-size:0.68rem; '
        f'color:#8a9bb0; letter-spacing:0.08em; text-transform:uppercase; '
        f'list-style:none; user-select:none;">&#9656; Show details</summary>'
        f'<div style="margin-top:0.5rem; overflow-x:auto;">{detail_html}</div>'
        f'</details>'
    ) if detail_html else ""
    return (
        f'<div style="padding:1.5rem 1.75rem; background:#fff; '
        f'border:1px solid #e4e8ee; border-top:3px solid #003963; '
        f'box-shadow:0 1px 4px rgba(0,57,99,0.06); animation:fadeUp 0.5s ease both;">'
        f'<p style="font-family:\'myriad-pro\',sans-serif; font-size:0.64rem; font-weight:600; '
        f'color:#8a9bb0; text-transform:uppercase; letter-spacing:0.14em; '
        f'margin-bottom:0.45rem;">{label}</p>'
        f'<p style="font-family:\'myriad-pro\',sans-serif; font-size:2.25rem; font-weight:700; '
        f'color:#003963; line-height:1; letter-spacing:-0.02em;">{escape(value)}</p>'
        f"{sub_html}{detail}"
        f"</div>"
    )


def _ticket_table(rows: list[dict], tdx_base_url: str | None) -> str:
    """Build a compact HTML table from ticket detail rows."""
    _th = ('style="text-align:left; padding:0.3rem 0.5rem; font-family:\'myriad-pro\',sans-serif; '
           'font-size:0.6rem; font-weight:600; color:#8a9bb0; text-transform:uppercase; '
           'letter-spacing:0.1em; border-bottom:1px solid #e4e8ee;"')
    _td = 'style="padding:0.3rem 0.5rem; font-size:0.72rem; font-family:\'minion-pro\',serif; color:#3a4a5c; border-bottom:1px solid #f0f2f5;"'
    _td_nb = 'style="padding:0.3rem 0.5rem; font-size:0.72rem; font-family:\'minion-pro\',serif; color:#3a4a5c; white-space:nowrap; border-bottom:1px solid #f0f2f5;"'

    if not rows:
        return '<p style="font-family:\'myriad-pro\',sans-serif; font-size:0.72rem; color:#aab4c0; padding:0.4rem 0;">No tickets</p>'

    def _link(row: dict) -> str:
        tid = row.get("ticket_id")
        app_id = row.get("ticket_app_id")
        if tdx_base_url and tid and app_id:
            org = str(tdx_base_url).rstrip("/").removesuffix("/TDWebApi")
            url = f"{org}/TDNext/Apps/{int(app_id)}/Tickets/TicketDet?TicketID={int(tid)}"
            return (f'<a href="{escape(url)}" target="_blank" '
                    f'style="color:#003963; text-decoration:none; border-bottom:1px solid rgba(0,57,99,0.25);">'
                    f'{escape(str(int(tid)))}</a>')
        return escape(str(tid or ""))

    header = (
        f'<table style="width:100%; border-collapse:collapse;">'
        f'<thead><tr>'
        f'<th {_th}>ID</th><th {_th}>Title</th>'
        f'<th {_th}>Service</th><th {_th}>Assignee</th>'
        f'</tr></thead><tbody>'
    )
    body = ""
    for row in rows:
        body += (
            f'<tr>'
            f'<td {_td_nb}>{_link(row)}</td>'
            f'<td {_td}>{escape(str(row.get("ticket_title") or ""))}</td>'
            f'<td {_td_nb}>{escape(str(row.get("service_name") or ""))}</td>'
            f'<td {_td_nb}>{escape(str(row.get("assignee_name") or ""))}</td>'
            f'</tr>'
        )
    return header + body + "</tbody></table>"


def render_executive_report_html(snapshot: dict) -> str:
    import json

    week_label = escape(str(snapshot.get("week_label", "")))
    generated_at = escape(str(snapshot.get("report_generated_at", "")))
    week_range = escape(str(snapshot.get("week_range_label", "")))
    prior_week_range = escape(str(snapshot.get("prior_week_range_label", "")))
    as_of_label = escape(str(snapshot.get("as_of_label", "")))
    tdx_base_url = snapshot.get("tdx_base_url")

    new_tickets = snapshot.get("new_tickets_this_week", 0)
    avg_weekly = snapshot.get("avg_weekly_tickets_created", 0.0)
    ww_delta = snapshot.get("week_over_week_delta_pct")
    sla_rate = snapshot.get("sla_compliance_rate")
    stale = snapshot.get("stale_open_count", 0)
    unassigned = snapshot.get("unassigned_count", 0)
    median_response_tw = snapshot.get("median_first_response_hours_this_week")
    median_response_all = snapshot.get("median_first_response_hours_all_time")

    ww_str = (
        f"{ww_delta:+.1f}% vs {prior_week_range}"
        if ww_delta is not None
        else f"no data for {prior_week_range}"
    )
    sla_str = f"{sla_rate * 100:.0f}%" if sla_rate is not None else "N/A"
    response_all_str = f"{median_response_all:.1f} hrs" if median_response_all is not None else "N/A"
    if median_response_tw is not None:
        response_tw_str = f"{median_response_tw:.1f} hrs"
        response_sub_str = f"{week_range} · all-time: {response_all_str}"
    else:
        response_tw_str = response_all_str
        response_sub_str = f"all-time · no data for {week_range}"
    easy_rate = snapshot.get("customer_effort_easy_rate")
    effort_str = f"{easy_rate * 100:.0f}%" if easy_rate is not None else "N/A"
    effort_period = escape(str(snapshot.get("customer_effort_period_label", "")))

    kpi_cards = "\n".join([
        _executive_kpi_card(
            "New Tickets", str(new_tickets), week_range, ww_str,
            detail_html=_ticket_table(snapshot.get("new_tickets_detail", []), tdx_base_url),
        ),
        _executive_kpi_card("Avg Weekly Tickets", f"{avg_weekly:.1f}", "all-time baseline"),
        _executive_kpi_card("SLA Compliance", sla_str, "all open & recently closed"),
        _executive_kpi_card(
            "Stale Open (>5 biz days)", str(stale), f"as of {as_of_label}",
            detail_html=_ticket_table(snapshot.get("stale_tickets_detail", []), tdx_base_url),
        ),
        _executive_kpi_card(
            "Unassigned Open", str(unassigned), f"as of {as_of_label}",
            detail_html=_ticket_table(snapshot.get("unassigned_tickets_detail", []), tdx_base_url),
        ),
        _executive_kpi_card("Median First Response", response_tw_str, response_sub_str),
        _executive_kpi_card("Customer Effort", effort_str, f"easy or very easy · {effort_period}"),
    ])

    # satisfaction counts table
    sat_counts = snapshot.get("satisfaction_counts", {})
    label_order = ["Very Satisfied", "Satisfied", "Dissatisfied", "Very Dissatisfied"]
    total_sat = sum(sat_counts.values()) or 1
    sat_rows = ""
    for lbl in label_order:
        count = sat_counts.get(lbl, 0)
        pct = count / total_sat * 100
        bar_w = int(pct)
        sat_rows += (
            f'<tr class="border-b border-stone-100">'
            f'<td class="px-4 py-3 text-sm text-stone-700">{escape(lbl)}</td>'
            f'<td class="px-4 py-3 text-sm text-stone-700 text-right">{escape(str(count))}</td>'
            f'<td class="px-4 py-3">'
            f'  <div class="h-2 bg-stone-100 rounded">'
            f'    <div class="h-2 bg-blue-500 rounded" style="width:{bar_w}%"></div>'
            f'  </div>'
            f'</td>'
            f'</tr>'
        )
    if not sat_rows:
        sat_rows = '<tr><td class="px-4 py-3 text-stone-500" colspan="3">No survey data</td></tr>'

    # satisfaction trend table
    trend_rows = ""
    for entry in snapshot.get("satisfaction_trend", []):
        pct = f'{entry["positive_rate"] * 100:.0f}%'
        trend_rows += (
            f'<tr class="border-b border-stone-100">'
            f'<td class="px-4 py-3 text-sm text-stone-700">{escape(str(entry["month"]))}</td>'
            f'<td class="px-4 py-3 text-sm text-stone-700 text-right">{escape(str(entry["total"]))}</td>'
            f'<td class="px-4 py-3 text-sm text-stone-700 text-right">{escape(pct)}</td>'
            f'</tr>'
        )
    if not trend_rows:
        trend_rows = '<tr><td class="px-4 py-3 text-stone-500" colspan="3">No trend data</td></tr>'

    # customer effort table rows
    _td_e = ('style="padding:0.65rem 0.9rem; font-family:\'minion-pro\',serif; '
             'font-size:0.9rem; color:#2c3a4a; border-bottom:1px solid #f0f2f5;"')
    _td_e_r = ('style="padding:0.65rem 0.9rem; font-family:\'myriad-pro\',sans-serif; font-weight:600; '
               'font-size:0.88rem; color:#003963; text-align:right; border-bottom:1px solid #f0f2f5;"')
    effort_label_order = ["Very Easy", "Easy", "Difficult", "Very Difficult"]
    effort_counts = snapshot.get("customer_effort_counts", {})
    total_effort = sum(effort_counts.values()) or 1
    effort_rows = ""
    for lbl in effort_label_order:
        count = effort_counts.get(lbl, 0)
        pct = count / total_effort * 100
        bar_color = "#003963" if lbl in ("Very Easy", "Easy") else "#F59536"
        effort_rows += (
            f'<tr>'
            f'<td {_td_e}>{escape(lbl)}</td>'
            f'<td {_td_e_r}>{escape(str(count))}</td>'
            f'<td style="padding:0.65rem 0.9rem; border-bottom:1px solid #f0f2f5; width:120px;">'
            f'  <div style="height:3px; background:#e8ecf0; border-radius:2px;">'
            f'    <div style="height:3px; background:{bar_color}; border-radius:2px; width:{int(pct)}%;"></div>'
            f'  </div>'
            f'</td>'
            f'</tr>'
        )
    if not effort_rows:
        effort_rows = ('<tr><td colspan="3" style="padding:1rem; font-family:\'myriad-pro\',sans-serif; '
                       'font-size:0.8rem; color:#aab4c0;">No effort data</td></tr>')

    # top services table rows
    _td_s = ('style="padding:0.65rem 0.9rem; font-family:\'minion-pro\',serif; '
             'font-size:0.9rem; color:#2c3a4a; border-bottom:1px solid #f0f2f5;"')
    _td_s_r = ('style="padding:0.65rem 0.9rem; font-family:\'myriad-pro\',sans-serif; font-weight:600; '
               'font-size:0.88rem; color:#003963; text-align:right; border-bottom:1px solid #f0f2f5;"')
    service_rows = ""
    for svc in snapshot.get("top_services", []):
        service_rows += (
            f'<tr>'
            f'<td {_td_s}>{escape(str(svc.get("service_name") or ""))}</td>'
            f'<td {_td_s_r}>{escape(str(svc.get("count") or 0))}</td>'
            f'</tr>'
        )
    if not service_rows:
        service_rows = ('<tr><td colspan="2" style="padding:1rem; font-family:\'myriad-pro\',sans-serif; '
                        'font-size:0.8rem; color:#aab4c0;">No service data</td></tr>')

    # plotly data — escape </script> injection
    buckets_tw = snapshot.get("completion_hours_this_week", [])
    buckets_all = snapshot.get("completion_hours_all_time", [])
    _ref = buckets_all if buckets_all else buckets_tw

    def _to_pct(buckets: list) -> list[float]:
        total = sum(b["count"] for b in buckets) or 1
        return [round(b["count"] / total * 100, 1) for b in buckets]

    chart_data_json = json.dumps({
        "labels": [b["label"] for b in _ref],
        "this_week": _to_pct(buckets_tw) if buckets_tw else [],
        "all_time": _to_pct(buckets_all) if buckets_all else [],
        "this_week_label": snapshot.get("week_range_label", "This Week"),
    }).replace("</", r"<\/")

    # table header style helpers
    _th_style = ('style="padding:0.6rem 1rem; font-family:\'myriad-pro\',sans-serif; font-size:0.62rem; '
                 'font-weight:600; color:rgba(255,255,255,0.35); text-transform:uppercase; letter-spacing:0.12em; '
                 'text-align:left; border-bottom:1px solid rgba(251,185,58,0.15);"')
    _th_style_r = ('style="padding:0.6rem 0.9rem; font-family:\'myriad-pro\',sans-serif; font-size:0.6rem; '
                   'font-weight:600; color:#8a9bb0; text-transform:uppercase; letter-spacing:0.12em; '
                   'text-align:right; border-bottom:1px solid #e4e8ee;"')
    _th_style = ('style="padding:0.6rem 0.9rem; font-family:\'myriad-pro\',sans-serif; font-size:0.6rem; '
                 'font-weight:600; color:#8a9bb0; text-transform:uppercase; letter-spacing:0.12em; '
                 'text-align:left; border-bottom:1px solid #e4e8ee;"')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>IT Executive Report</title>
  <link rel="stylesheet" href="https://use.typekit.net/apf8ssc.css" />
  <script src="https://cdn.plot.ly/plotly-3.4.0.min.js"></script>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    html {{ scroll-behavior: smooth; }}
    body {{
      background: #f4f6f8;
      color: #1a2534;
      font-family: 'minion-pro', Georgia, serif;
      font-size: 16px;
      line-height: 1.65;
      min-height: 100vh;
    }}
    @keyframes fadeUp {{
      from {{ opacity: 0; transform: translateY(8px); }}
      to   {{ opacity: 1; transform: translateY(0); }}
    }}
    .section-eyebrow {{
      font-family: 'myriad-pro', sans-serif;
      font-size: 0.6rem;
      font-weight: 600;
      color: #8a9bb0;
      text-transform: uppercase;
      letter-spacing: 0.18em;
      margin-bottom: 0.4rem;
    }}
    .section-heading {{
      font-family: 'myriad-pro', sans-serif;
      font-size: 1rem;
      font-weight: 600;
      color: #003963;
      letter-spacing: 0.005em;
      margin-bottom: 1.2rem;
      padding-bottom: 0.55rem;
      border-bottom: 1px solid #d8dfe8;
    }}
    .card {{
      background: #fff;
      border: 1px solid #e4e8ee;
      box-shadow: 0 1px 3px rgba(0,57,99,0.06);
    }}
    .data-table {{ width: 100%; border-collapse: collapse; }}
    details summary::-webkit-details-marker {{ display: none; }}
    details[open] > summary {{ color: #003963; }}
  </style>
</head>
<body>

  <!-- Header: CU Blue band -->
  <header style="background:#003963; padding:2rem 3rem 1.75rem;">
    <div style="max-width:1100px; margin:0 auto;">
      <p style="font-family:'myriad-pro',sans-serif; font-size:0.6rem; font-weight:600;
                color:rgba(251,185,58,0.75); text-transform:uppercase; letter-spacing:0.2em;
                margin-bottom:0.45rem;">Cedarville University &nbsp;·&nbsp; Information Technology</p>
      <h1 style="font-family:'myriad-pro',sans-serif; font-size:1.85rem; font-weight:700;
                 color:#fff; letter-spacing:0.01em; line-height:1.15;">IT Executive Report</h1>
      <div style="margin-top:0.55rem; display:flex; gap:1.75rem; align-items:baseline; flex-wrap:wrap;">
        <span style="font-family:'minion-pro',serif; font-style:italic; font-size:0.95rem;
                     color:#FBB93A;">Week of {week_label}</span>
        <span style="font-family:'myriad-pro',sans-serif; font-size:0.7rem;
                     color:rgba(255,255,255,0.4); letter-spacing:0.03em;">Generated {generated_at}</span>
      </div>
    </div>
  </header>
  <!-- Thin gold rule under header -->
  <div style="height:3px; background:linear-gradient(to right,#FBB93A,#F59536 40%,transparent);"></div>

  <main style="max-width:1100px; margin:0 auto; padding:2.5rem 3rem 4rem;">

    <!-- ── AT A GLANCE ── -->
    <section style="margin-bottom:3rem; animation:fadeUp 0.4s ease both;">
      <p class="section-eyebrow">At a Glance</p>
      <div style="display:grid; grid-template-columns:repeat(auto-fill,minmax(195px,1fr)); gap:1rem;">
        {kpi_cards}
      </div>
    </section>

    <!-- ── COMPLETION TIME ── -->
    <section style="margin-bottom:3rem; animation:fadeUp 0.45s ease 0.05s both;">
      <p class="section-eyebrow">Ticket Volume</p>
      <p class="section-heading">Completion Time &ensp;—&ensp; {week_range} vs All Time</p>
      <div class="card" style="padding:1rem 0.5rem;">
        <div id="completion-chart" style="height:300px;"></div>
      </div>
    </section>

    <!-- ── CUSTOMER EFFORT + TOP SERVICES ── -->
    <section style="margin-bottom:3rem; animation:fadeUp 0.45s ease 0.1s both;
                    display:grid; grid-template-columns:1fr 1fr; gap:2rem;">

      <div>
        <p class="section-eyebrow">Survey Results</p>
        <p class="section-heading">Customer Effort &ensp;—&ensp; {effort_period}</p>
        <div class="card">
          <table class="data-table">
            <thead><tr>
              <th {_th_style}>Effort Level</th>
              <th {_th_style_r}>Count</th>
              <th {_th_style}>Share</th>
            </tr></thead>
            <tbody>{effort_rows}</tbody>
          </table>
        </div>
      </div>

      <div>
        <p class="section-eyebrow">Ticket Breakdown</p>
        <p class="section-heading">Top Request Categories &ensp;—&ensp; {week_range}</p>
        <div class="card">
          <table class="data-table">
            <thead><tr>
              <th {_th_style}>Service</th>
              <th {_th_style_r}>Tickets</th>
            </tr></thead>
            <tbody>{service_rows}</tbody>
          </table>
        </div>
      </div>

    </section>

  </main>

  <footer style="border-top:1px solid #d8dfe8; padding:1.25rem 3rem;
                 font-family:'myriad-pro',sans-serif; font-size:0.62rem;
                 color:#aab4c0; letter-spacing:0.07em; text-transform:uppercase;">
    Cedarville University Information Technology &nbsp;·&nbsp; Confidential
  </footer>

  <script>
    window.EXEC_CHART_DATA = {chart_data_json};
    (function () {{
      var d = window.EXEC_CHART_DATA;
      var traces = [
        {{
          type: 'bar',
          x: d.labels,
          y: d.all_time,
          name: 'All Time',
          marker: {{ color: '#c8d5e0' }},
        }},
        {{
          type: 'bar',
          x: d.labels,
          y: d.this_week,
          name: d.this_week_label,
          marker: {{ color: '#003963' }},
        }},
      ];
      var layout = {{
        barmode: 'group',
        margin: {{ t: 12, r: 16, b: 80, l: 52 }},
        xaxis: {{
          title: {{ text: 'Business Hours', font: {{ family: 'myriad-pro, sans-serif', size: 11, color: '#8a9bb0' }} }},
          tickfont: {{ family: 'myriad-pro, sans-serif', size: 11, color: '#8a9bb0' }},
          gridcolor: '#edf0f4', linecolor: '#d8dfe8',
        }},
        yaxis: {{
          title: {{ text: '% of Tickets', font: {{ family: 'myriad-pro, sans-serif', size: 11, color: '#8a9bb0' }} }},
          ticksuffix: '%',
          zeroline: false,
          tickfont: {{ family: 'myriad-pro, sans-serif', size: 11, color: '#8a9bb0' }},
          gridcolor: '#edf0f4',
        }},
        legend: {{
          orientation: 'h', y: -0.28,
          font: {{ family: 'myriad-pro, sans-serif', size: 11, color: '#6b7c93' }},
        }},
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
      }};
      Plotly.newPlot('completion-chart', traces, layout, {{ responsive: true, displayModeBar: false }});
    }})();
  </script>
</body>
</html>
"""


def write_executive_report(snapshot: dict, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_executive_report_html(snapshot))
    return output_path


# ─── Executive Email (HTML email, no JS, table-based layout) ──────────────────


def _email_kpi_card(label: str, value: str, sub: str = "", sub2: str = "") -> str:
    """KPI metric card for HTML email — inline styles, no JS."""
    sub_html = (
        f'<div style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:11px;'
        f'color:#6b7c93;margin-top:4px;line-height:1.4;">{sub}</div>'
    ) if sub else ""
    sub2_html = (
        f'<div style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:10px;'
        f'color:#9aabb8;margin-top:2px;line-height:1.4;">{sub2}</div>'
    ) if sub2 else ""
    return (
        f'<div style="border-top:3px solid #003963;padding:14px 16px 16px;background:#f7f9fc;">'
        f'<div style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:9px;'
        f'font-weight:700;color:#8a9bb0;text-transform:uppercase;letter-spacing:0.13em;'
        f'margin-bottom:8px;">{label}</div>'
        f'<div style="font-family:Georgia,\'Times New Roman\',serif;font-size:28px;'
        f'font-weight:bold;color:#003963;line-height:1;margin-bottom:5px;">'
        f'{escape(value)}</div>'
        f'{sub_html}{sub2_html}'
        f'</div>'
    )


def _email_completion_chart(
    buckets_tw: list[dict],
    buckets_all: list[dict],
    week_range_label: str,
) -> str:
    """Dual horizontal bar chart for completion time — pure HTML tables, no JS."""
    ref = buckets_all if buckets_all else buckets_tw
    if not ref:
        return ('<p style="font-family:Helvetica,Arial,sans-serif;font-size:12px;'
                'color:#aab4c0;margin:0;">No data</p>')

    total_tw = sum(b["count"] for b in buckets_tw) or 1
    total_all = sum(b["count"] for b in buckets_all) or 1

    def _bar_cell(pct: int, color: str) -> str:
        inner = (
            f'<td width="{pct}%" style="background:{color};height:7px;border-radius:2px;"></td>'
            f'<td style="height:7px;"></td>'
        ) if pct > 0 else '<td style="height:7px;"></td>'
        return (
            f'<table cellpadding="0" cellspacing="0" style="width:100%;">'
            f'<tr>{inner}</tr></table>'
        )

    _hdr = ('style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:9px;'
            'font-weight:700;color:#8a9bb0;text-transform:uppercase;letter-spacing:0.1em;'
            'padding-bottom:10px;"')
    _lbl = ('style="padding:7px 8px 7px 0;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
            'font-size:10px;color:#6b7c93;white-space:nowrap;vertical-align:middle;'
            'border-bottom:1px solid #f0f2f5;"')
    _bar = 'style="padding:7px 4px;vertical-align:middle;border-bottom:1px solid #f0f2f5;"'
    _pct_tw = ('style="width:34px;padding:7px 0 7px 4px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
               'font-size:10px;font-weight:700;color:#003963;text-align:right;white-space:nowrap;'
               'vertical-align:middle;border-bottom:1px solid #f0f2f5;"')
    _pct_all = ('style="width:34px;padding:7px 0 7px 4px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
                'font-size:10px;font-weight:700;color:#6a90ad;text-align:right;white-space:nowrap;'
                'vertical-align:middle;border-bottom:1px solid #f0f2f5;"')
    _gap = 'style="width:12px;border-bottom:1px solid #f0f2f5;"'

    rows = (
        f'<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;">'
        f'<tr>'
        f'<td width="68"></td>'
        f'<td {_hdr}>{escape(str(week_range_label))}</td>'
        f'<td width="34"></td>'
        f'<td width="12"></td>'
        f'<td {_hdr}>All Time</td>'
        f'<td width="34"></td>'
        f'</tr>'
    )

    for b in ref:
        lbl = b["label"]
        tw_count = next((x["count"] for x in buckets_tw if x["label"] == lbl), 0)
        all_count = next((x["count"] for x in buckets_all if x["label"] == lbl), 0)
        tw_pct = int(tw_count / total_tw * 100)
        all_pct = int(all_count / total_all * 100)
        rows += (
            f'<tr>'
            f'<td width="68" {_lbl}>{escape(lbl)}</td>'
            f'<td {_bar}>{_bar_cell(tw_pct, "#003963")}</td>'
            f'<td {_pct_tw}>{tw_pct}%</td>'
            f'<td {_gap}></td>'
            f'<td {_bar}>{_bar_cell(all_pct, "#6a90ad")}</td>'
            f'<td {_pct_all}>{all_pct}%</td>'
            f'</tr>'
        )

    return rows + '</table>'


def render_executive_email_html(snapshot: dict) -> str:
    """Render the executive snapshot as a self-contained HTML email (no JS, inline styles)."""
    week_label = escape(str(snapshot.get("week_label", "")))
    generated_at = escape(str(snapshot.get("report_generated_at", ""))[:10])
    week_range = escape(str(snapshot.get("week_range_label", "")))
    prior_week_range = escape(str(snapshot.get("prior_week_range_label", "")))
    as_of_label = escape(str(snapshot.get("as_of_label", "")))
    effort_period = escape(str(snapshot.get("customer_effort_period_label", "")))

    new_tickets = snapshot.get("new_tickets_this_week", 0)
    avg_weekly = snapshot.get("avg_weekly_tickets_created", 0.0)
    ww_delta = snapshot.get("week_over_week_delta_pct")
    sla_rate = snapshot.get("sla_compliance_rate")
    stale = snapshot.get("stale_open_count", 0)
    unassigned = snapshot.get("unassigned_count", 0)
    median_tw = snapshot.get("median_first_response_hours_this_week")
    median_all = snapshot.get("median_first_response_hours_all_time")
    easy_rate = snapshot.get("customer_effort_easy_rate")

    ww_str = (
        f"{ww_delta:+.1f}% vs {prior_week_range}"
        if ww_delta is not None else f"no data for {prior_week_range}"
    )
    sla_str = f"{sla_rate * 100:.0f}%" if sla_rate is not None else "N/A"
    response_all_str = f"{median_all:.1f} hrs" if median_all is not None else "N/A"
    if median_tw is not None:
        response_tw_str = f"{median_tw:.1f} hrs"
        response_sub_str = f"{week_range} &middot; all-time: {response_all_str}"
    else:
        response_tw_str = response_all_str
        response_sub_str = f"all-time &middot; no data for {week_range}"
    effort_str = f"{easy_rate * 100:.0f}%" if easy_rate is not None else "N/A"

    kpi_row_1 = (
        f'<td width="50%" style="padding:0 6px 0 0;vertical-align:top;">'
        f'{_email_kpi_card("New Tickets", str(new_tickets), week_range, ww_str)}</td>'
        f'<td width="50%" style="padding:0 0 0 6px;vertical-align:top;">'
        f'{_email_kpi_card("Avg Weekly Tickets", f"{avg_weekly:.1f}", "all-time baseline")}</td>'
    )
    kpi_row_2 = (
        f'<td width="50%" style="padding:0 6px 0 0;vertical-align:top;">'
        f'{_email_kpi_card("SLA Compliance", sla_str, "all open &amp; recently closed")}</td>'
        f'<td width="50%" style="padding:0 0 0 6px;vertical-align:top;">'
        f'{_email_kpi_card("Median First Response", response_tw_str, response_sub_str)}</td>'
    )
    kpi_row_3 = (
        f'<td width="50%" style="padding:0 6px 0 0;vertical-align:top;">'
        f'{_email_kpi_card("Stale Open (&gt;5 biz days)", str(stale), f"as of {as_of_label}")}</td>'
        f'<td width="50%" style="padding:0 0 0 6px;vertical-align:top;">'
        f'{_email_kpi_card("Unassigned Open", str(unassigned), f"as of {as_of_label}")}</td>'
    )

    completion_chart = _email_completion_chart(
        snapshot.get("completion_hours_this_week", []),
        snapshot.get("completion_hours_all_time", []),
        week_range,
    )

    # customer effort rows
    _EASY = {"Very Easy", "Easy"}
    effort_label_order = ["Very Easy", "Easy", "Difficult", "Very Difficult"]
    effort_counts = snapshot.get("customer_effort_counts", {})
    total_effort = sum(effort_counts.values()) or 1
    effort_rows = ""
    for lbl in effort_label_order:
        count = effort_counts.get(lbl, 0)
        pct = int(count / total_effort * 100)
        bar_color = "#003963" if lbl in _EASY else "#F59536"
        bar_inner = (
            f'<td width="{pct}%" style="background:{bar_color};height:6px;border-radius:1px;"></td>'
            f'<td style="height:6px;"></td>'
        ) if pct > 0 else '<td style="height:6px;"></td>'
        effort_rows += (
            f'<tr style="border-bottom:1px solid #f0f2f5;">'
            f'<td style="padding:6px 8px 6px 0;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
            f'font-size:11px;color:#2c3a4a;vertical-align:middle;">{escape(lbl)}</td>'
            f'<td style="padding:6px 4px;vertical-align:middle;">'
            f'<table cellpadding="0" cellspacing="0" style="width:100%;">'
            f'<tr>{bar_inner}</tr></table></td>'
            f'<td width="34" style="padding:6px 0 6px 4px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
            f'font-size:10px;font-weight:700;color:#003963;text-align:right;white-space:nowrap;'
            f'vertical-align:middle;">{escape(str(count))}</td>'
            f'</tr>'
        )
    if not effort_rows:
        effort_rows = ('<tr><td colspan="3" style="padding:12px;font-family:Helvetica,Arial,sans-serif;'
                       'font-size:11px;color:#aab4c0;">No data</td></tr>')

    # top services rows
    service_rows = ""
    for svc in snapshot.get("top_services", []):
        service_rows += (
            f'<tr style="border-bottom:1px solid #f0f2f5;">'
            f'<td style="padding:7px 8px 7px 0;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
            f'font-size:11px;color:#2c3a4a;">{escape(str(svc.get("service_name") or ""))}</td>'
            f'<td style="padding:7px 0;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
            f'font-size:11px;font-weight:700;color:#003963;text-align:right;white-space:nowrap;">'
            f'{escape(str(svc.get("count") or 0))}</td>'
            f'</tr>'
        )
    if not service_rows:
        service_rows = ('<tr><td colspan="2" style="padding:12px;font-family:Helvetica,Arial,sans-serif;'
                        'font-size:11px;color:#aab4c0;">No data</td></tr>')

    _eyebrow = ('font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:9px;'
                'font-weight:700;color:#8a9bb0;text-transform:uppercase;letter-spacing:0.14em;'
                'margin:0 0 4px;')
    _heading = ('font-family:Georgia,\'Times New Roman\',serif;font-size:14px;'
                'font-weight:bold;color:#003963;margin:0 0 16px;padding-bottom:10px;'
                'border-bottom:1px solid #d8dfe8;')
    _section = 'background:#ffffff;padding:28px 32px;'
    _divider = ('<tr><td style="height:1px;background:#e4e8ee;font-size:1px;line-height:1px;">'
                '&#8203;</td></tr>')

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <title>IT Executive Report</title>
</head>
<body style="margin:0;padding:0;background:#edf1f5;font-family:Georgia,'Times New Roman',serif;">

<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#edf1f5;">
  <tr>
    <td align="center" style="padding:28px 16px;">

      <table width="600" cellpadding="0" cellspacing="0" border="0"
             style="max-width:600px;border-radius:6px;overflow:hidden;
                    box-shadow:0 8px 24px rgba(0,57,99,0.08);">

        <!-- HEADER -->
        <tr>
          <td style="background:#003963;padding:28px 32px 24px;">
            <p style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:9px;
                      font-weight:700;color:rgba(251,185,58,0.8);text-transform:uppercase;
                      letter-spacing:0.2em;margin:0 0 8px 0;">
              Cedarville University &nbsp;&middot;&nbsp; Information Technology
            </p>
            <h1 style="font-family:Georgia,'Times New Roman',serif;font-size:24px;
                       font-weight:bold;color:#ffffff;margin:0 0 10px 0;line-height:1.2;">
              IT Executive Report
            </h1>
            <table cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="font-family:Georgia,'Times New Roman',serif;font-style:italic;
                           font-size:14px;color:#FBB93A;padding-right:16px;">
                  Week of {week_label}
                </td>
                <td style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:11px;
                           color:rgba(255,255,255,0.4);">
                  Generated {generated_at}
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- GOLD RULE -->
        <tr>
          <td style="height:3px;background:linear-gradient(to right,#FBB93A,#F59536 40%,transparent);
                     font-size:1px;line-height:1px;">&#8203;</td>
        </tr>

        <!-- AT A GLANCE -->
        <tr>
          <td style="{_section}">
            <p style="{_eyebrow}">At a Glance</p>
            <table cellpadding="0" cellspacing="0" width="100%">
              <tr>{kpi_row_1}</tr>
              <tr><td colspan="2" style="height:10px;"></td></tr>
              <tr>{kpi_row_2}</tr>
              <tr><td colspan="2" style="height:10px;"></td></tr>
              <tr>{kpi_row_3}</tr>
            </table>
          </td>
        </tr>

        {_divider}

        <!-- COMPLETION TIME -->
        <tr>
          <td style="{_section}">
            <p style="{_eyebrow}">Ticket Volume</p>
            <p style="{_heading}">Completion Time &mdash; {week_range} vs All Time</p>
            {completion_chart}
          </td>
        </tr>

        {_divider}

        <!-- CUSTOMER EFFORT + TOP SERVICES -->
        <tr>
          <td style="{_section}">
            <table cellpadding="0" cellspacing="0" width="100%">
              <tr>
                <td width="50%" style="vertical-align:top;padding-right:20px;">
                  <p style="{_eyebrow}">Survey Results</p>
                  <p style="{_heading}">Customer Effort &mdash; {effort_period}</p>
                  <table cellpadding="0" cellspacing="0" width="100%">
                    {effort_rows}
                  </table>
                  <p style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:10px;
                             color:#6b7c93;margin:10px 0 0 0;">{effort_str} easy or very easy</p>
                </td>
                <td width="50%" style="vertical-align:top;padding-left:20px;
                                       border-left:1px solid #e4e8ee;">
                  <p style="{_eyebrow}">Ticket Breakdown</p>
                  <p style="{_heading}">Top Request Categories &mdash; {week_range}</p>
                  <table cellpadding="0" cellspacing="0" width="100%">
                    {service_rows}
                  </table>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- FOOTER -->
        <tr>
          <td style="background:#002a4a;padding:16px 32px;">
            <p style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:9px;
                      font-weight:600;color:rgba(255,255,255,0.35);text-transform:uppercase;
                      letter-spacing:0.1em;margin:0;">
              Cedarville University Information Technology &nbsp;&middot;&nbsp; Confidential
            </p>
          </td>
        </tr>

      </table>
    </td>
  </tr>
</table>

</body>
</html>
"""


def write_executive_email(snapshot: dict, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_executive_email_html(snapshot))
    return output_path


# ── CFO Update email ──────────────────────────────────────────────────────────

_LABEL_H = 10  # fixed height of the value-label row above each bar


def _cfo_sparkline(
    weekly: list[dict],
    *,
    max_h: int = 28,
    bar_w: int = 5,
    gap: int = 2,
    current_color: str = "#003963",
    prior_color: str = "#b8cede",
    min_h: int = 2,
    period_days: int = 7,
) -> str:
    """Render a vertical bar sparkline as nested email-safe HTML tables.

    weekly: list of {"week": label, "count": n}, oldest first, newest last.
    The final entry is highlighted as the current period.

    Bars are scaled from 0 to max so heights are proportional to absolute values.
    The current-period count is shown as a small label above the rightmost bar.
    A caption row shows the date range and period unit.
    """
    if not weekly:
        return ""
    counts = [w["count"] for w in weekly]
    n = len(counts)
    total_w = n * bar_w + (n - 1) * gap

    # Scale from 0 so bar heights are proportional to absolute values
    hi = max(counts) or 1  # guard against all-zero

    bars = ""
    for i, (w, count) in enumerate(zip(weekly, counts)):
        is_current = i == n - 1
        color = current_color if is_current else prior_color
        bar_h = max(min_h, round(count / hi * max_h))
        spacer_h = max_h - bar_h

        # Show the current period's value as a small label above its bar
        if is_current:
            label_td = (
                f'<td height="{_LABEL_H}" '
                f'style="height:{_LABEL_H}px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
                f'font-size:7px;font-weight:700;color:{current_color};'
                f'text-align:center;white-space:nowrap;vertical-align:bottom;'
                f'line-height:{_LABEL_H}px;">{count}</td>'
            )
        else:
            label_td = (
                f'<td height="{_LABEL_H}" '
                f'style="height:{_LABEL_H}px;font-size:1px;line-height:1px;">&#8203;</td>'
            )

        bars += (
            f'<td width="{bar_w}" style="padding:0 {gap // 2}px 0 0;vertical-align:bottom;">'
            f'<table cellpadding="0" cellspacing="0" border="0" style="width:{bar_w}px;">'
            f'<tr>{label_td}</tr>'
            f'<tr><td height="{spacer_h}" style="height:{spacer_h}px;font-size:1px;line-height:1px;">&#8203;</td></tr>'
            f'<tr><td height="{bar_h}" style="height:{bar_h}px;background:{color};'
            f'font-size:1px;line-height:1px;border-radius:1px 1px 0 0;" '
            f'title="{escape(w["week"])}: {escape(str(count))}">&#8203;</td></tr>'
            f'</table>'
            f'</td>'
        )

    # Caption: date range + period unit
    first = escape(weekly[0]["week"])
    last = escape(weekly[-1]["week"])
    unit = f"{period_days}d"
    caption = (
        f'<tr><td colspan="{n}" '
        f'style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
        f'font-size:9px;color:#7b8da2;padding-top:5px;white-space:nowrap;'
        f'letter-spacing:0.02em;">'
        f'{first} &#8594; {last} &nbsp;&middot;&nbsp; {unit} periods'
        f'</td></tr>'
    )

    return (
        f'<table cellpadding="0" cellspacing="0" border="0" '
        f'style="width:{total_w}px;border-collapse:collapse;">'
        f'<tr style="vertical-align:bottom;">{bars}</tr>'
        f'{caption}'
        f'</table>'
    )


def _cfo_datatype_sparkline(
    weekly: list[dict],
    *,
    current_color: str = "#003963",
    period_days: int = 7,
) -> str:
    if not weekly:
        return ""
    counts = [int(w.get("count", 0) or 0) for w in weekly]
    hi = max(counts) or 1
    values = [str(max(0, min(100, int(round(count / hi * 100))))) for count in counts]
    expression = "{b:" + ",".join(values) + "}"
    first = escape(str(weekly[0].get("week", "")))
    last = escape(str(weekly[-1].get("week", "")))
    current_count = escape(str(counts[-1]))
    return (
        '<table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">'
        '<tr>'
        '<td style="font-family:\'Datatype\',Datatype,Helvetica Neue,Helvetica,Arial,sans-serif;'
        f'font-size:24px;line-height:1;color:{current_color};font-weight:400;'
        "font-variation-settings:'wdth' 15, 'wght' 400;letter-spacing:0;white-space:nowrap;"
        "font-feature-settings:'liga' 1, 'calt' 1;font-variant-ligatures:common-ligatures contextual;"
        'mso-font-alt:Arial;">'
        + escape(expression)
        + '</td>'
        '<td style="padding-left:7px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
        f'font-size:8px;font-weight:700;color:{current_color};vertical-align:middle;'
        'white-space:nowrap;">'
        + current_count
        + '</td>'
        '</tr>'
        '<tr><td colspan="2" style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
        'font-size:9px;color:#7b8da2;padding-top:4px;white-space:nowrap;letter-spacing:0.02em;">'
        f'{first} &#8594; {last} &nbsp;&middot;&nbsp; {period_days}d periods'
        '</td></tr>'
        '</table>'
    )


def _cfo_delta_badge(pct: float | None, prior_label: str) -> str:
    """Render a compact delta badge: e.g. '+12% vs prior week'."""
    if pct is None:
        return (
            '<span style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
            'font-size:10px;color:#8597aa;">no prior data</span>'
        )
    arrow = "&#9650;" if pct >= 0 else "&#9660;"
    color = "#b54708" if pct > 0 else ("#027a48" if pct < 0 else "#5f7186")
    sign = "+" if pct >= 0 else ""
    return (
        f'<span style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
        f'font-size:10px;font-weight:700;color:{color};">'
        f'{arrow} {sign}{pct:.1f}%</span>'
        f'<span style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
        f'font-size:10px;color:#73859a;"> vs {escape(prior_label)}</span>'
    )


def _cfo_volume_row(
    label: str,
    tw: int,
    pw: int,
    ya: int,
    ww_pct: float | None,
    yy_pct: float | None,
    current_period_label: str,
    prior_week_label: str,
    year_ago_label: str,
    prior_delta_label: str,
    sparkline_html: str = "",
) -> str:
    """One row in the ticket volume comparison table."""
    _cell = (
        'style="padding:10px 12px;vertical-align:top;'
        'border-bottom:1px solid #edf0f4;"'
    )
    _num = (
        'style="font-family:Georgia,\'Times New Roman\',serif;font-size:20px;'
        'font-weight:bold;color:#003963;line-height:1;display:block;margin-bottom:2px;"'
    )
    _sub = (
        'style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
        'font-size:10px;color:#617489;display:block;margin-top:4px;"'
    )
    _lbl = (
        'style="padding:10px 12px 10px 0;vertical-align:middle;'
        'border-bottom:1px solid #edf0f4;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
        'font-size:12px;font-weight:600;color:#243447;white-space:nowrap;"'
    )
    _spark_cell = (
        'style="padding:10px 0 10px 12px;vertical-align:middle;'
        'border-bottom:1px solid #edf0f4;"'
    )
    spark_td = f'<td {_spark_cell}>{sparkline_html}</td>' if sparkline_html else ""
    return (
        f'<tr>'
        f'<td {_lbl}>{escape(label)}</td>'
        f'<td {_cell}>'
        f'  <span {_num}>{escape(str(tw))}</span>'
        f'  <span {_sub}>{escape(current_period_label)}</span>'
        f'</td>'
        f'<td {_cell}>'
        f'  <span {_num} style="color:#6a90ad;">{escape(str(pw))}</span>'
        f'  <span {_sub}>{escape(prior_week_label)}</span>'
        f'  <br/>{_cfo_delta_badge(ww_pct, prior_delta_label)}'
        f'</td>'
        f'<td {_cell}>'
        f'  <span {_num} style="color:#6a90ad;">{escape(str(ya))}</span>'
        f'  <span {_sub}>{escape(year_ago_label)}</span>'
        f'  <br/>{_cfo_delta_badge(yy_pct, "year ago")}'
        f'</td>'
        f'{spark_td}'
        f'</tr>'
    )


def render_cfo_email_html(
    snapshot: dict,
    *,
    header_burst_img_src: str | None = None,
    datatype_sparklines: bool = False,
) -> str:
    """Render the CFO Update as a self-contained HTML email (no JS, inline styles)."""
    period_label = escape(str(snapshot.get("period_label") or snapshot.get("week_range_label", "")))
    generated_at = escape(str(snapshot.get("report_generated_at", ""))[:10])
    week_range = period_label
    prior_week_range = snapshot.get("prior_week_range_label", "prior week")
    year_ago_range = snapshot.get("year_ago_range_label", "year ago")
    volume_period_label = str(snapshot.get("volume_period_label") or "last 7 days")
    prior_volume_period_label = str(snapshot.get("prior_volume_period_label") or "prior 7 days")

    total_open = snapshot.get("total_open_tickets", 0)
    total_open_delta = snapshot.get("total_open_tickets_ww_delta", 0)
    projects: list[dict] = snapshot.get("youtrack_projects", [])
    project_movement: dict[str, int] = snapshot.get("youtrack_project_movement", {})
    aha_roadmap: dict[str, object] = snapshot.get("aha_roadmap", {}) or {}
    discussion_items: list[dict[str, str] | str] = snapshot.get("discussion_items", []) or []
    survey_comments: list[dict] = snapshot.get("survey_comments", [])
    survey_satisfaction_counts: dict[str, int] = snapshot.get("survey_satisfaction_counts", {})
    survey_satisfaction_total = int(snapshot.get("survey_satisfaction_total", 0) or 0)
    survey_trailing_year_counts: dict[str, int] = snapshot.get(
        "survey_satisfaction_counts_trailing_year",
        {},
    )
    survey_trailing_year_total = int(snapshot.get("survey_satisfaction_total_trailing_year", 0) or 0)
    survey_trailing_year_effective_start = str(
        snapshot.get("survey_trailing_year_effective_start_label") or ""
    ).strip()

    def _truncate_metadata(text: str, max_length: int = 58) -> str:
        clean = text.strip()
        if len(clean) <= max_length:
            return clean
        return clean[: max_length - 3].rstrip() + "..."

    def _discussion_rows(items: list[dict[str, str] | str]) -> str:
        if not items:
            return (
                '<li style="margin-bottom:6px;">[Replace with your first discussion point for this week.]</li>'
                '<li style="margin-bottom:6px;">[Replace with your second discussion point or key highlight.]</li>'
                '<li style="margin-bottom:6px;">[Replace with any risk, blocker, or upcoming milestone.]</li>'
                '<li>[Replace with a request for input or decision needed.]</li>'
            )
        rows = ""
        for item in items:
            if isinstance(item, dict):
                source = str(item.get("source") or "").strip()
                text = str(item.get("text") or "").strip()
            else:
                source = ""
                text = str(item).strip()
            if not text:
                continue
            source_html = (
                '<span style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
                'font-size:10px;font-weight:700;color:#003963;background:#f4d487;'
                'border:1px solid #e1bd64;border-radius:999px;padding:2px 6px;'
                'margin-right:7px;white-space:nowrap;">'
                + escape(source)
                + '</span>'
                if source
                else ""
            )
            rows += (
                '<li style="margin-bottom:7px;">'
                + source_html
                + escape(text)
                + '</li>'
            )
        return rows or _discussion_rows([])

    def _signed_count(value: int) -> str:
        return f"+{value}" if value > 0 else str(value)

    def _trend_color(value: int, *, positive: str, negative: str, neutral: str) -> str:
        if value > 0:
            return positive
        if value < 0:
            return negative
        return neutral

    # ── Sparklines ────────────────────────────────────────────────────────────
    sparkline_renderer = _cfo_datatype_sparkline if datatype_sparklines else _cfo_sparkline
    created_spark = sparkline_renderer(snapshot.get("created_weekly", []))
    closed_spark = sparkline_renderer(snapshot.get("closed_weekly", []))
    open_spark = sparkline_renderer(snapshot.get("open_weekly", []))
    has_spark = bool(created_spark)

    # ── Ticket volume rows ─────────────────────────────────────────────────────
    created_row = _cfo_volume_row(
        "Created",
        snapshot.get("tickets_created_this_week", 0),
        snapshot.get("tickets_created_prior_week", 0),
        snapshot.get("tickets_created_year_ago", 0),
        snapshot.get("tickets_created_ww_delta_pct"),
        snapshot.get("tickets_created_yy_delta_pct"),
        volume_period_label,
        prior_week_range,
        year_ago_range,
        prior_volume_period_label,
        sparkline_html=created_spark,
    )
    closed_row = _cfo_volume_row(
        "Closed",
        snapshot.get("tickets_closed_this_week", 0),
        snapshot.get("tickets_closed_prior_week", 0),
        snapshot.get("tickets_closed_year_ago", 0),
        snapshot.get("tickets_closed_ww_delta_pct"),
        snapshot.get("tickets_closed_yy_delta_pct"),
        volume_period_label,
        prior_week_range,
        year_ago_range,
        prior_volume_period_label,
        sparkline_html=closed_spark,
    )

    # ── In Progress issues (from board sprint) ─────────────────────────────────
    _LOREM = (
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
        "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua."
    )
    project_rows = ""
    for p in projects[:30]:
        summary = escape(str(p.get("summary") or p.get("name") or ""))
        badge = escape(str(p.get("it_team") or p.get("team_name") or p.get("short_name") or ""))
        assignee = escape(str(p.get("assignee") or p.get("leader_name") or ""))
        new_badge = ""
        if p.get("is_new_this_week"):
            new_badge = (
                '<span style="display:inline-block;margin-left:7px;vertical-align:1px;'
                'font-family:Georgia,\'Times New Roman\',serif;font-size:10px;'
                'font-weight:bold;font-style:italic;color:#003963;background:#FBB93A;'
                'border:1px solid #d99a27;border-radius:2px;padding:1px 6px 2px;'
                'line-height:1.1;letter-spacing:0.01em;white-space:nowrap;">'
                'New</span>'
            )
        assignee_cell = (
            f'<span style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
            f'font-size:10px;color:#5f7287;">{assignee}</span>'
            if assignee else ""
        )
        project_rows += (
            f'<tr style="border-bottom:1px solid #edf1f5;">'
            f'<td style="padding:10px 10px 10px 0;vertical-align:top;width:84px;">'
            f'  <span style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
            f'  font-size:9px;font-weight:700;color:#fff;background:#003963;'
            f'  padding:3px 7px;border-radius:999px;letter-spacing:0.04em;white-space:nowrap;">{badge}</span>'
            f'</td>'
            f'<td style="padding:10px 0;vertical-align:top;">'
            f'  <span style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
            f'  font-size:12px;font-weight:600;color:#243447;display:block;margin-bottom:4px;line-height:1.4;">{summary}{new_badge}</span>'
            f'  <span style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
            f'  font-size:12px;color:#6f8094;font-style:italic;line-height:1.55;">[{_LOREM}]</span>'
            f'</td>'
            f'<td style="padding:10px 0 10px 12px;vertical-align:top;text-align:right;white-space:nowrap;">'
            f'  {assignee_cell}'
            f'</td>'
            f'</tr>'
        )
    if not project_rows:
        project_rows = (
            '<tr><td colspan="3" style="padding:10px;font-family:Helvetica,Arial,sans-serif;'
            'font-size:11px;color:#7d8ea2;">No projects moved this week. Either calm prevailed or the paperwork did.</td></tr>'
        )

    project_summary_html = (
        '<p style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:12px;'
        'color:#56697f;line-height:1.7;margin:0 0 16px;">'
        + escape(
            " · ".join(
                [
                    f"{int(project_movement.get('in_progress_count', len(projects)))} in progress",
                    f"{int(project_movement.get('new_this_week', 0))} new this week",
                    f"{int(project_movement.get('completed_this_week', 0))} completed this week",
                ]
            )
        )
        + "</p>"
    )

    def _aha_date_label(value: object) -> str:
        if not value:
            return "TBD"
        ts = pd.to_datetime(value, errors="coerce")
        if pd.isna(ts):
            return "TBD"
        return pd.Timestamp(ts).strftime("%b %-d, %Y")

    def _aha_progress_pct(value: object) -> int | None:
        if value is None or value == "":
            return None
        pct = pd.to_numeric(value, errors="coerce")
        if pd.isna(pct):
            return None
        return max(0, min(100, int(round(float(pct)))))

    def _aha_status_color(value: object) -> str:
        color = str(value or "").strip()
        if len(color) in {4, 7} and color.startswith("#") and all(
            c in "0123456789abcdefABCDEF" for c in color[1:]
        ):
            return color
        return "#6a7d90"

    def _aha_initiative_row(initiative: dict[str, object]) -> str:
        name = escape(str(initiative.get("name") or "Untitled initiative"))
        reference = escape(str(initiative.get("reference_num") or "").strip())
        url = str(initiative.get("url") or "").strip()
        status = escape(str(initiative.get("status") or "Roadmap").strip())
        status_color = _aha_status_color(initiative.get("status_color"))
        end_date = escape(_aha_date_label(initiative.get("end_date")))
        pct = _aha_progress_pct(initiative.get("progress"))
        pct_label = "TBD" if pct is None else f"{pct}%"
        pct_width = 0 if pct is None else pct
        name_html = (
            f'<a href="{escape(url)}" style="color:#003963;text-decoration:none;">{name}</a>'
            if url.startswith("http")
            else name
        )
        reference_html = (
            '<span style="display:inline-block;margin-left:7px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
            'font-size:9px;font-weight:700;color:#52667b;background:#eef2f6;border:1px solid #d7e0e9;'
            'border-radius:999px;padding:2px 6px;letter-spacing:0.03em;white-space:nowrap;">'
            + reference
            + '</span>'
            if reference
            else ""
        )
        return (
            '<tr>'
            '<td style="padding:11px 12px 11px 0;vertical-align:top;border-top:1px solid #e3e9f0;">'
            '<p style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:12px;font-weight:700;'
            'color:#243447;margin:0 0 5px;line-height:1.35;">'
            + name_html
            + reference_html
            + '</p>'
            '<p style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:10px;color:#65788d;'
            'margin:0;line-height:1.45;">'
            '<span style="display:inline-block;width:7px;height:7px;background:'
            + status_color
            + ';border-radius:999px;margin-right:5px;">&#8203;</span>'
            + status
            + '</p>'
            '</td>'
            '<td style="padding:11px 12px;vertical-align:top;border-top:1px solid #e3e9f0;white-space:nowrap;">'
            '<p style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:9px;font-weight:700;'
            'color:#73859a;text-transform:uppercase;letter-spacing:0.06em;margin:0 0 4px;">Expected End</p>'
            '<p style="font-family:Georgia,\'Times New Roman\',serif;font-size:13px;font-weight:bold;'
            'color:#003963;margin:0;">'
            + end_date
            + '</p>'
            '</td>'
            '<td style="padding:11px 0 11px 12px;vertical-align:top;border-top:1px solid #e3e9f0;width:150px;">'
            '<table cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;">'
            '<tr>'
            '<td style="padding:0 8px 5px 0;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
            'font-size:9px;font-weight:700;color:#73859a;text-transform:uppercase;letter-spacing:0.06em;">Progress</td>'
            '<td style="padding:0 0 5px;text-align:right;font-family:Georgia,\'Times New Roman\',serif;'
            'font-size:13px;font-weight:bold;color:#003963;white-space:nowrap;">'
            + escape(pct_label)
            + '</td>'
            '</tr>'
            '<tr><td colspan="2" bgcolor="#d9e2ec" '
            'style="background:#d9e2ec;height:9px;font-size:1px;line-height:1px;">'
            '<table cellpadding="0" cellspacing="0" border="0" width="'
            + str(pct_width)
            + '%" style="border-collapse:collapse;width:'
            + str(pct_width)
            + '%;"><tr><td bgcolor="#FBB93A" '
            'style="background:#FBB93A;height:9px;font-size:1px;line-height:1px;">&#8203;</td></tr></table>'
            '</td></tr>'
            '</table>'
            '</td>'
            '</tr>'
        )

    def _aha_roadmap_section(roadmap: dict[str, object]) -> str:
        workspaces = roadmap.get("workspaces") or []
        if not workspaces:
            return (
                '<p style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:11px;color:#7d8ea2;'
                'line-height:1.6;margin:0;">No Aha roadmap data available.</p>'
            )
        sections = ""
        for workspace in workspaces:
            workspace_name = escape(str(workspace.get("name") or "Roadmap"))
            goal_blocks = ""
            for goal in workspace.get("goals") or []:
                goal_name = escape(str(goal.get("name") or "Roadmap"))
                initiative_rows = "".join(
                    _aha_initiative_row(initiative)
                    for initiative in goal.get("initiatives") or []
                )
                if not initiative_rows:
                    initiative_rows = (
                        '<tr><td colspan="3" style="padding:10px 0;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
                        'font-size:11px;color:#7d8ea2;border-top:1px solid #e3e9f0;">No initiatives listed.</td></tr>'
                    )
                goal_blocks += (
                    '<div style="margin:0 0 16px;">'
                    '<p style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:11px;font-weight:700;'
                    'color:#52667b;text-transform:uppercase;letter-spacing:0.05em;margin:0 0 6px;">'
                    + goal_name
                    + '</p>'
                    '<table cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;">'
                    + initiative_rows
                    + '</table></div>'
                )
            sections += (
                '<div style="background:#f3efe7;border:1px solid #d5dee8;border-radius:6px;padding:17px 18px;margin:0 0 14px;">'
                '<p style="font-family:Georgia,\'Times New Roman\',serif;font-size:15px;font-weight:bold;'
                'color:#003963;margin:0 0 12px;line-height:1.25;">'
                + workspace_name
                + '</p>'
                + goal_blocks
                + '</div>'
            )
        return sections

    aha_roadmap_html = _aha_roadmap_section(aha_roadmap)

    open_trend_color = _trend_color(
        int(total_open_delta),
        positive="#b54708",
        negative="#027a48",
        neutral="#6b7c93",
    )
    open_trend_label = (
        f"{_signed_count(int(total_open_delta))} vs prior week"
        if "total_open_tickets_prior_week" in snapshot
        else "baseline pending prior week data"
    )

    def _survey_distribution_card(title: str, subtitle: str, counts: dict[str, int], total: int) -> str:
        rows = ""
        for label, count in counts.items():
            pct = (count / total * 100.0) if total else 0.0
            rows += (
                '<tr>'
                '<td style="padding:4px 10px 4px 0;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
                'font-size:11px;color:#243447;white-space:nowrap;">'
                + escape(label)
                + '</td>'
                '<td style="padding:4px 0;width:100%;">'
                '<table cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;">'
                '<tr>'
                '<td style="background:#d9e2ec;border-radius:999px;height:8px;font-size:1px;line-height:1px;">'
                '<div style="background:#003963;height:8px;border-radius:999px;width:'
                + f"{pct:.1f}%"
                + ';"></div>'
                '</td>'
                '</tr>'
                '</table>'
                '</td>'
                '<td style="padding:4px 0 4px 10px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
                'font-size:12px;font-weight:700;color:#5f7186;text-align:right;white-space:nowrap;">'
                + escape(f"{count} ({pct:.0f}%)")
                + '</td>'
                '</tr>'
            )
        if not rows:
            rows = (
                '<tr><td style="padding:6px 0;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
                'font-size:11px;color:#7d8ea2;">No scored responses</td></tr>'
            )
        return (
            '<td style="width:50%;padding:0 6px;vertical-align:top;">'
            '<div style="padding:16px 18px;background:#f3efe7;border:1px solid #d2dbe4;border-radius:6px;">'
            '<p style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:11px;font-weight:700;'
            'color:#52667b;text-transform:uppercase;letter-spacing:0.05em;margin:0 0 8px;">'
            + escape(title)
            + '</p>'
            '<p style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:12px;color:#576a80;'
            'line-height:1.6;margin:0 0 12px;">'
            + escape(subtitle)
            + '</p>'
            '<table cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;">'
            + rows
            + '</table></div></td>'
        )

    survey_chart_html = (
        '<div style="margin:0 0 22px;">'
        '<p style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:11px;font-weight:700;'
        'color:#52667b;text-transform:uppercase;letter-spacing:0.05em;margin:0 0 12px;">'
        'Survey Snapshot</p>'
        '<table cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;margin:0 -6px;">'
        '<tr>'
        + _survey_distribution_card(
            "Past 7 Days",
            f"{survey_satisfaction_total} scored responses in this period",
            survey_satisfaction_counts,
            survey_satisfaction_total,
        )
        + _survey_distribution_card(
            "Past 12 Months",
            (
                f"{survey_trailing_year_total} scored responses in this rolling year"
                + (
                    f" · Data since {survey_trailing_year_effective_start}"
                    if survey_trailing_year_effective_start
                    else ""
                )
            ),
            survey_trailing_year_counts,
            survey_trailing_year_total,
        )
        + '</tr></table></div>'
    )

    comment_rows = ""
    for item in survey_comments:
        raw_completed = item.get("survey_completed_at")
        completed_label = ""
        if raw_completed:
            completed_ts = pd.to_datetime(raw_completed, utc=True, errors="coerce")
            if pd.notna(completed_ts):
                completed_label = completed_ts.strftime("%b %-d")
        meta_parts = [
            part
            for part in [
                completed_label,
                str(item.get("satisfaction_label") or "").strip(),
                _truncate_metadata(
                    str(item.get("ticket_title") or "").strip()
                    or str(item.get("team_name") or "").strip()
                ),
            ]
            if part
        ]
        meta_html = ""
        if meta_parts:
            meta_html = (
                '<p style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:11px;'
                'font-weight:700;color:#5f7186;letter-spacing:0.01em;'
                'margin:0 0 8px;">'
                + escape(" · ".join(meta_parts))
                + "</p>"
            )
        commenter_name = str(item.get("commenter_name") or "").strip()
        signature_html = ""
        if commenter_name:
            signature_html = (
                '<div style="margin-top:12px;padding-top:2px;">'
                '<p style="font-family:Georgia,\'Times New Roman\',serif;font-size:12px;'
                'font-style:italic;color:#003963;margin:0;">'
                + escape(commenter_name)
                + "</p></div>"
            )
        comment_rows += (
            '<tr style="border-bottom:1px solid #edf1f5;"><td style="padding:14px 0;">'
            + meta_html
            + '<p style="font-family:Georgia,\'Times New Roman\',serif;font-size:13px;'
              'line-height:1.7;color:#253446;margin:0;">'
            + escape(str(item.get("comment_text") or ""))
            + "</p>"
            + signature_html
            + "</td></tr>"
        )
    if not comment_rows:
        comment_rows = (
            '<tr><td style="padding:10px 0;font-family:Helvetica,Arial,sans-serif;'
            'font-size:11px;color:#7d8ea2;">No survey comments landed this period. Either smooth sailing or disciplined silence.</td></tr>'
        )

    # ── Shared style tokens ────────────────────────────────────────────────────
    _eyebrow = (
        'font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:11px;'
        'font-weight:700;color:#51657a;text-transform:uppercase;letter-spacing:0.08em;'
        'margin:0 0 6px;'
    )
    _heading = (
        'font-family:Georgia,\'Times New Roman\',serif;font-size:17px;'
        'font-weight:bold;color:#003963;margin:0 0 18px;padding-bottom:10px;'
        'border-bottom:1px solid #d8e0e9;line-height:1.25;'
    )
    _section = (
        'background:#fbfaf7;background-color:#fbfaf7;'
        f'background-image:url({_LIGHT_PANEL_PIXEL_DATA_URI});background-repeat:repeat;'
        'padding:28px 30px;'
    )
    _divider = (
        '<tr><td style="height:1px;background:#cfd8e1;font-size:1px;line-height:1px;">'
        '&#8203;</td></tr>'
    )
    _col_hdr = (
        'style="padding:0 12px 8px;font-family:Helvetica Neue,Helvetica,Arial,sans-serif;'
        'font-size:11px;font-weight:700;color:#52667b;text-transform:uppercase;'
        'letter-spacing:0.05em;text-align:center;border-bottom:2px solid #e4e8ee;"'
    )
    trend_hdr = f'<td {_col_hdr}>8-Wk Trend</td>' if has_spark else ''
    header_burst_row = ""
    if header_burst_img_src:
        header_burst_row = (
            '<tr>'
            '<td style="height:0;padding:0;font-size:0;line-height:0;">'
            '<div align="right" style="height:0;overflow:visible;margin:-72px -14px -44px 0;">'
            '<img src="'
            + escape(header_burst_img_src)
            + '" width="180" height="130" alt="Weekly tagline" '
              'style="display:block;border:0;outline:none;text-decoration:none;">'
            '</div>'
            '</td>'
            '</tr>'
        )
    datatype_font_links = ""
    if datatype_sparklines:
        datatype_font_links = (
            '<link rel="preconnect" href="https://fonts.googleapis.com" />'
            '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />'
            '<link rel="stylesheet" '
            'href="https://fonts.googleapis.com/css2?family=Datatype&amp;display=swap" />'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta http-equiv="X-UA-Compatible" content="IE=edge" />
  <meta name="color-scheme" content="light" />
  <meta name="supported-color-schemes" content="light" />
  {datatype_font_links}
  <title>CFO Update &mdash; IT</title>
</head>
<body bgcolor="#dbe2ea" style="margin:0;padding:0;background:#dbe2ea;background-color:#dbe2ea;font-family:Georgia,'Times New Roman',serif;">

<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#dbe2ea" style="background:#dbe2ea;background-color:#dbe2ea;">
  <tr>
    <td align="center" style="padding:28px 16px;">

      <table width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="#fbfaf7" background="{_LIGHT_PANEL_PIXEL_DATA_URI}"
             style="max-width:600px;border-radius:6px;overflow:hidden;background:#fbfaf7;background-color:#fbfaf7;
                    background-image:url('{_LIGHT_PANEL_PIXEL_DATA_URI}');background-repeat:repeat;
                    box-shadow:0 8px 22px rgba(31,57,83,0.08);">

        <!-- HEADER -->
        <tr>
          <td bgcolor="#003963" style="background:#003963;background-color:#003963;padding:28px 30px 22px;">
            <p style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:10px;
                      font-weight:700;color:#f5c96a;
                      letter-spacing:0.08em;margin:0 0 8px 0;">
              Cedarville University &nbsp;&middot;&nbsp; Information Technology
            </p>
            <h1 style="font-family:Georgia,'Times New Roman',serif;font-size:24px;
                       font-weight:bold;color:#ffffff;margin:0 0 10px 0;line-height:1.15;">
              CFO Update
            </h1>
            <table cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="font-family:Georgia,'Times New Roman',serif;font-style:italic;
                           font-size:14px;color:#FBB93A;padding-right:16px;">
                  {period_label}
                </td>
                <td style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:11px;
                           color:#d0deea;">
                  Generated {generated_at}
                </td>
              </tr>
            </table>
          </td>
        </tr>
        {header_burst_row}

        <!-- GOLD RULE -->
        <tr>
          <td bgcolor="#FBB93A" style="height:4px;background:#FBB93A;background:linear-gradient(to right,#FBB93A,#F59536 40%,transparent);
                     font-size:1px;line-height:1px;">&#8203;</td>
        </tr>

        <!-- DISCUSSION -->
        <tr>
          <td bgcolor="#fbfaf7" background="{_LIGHT_PANEL_PIXEL_DATA_URI}" style="{_section}">
            <p style="{_eyebrow}">Leadership Notes</p>
            <p style="{_heading}">Discussion</p>
            <ul style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:12px;
                       color:#2c3a4a;line-height:1.7;margin:0;padding-left:18px;">
              {_discussion_rows(discussion_items)}
            </ul>
          </td>
        </tr>

        {_divider}

        <!-- TICKET VOLUME -->
        <tr>
          <td bgcolor="#fbfaf7" background="{_LIGHT_PANEL_PIXEL_DATA_URI}" style="{_section}">
            <p style="{_eyebrow}">Activity</p>
            <p style="{_heading}">Ticket Volume &mdash; {week_range}</p>
            <table cellpadding="0" cellspacing="0" width="100%"
                   style="border-collapse:collapse;">
              <tr>
                <td width="80"></td>
                <td {_col_hdr}>Last 7 Days</td>
                <td {_col_hdr}>Prior 7 Days</td>
                <td {_col_hdr}>Year Ago</td>
                {trend_hdr}
              </tr>
              {created_row}
              {closed_row}
            </table>
          </td>
        </tr>

        {_divider}

        <!-- OPEN TICKETS KPI -->
        <tr>
          <td bgcolor="#fbfaf7" background="{_LIGHT_PANEL_PIXEL_DATA_URI}" style="{_section}">
            <p style="{_eyebrow}">Queue Depth</p>
            <p style="{_heading}">Open Tickets</p>
            <table cellpadding="0" cellspacing="0" border="0">
              <tr>
                <td style="padding-right:20px;vertical-align:middle;">
                  <span style="font-family:Georgia,'Times New Roman',serif;font-size:48px;
                               font-weight:bold;color:#003963;line-height:1;">
                    {escape(str(total_open))}
                  </span>
                </td>
                <td style="vertical-align:middle;padding-right:20px;">
                  <span style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:12px;
                               color:#586b81;display:block;line-height:1.6;">
                    total open as of {escape(snapshot.get("as_of_label", ""))}
                  </span>
                  <span style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:11px;
                               font-weight:700;color:{open_trend_color};display:block;line-height:1.6;">
                    {escape(open_trend_label)}
                  </span>
                </td>
                {'<td style="vertical-align:bottom;">' + open_spark + '</td>' if open_spark else ''}
              </tr>
            </table>
          </td>
        </tr>

        {_divider}

        <!-- AHA ROADMAP -->
        <tr>
          <td bgcolor="#fbfaf7" background="{_LIGHT_PANEL_PIXEL_DATA_URI}" style="{_section}">
            <p style="{_eyebrow}">Strategic Technology</p>
            <p style="{_heading}">Aha Roadmap</p>
            {aha_roadmap_html}
          </td>
        </tr>

        {_divider}

        <!-- SURVEY COMMENTS -->
        <tr>
          <td bgcolor="#fbfaf7" background="{_LIGHT_PANEL_PIXEL_DATA_URI}" style="{_section}">
            <p style="{_eyebrow}">Voice of Customer</p>
            <p style="{_heading}">Survey Comments</p>
            {survey_chart_html}
            <table cellpadding="0" cellspacing="0" width="100%"
                   style="border-collapse:collapse;">
              {comment_rows}
            </table>
          </td>
        </tr>

        {_divider}

        <!-- PROJECTS -->
        <tr>
          <td bgcolor="#fbfaf7" background="{_LIGHT_PANEL_PIXEL_DATA_URI}" style="{_section}">
            <p style="{_eyebrow}">Operational Projects</p>
            {project_summary_html}
            <table cellpadding="0" cellspacing="0" width="100%"
                   style="border-collapse:collapse;">
              {project_rows}
            </table>
          </td>
        </tr>

        <!-- FOOTER -->
        <tr>
          <td bgcolor="#002a4a" style="background:#002a4a;background-color:#002a4a;padding:16px 30px;">
            <p style="font-family:Helvetica Neue,Helvetica,Arial,sans-serif;font-size:10px;
                      font-weight:600;color:#cbd8e5;
                      letter-spacing:0.04em;margin:0;">
              Cedarville University Information Technology &nbsp;&middot;&nbsp; Confidential
            </p>
          </td>
        </tr>

      </table>
    </td>
  </tr>
</table>

</body>
</html>
"""


def write_cfo_email(
    snapshot: dict,
    output_path: Path,
    *,
    header_burst_img_src: str | None = None,
    datatype_sparklines: bool = False,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_cfo_email_html(
            snapshot,
            header_burst_img_src=header_burst_img_src,
            datatype_sparklines=datatype_sparklines,
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
