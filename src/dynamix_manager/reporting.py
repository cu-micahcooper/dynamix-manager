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
    columns = [c for c in _SURVEY_JSON_COLUMNS if c in frame.columns]
    return frame[columns].to_json(orient="records", date_format="iso")


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


def render_survey_health_html(frame: pd.DataFrame) -> str:
    summary = summarize_survey_health(frame)
    total_responses = summary["total_responses"]
    linked_responses = summary["linked_responses"]
    comment_count = summary["comment_count"]
    satisfaction_counts = summary["satisfaction_counts"]
    team_counts = summary["top_teams"]
    average_score = summary["average_score"]
    negative_response_rate = summary["negative_response_rate"]
    recent_comments = summary["recent_comments"]

    satisfaction_items = "".join(
        f'<li class="flex items-center justify-between border-b border-stone-200 py-2"><span>{escape(label)}</span><span class="font-semibold">{count}</span></li>'
        for label, count in satisfaction_counts
    ) or '<li class="py-2 text-stone-500">No satisfaction labels found.</li>'

    team_items = "".join(
        f'<li class="flex items-center justify-between border-b border-stone-200 py-2"><span>{escape(label)}</span><span class="font-semibold">{count}</span></li>'
        for label, count in team_counts
    ) or '<li class="py-2 text-stone-500">No linked team data found.</li>'

    recent_comment_items = "".join(
        f'<li class="border-b border-stone-200 py-3"><p class="font-medium text-stone-800">{escape(str(item.get("satisfaction_label") or "Unrated"))}</p><p class="text-sm text-stone-500">{escape(str(item.get("team_name") or "Unassigned team"))}</p><p class="mt-2 text-sm text-stone-700">{escape(str(item.get("comment_text") or ""))}</p></li>'
        for item in recent_comments
    ) or '<li class="py-2 text-stone-500">No recent comments found.</li>'

    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Survey Health</title>
    <script src="https://cdn.tailwindcss.com"></script>
  </head>
  <body class="min-h-screen bg-stone-100 text-stone-900">
    <main class="mx-auto max-w-6xl px-6 py-10">
      <section class="mb-8">
        <p class="text-sm uppercase tracking-[0.3em] text-stone-500">TeamDynamix Analytics</p>
        <h1 class="mt-2 text-4xl font-semibold">Survey Health</h1>
        <p class="mt-3 max-w-2xl text-stone-600">Ticket-linked survey pulse with a compact executive summary and high-signal breakdowns.</p>
      </section>
      <section class="grid gap-4 md:grid-cols-3">
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <p class="text-sm text-stone-500">Responses</p>
          <p class="mt-3 text-3xl font-semibold">{total_responses}</p>
        </article>
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <p class="text-sm text-stone-500">Ticket-linked</p>
          <p class="mt-3 text-3xl font-semibold">{linked_responses}</p>
        </article>
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <p class="text-sm text-stone-500">Comments</p>
          <p class="mt-3 text-3xl font-semibold">{comment_count}</p>
        </article>
      </section>
      <section class="mt-8 grid gap-4 md:grid-cols-2">
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <p class="text-sm text-stone-500">Average score</p>
          <p class="mt-3 text-3xl font-semibold">{average_score if average_score is not None else "N/A"}</p>
        </article>
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <p class="text-sm text-stone-500">Negative response rate</p>
          <p class="mt-3 text-3xl font-semibold">{negative_response_rate:.1%}</p>
        </article>
      </section>
      <section class="mt-8 grid gap-6 lg:grid-cols-2">
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Top Satisfaction Labels</h2>
          <ul class="mt-4">{satisfaction_items}</ul>
        </article>
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Top Linked Teams</h2>
          <ul class="mt-4">{team_items}</ul>
        </article>
      </section>
      <section class="mt-8">
        <article class="rounded-2xl bg-white p-6 shadow-sm">
          <h2 class="text-lg font-semibold">Recent Comments</h2>
          <ul class="mt-4">{recent_comment_items}</ul>
        </article>
      </section>
    </main>
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
    stale_rows = _ticket_quality_rows(
        summary["stale_open_tickets"][:10],
        [
            ("ticket_id", "Ticket"),
            ("ticket_title", "Title"),
            ("team_name", "Team"),
            ("stale_business_days", "Business Days"),
        ],
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
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_ticket_health_html(
            frame,
            days_off=days_off,
            quality_flags=quality_flags,
            interactions=interactions,
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
