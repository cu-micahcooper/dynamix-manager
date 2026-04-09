from __future__ import annotations

from typing import Any

import pandas as pd
import requests

from dynamix_manager.models import build_ticket_linked_survey_model
from dynamix_manager.notebooks import (
    write_survey_health_notebook,
    write_ticket_quality_notebook,
)
from dynamix_manager.planned_days_off import PLANNED_DAYS_OFF
from dynamix_manager.config import RuntimeConfig, survey_report_id
from dynamix_manager.cfo import summarize_cfo_snapshot
from dynamix_manager.executive import summarize_executive_snapshot
from dynamix_manager.reporting import (
    write_survey_health_report,
    write_ticket_health_report,
    write_ticket_quality_report,
    write_executive_email,
    write_executive_report,
    write_cfo_email,
)
from dynamix_manager.gmail import create_draft, credentials_available
from dynamix_manager.youtrack import fetch_youtrack_inprogress_projects
from dynamix_manager.storage import read_table, replace_table, table_exists
from dynamix_manager.ticket_quality import (
    build_ticket_quality_flags,
    normalize_ticket_quality_feed_rows,
    normalize_ticket_quality_ticket_rows,
)
from dynamix_manager.tickets import build_ticket_search_filters, normalize_ticket_rows
from dynamix_manager.surveys import normalize_survey_rows
from dynamix_manager.tdx_client import TeamDynamixClient

INFOTECH_TICKETS_APP_NAME = "InfoTech Tickets"


def _write_survey_rows(config: RuntimeConfig, rows: list[dict]) -> pd.DataFrame:
    survey_responses = normalize_survey_rows(rows)
    replace_table(config.db_path, "survey_responses", survey_responses)
    return survey_responses


def _write_days_off_rows(config: RuntimeConfig, rows: list[dict]) -> pd.DataFrame:
    live_days_off = pd.DataFrame(
        [
            {
                "day_off_id": row.get("ID"),
                "name": row.get("Name"),
                "holiday_date": (
                    pd.to_datetime(row.get("Date"), utc=True, errors="coerce").strftime("%Y-%m-%d")
                    if row.get("Date")
                    else None
                ),
            }
            for row in rows
        ]
    )
    planned_days_off = pd.DataFrame(PLANNED_DAYS_OFF)
    days_off = pd.concat([live_days_off, planned_days_off], ignore_index=True)
    if not days_off.empty:
        days_off = days_off.dropna(subset=["holiday_date"])
        days_off = days_off.sort_values(["holiday_date", "name", "day_off_id"]).drop_duplicates(
            subset=["holiday_date"],
            keep="first",
        )
        days_off = days_off.reset_index(drop=True)
    replace_table(config.db_path, "days_off", days_off)
    return days_off


def _survey_ticket_ids(rows: list[dict]) -> list[int]:
    return sorted({row["TicketID"] for row in rows if row.get("TicketID") is not None})


def _artifact_root(config: RuntimeConfig):
    db_parent = config.db_path.parent
    if db_parent.name == "data":
        return db_parent.parent
    return db_parent


def _open_tickets_only(tickets: pd.DataFrame) -> pd.DataFrame:
    frame = tickets.copy()
    if "status_class" in frame.columns:
        sc = pd.to_numeric(frame["status_class"], errors="coerce")
        known_closed = sc.isin({3, 4})
        if "resolved_at" in frame.columns:
            resolved = pd.to_datetime(frame["resolved_at"], utc=True, errors="coerce")
            frame = frame.loc[~(known_closed | (sc.isna() & resolved.notna()))]
        else:
            frame = frame.loc[~known_closed]
    elif "resolved_at" in frame.columns:
        resolved = pd.to_datetime(frame["resolved_at"], utc=True, errors="coerce")
        frame = frame.loc[resolved.isna()]
    return frame


def _discover_ticket_app_from_rows(
    client: TeamDynamixClient,
    token: str,
    rows: list[dict],
) -> dict[str, Any]:
    sample_ticket_id = next(row["TicketID"] for row in rows if row.get("TicketID") is not None)
    applications = client.list_ticketing_applications(client.fetch_applications(token))
    preferred = next((app for app in applications if app.get("Name") == INFOTECH_TICKETS_APP_NAME), None)
    if preferred is not None:
        return preferred
    for application in applications:
        app_id = application["AppID"]
        try:
            ticket = client.get_ticket(sample_ticket_id, token, app_id)
        except Exception:
            continue
        if ticket.get("ID") == sample_ticket_id:
            return application
    raise RuntimeError("Unable to determine ticket application for survey report")


def _cache_ticket_context_from_rows(
    config: RuntimeConfig,
    client: TeamDynamixClient,
    token: str,
    ticket_app_id: int,
    ticket_app_name: str | None,
    survey_rows: list[dict],
    ticket_ids: list[int] | None = None,
    limit: int | None = None,
    existing_tickets: pd.DataFrame | None = None,
    max_attempts: int = 1,
) -> tuple[pd.DataFrame, list[int]]:
    if ticket_ids is None:
        ticket_ids = _survey_ticket_ids(survey_rows)
    if existing_tickets is None and table_exists(config.db_path, "tickets"):
        existing_tickets = read_table(config.db_path, "tickets")
    existing_ticket_ids = set()
    if existing_tickets is not None and not existing_tickets.empty:
        existing_ticket_ids = set(existing_tickets["ticket_id"].tolist())
    pending_ticket_ids = [ticket_id for ticket_id in ticket_ids if ticket_id not in existing_ticket_ids]
    if limit is not None:
        batch_ticket_ids = pending_ticket_ids[:limit]
    else:
        batch_ticket_ids = pending_ticket_ids
    tickets = []
    fetched_count = 0
    for ticket_id in batch_ticket_ids:
        try:
            tickets.append(
                client.get_ticket(
                    ticket_id,
                    token,
                    ticket_app_id,
                    max_attempts=max_attempts,
                )
            )
            fetched_count += 1
        except requests.HTTPError as exc:
            status_code = getattr(exc.response, "status_code", None)
            if status_code == 429:
                break
            if status_code == 404:
                continue
            raise
    ticket_frame = normalize_ticket_rows(tickets)
    if not ticket_frame.empty:
        ticket_frame["ticket_app_id"] = ticket_app_id
        ticket_frame["ticket_app_name"] = ticket_app_name
    if existing_tickets is not None:
        if ticket_frame.empty:
            ticket_frame = existing_tickets
        else:
            ticket_frame = pd.concat([existing_tickets, ticket_frame], ignore_index=True)
            ticket_frame = ticket_frame.drop_duplicates(subset=["ticket_id"], keep="first")
    replace_table(config.db_path, "tickets", ticket_frame)
    return ticket_frame, pending_ticket_ids[fetched_count:]


def cache_survey_report(
    config: RuntimeConfig,
    client: TeamDynamixClient,
    report_id: int,
):
    token = client.authenticate()
    rows = client.fetch_report(report_id, token, with_data=True)
    return _write_survey_rows(config, rows)


def cache_days_off(
    config: RuntimeConfig,
    client: TeamDynamixClient,
):
    token = client.authenticate()
    rows = client.fetch_days_off(token)
    days_off = _write_days_off_rows(config, rows)
    return {"rows_written": len(days_off)}


def discover_ticket_app(
    config: RuntimeConfig,
    client: TeamDynamixClient,
    report_id: int,
) -> dict[str, Any]:
    token = client.authenticate()
    rows = client.fetch_report(report_id, token, with_data=True)
    return _discover_ticket_app_from_rows(client, token, rows)


def cache_ticket_context(
    config: RuntimeConfig,
    client: TeamDynamixClient,
    ticket_app_id: int,
    ticket_app_name: str | None = None,
    report_id: int = 100482,
    limit: int | None = None,
):
    token = client.authenticate()
    survey_rows = client.fetch_report(report_id, token, with_data=True)
    ticket_frame, _ = _cache_ticket_context_from_rows(
        config=config,
        client=client,
        token=token,
        ticket_app_id=ticket_app_id,
        ticket_app_name=ticket_app_name,
        survey_rows=survey_rows,
        ticket_ids=None,
        limit=limit,
        max_attempts=1,
    )
    return ticket_frame


def cache_ticket_quality_slice(
    config: RuntimeConfig,
    client: TeamDynamixClient,
    ticket_app_id: int,
    limit: int | None = None,
):
    all_tickets = read_table(config.db_path, "tickets")
    source_tickets = _open_tickets_only(all_tickets)
    days_off = read_table(config.db_path, "days_off") if table_exists(config.db_path, "days_off") else pd.DataFrame(columns=["holiday_date"])
    if "modified_at" in source_tickets.columns:
        source_tickets = source_tickets.sort_values("modified_at", ascending=False)
    if limit is not None:
        source_tickets = source_tickets.head(limit)

    token = client.authenticate()
    ticket_rows = []
    interaction_frames = []

    for ticket_id in source_tickets["ticket_id"].tolist():
        ticket_detail = client.get_ticket(int(ticket_id), token, ticket_app_id, max_attempts=1)
        ticket_rows.append(ticket_detail)
        feed_entries = client.get_ticket_feed(int(ticket_id), token, ticket_app_id)
        detailed_feed_entries = {}
        for entry in feed_entries:
            if entry.get("RepliesCount") and not (entry.get("Replies") or []):
                uri = entry.get("Uri")
                if uri:
                    detailed_feed_entries[entry["ID"]] = client.get_feed_item(uri, token)
        interaction_frames.append(
            normalize_ticket_quality_feed_rows(
                ticket_id=int(ticket_id),
                requestor_uid=ticket_detail.get("RequestorUid"),
                requestor_name=ticket_detail.get("RequestorName"),
                feed_entries=feed_entries,
                detailed_feed_entries=detailed_feed_entries,
            )
        )

    quality_tickets = normalize_ticket_quality_ticket_rows(ticket_rows)
    quality_tickets = _open_tickets_only(quality_tickets)
    open_ticket_ids = set(quality_tickets["ticket_id"].tolist())
    interactions = (
        pd.concat(interaction_frames, ignore_index=True)
        if interaction_frames
        else pd.DataFrame(
            columns=[
                "ticket_id",
                "interaction_id",
                "parent_interaction_id",
                "created_at",
                "created_uid",
                "created_full_name",
                "body",
                "is_private",
                "is_communication",
                "update_type",
                "actor_type",
                "interaction_source",
            ]
        )
    )
    if not interactions.empty:
        interactions = interactions.loc[interactions["ticket_id"].isin(open_ticket_ids)].reset_index(drop=True)
    flags = build_ticket_quality_flags(quality_tickets, interactions, days_off=days_off)
    replace_table(config.db_path, "ticket_quality_tickets", quality_tickets)
    replace_table(config.db_path, "ticket_quality_interactions", interactions)
    replace_table(config.db_path, "ticket_quality_flags", flags)

    artifact_root = _artifact_root(config)
    report_path = artifact_root / "reports" / "ticket_quality.html"
    notebook_path = artifact_root / "notebooks" / "ticket_quality.ipynb"
    health_report_path = artifact_root / "reports" / "ticket_health.html"
    write_ticket_quality_report(flags, report_path)
    write_ticket_quality_notebook(config.db_path, notebook_path)
    write_ticket_health_report(
        all_tickets,
        health_report_path,
        days_off=days_off,
        quality_flags=flags,
        interactions=interactions,
        tdx_base_url=config.base_url,
    )

    return {
        "ticket_rows": len(quality_tickets),
        "interaction_rows": len(interactions),
        "flag_rows": len(flags),
        "report_written": int(report_path.exists()),
        "notebook_written": int(notebook_path.exists()),
        "ticket_health_report_written": int(health_report_path.exists()),
    }


def materialize_ticket_linked_surveys(config: RuntimeConfig):
    surveys = read_table(config.db_path, "survey_responses")
    tickets = read_table(config.db_path, "tickets")
    model = build_ticket_linked_survey_model(surveys, tickets)
    replace_table(config.db_path, "ticket_linked_surveys", model)
    return model


def sync_tickets(
    config: RuntimeConfig,
    client: TeamDynamixClient,
    ticket_app_id: int,
) -> dict[str, int]:
    """Bulk-sync all tickets modified since the last known date via the search API."""
    modified_from: str | None = None
    existing = pd.DataFrame()
    if table_exists(config.db_path, "tickets"):
        existing = read_table(config.db_path, "tickets")
        if not existing.empty and "modified_at" in existing.columns:
            max_mod = pd.to_datetime(existing["modified_at"], utc=True, errors="coerce").dropna().max()
            if pd.notna(max_mod):
                modified_from = max_mod.isoformat()

    token = client.authenticate()
    payload = build_ticket_search_filters(modified_from=modified_from)
    rows = client.search_tickets(token, payload, ticket_app_id=ticket_app_id)
    new_frame = normalize_ticket_rows(rows)

    if not new_frame.empty:
        new_frame["ticket_app_id"] = ticket_app_id

    if existing.empty:
        combined = new_frame
    elif new_frame.empty:
        combined = existing
    else:
        combined = pd.concat([existing, new_frame], ignore_index=True)
        combined = combined.drop_duplicates(subset=["ticket_id"], keep="last")

    replace_table(config.db_path, "tickets", combined)
    return {"synced_tickets": len(new_frame)}


def generate_executive_report(
    config: RuntimeConfig,
    client: TeamDynamixClient | None = None,
) -> dict[str, object]:
    if client is not None:
        cache_survey_report(config=config, client=client, report_id=survey_report_id())
        ticket_app = discover_ticket_app(config=config, client=client, report_id=survey_report_id())
        sync_tickets(config=config, client=client, ticket_app_id=int(ticket_app["AppID"]))

    tickets = read_table(config.db_path, "tickets") if table_exists(config.db_path, "tickets") else pd.DataFrame()
    surveys = (
        read_table(config.db_path, "survey_responses")
        if table_exists(config.db_path, "survey_responses")
        else pd.DataFrame()
    )
    days_off = (
        read_table(config.db_path, "days_off")
        if table_exists(config.db_path, "days_off")
        else pd.DataFrame(columns=["holiday_date"])
    )

    snapshot = summarize_executive_snapshot(
        tickets, surveys, days_off=days_off, tdx_base_url=config.base_url
    )

    artifact_root = _artifact_root(config)
    report_path = artifact_root / "reports" / "executive_report.html"
    write_executive_report(snapshot, report_path)

    return {
        "report_written": int(report_path.exists()),
        "new_tickets_this_week": snapshot["new_tickets_this_week"],
        "avg_weekly_tickets": snapshot["avg_weekly_tickets_created"],
        "stale_open_count": snapshot["stale_open_count"],
    }


def generate_executive_email(
    config: RuntimeConfig,
) -> dict[str, object]:
    """Render the executive email HTML from cached data (no live sync)."""
    tickets = read_table(config.db_path, "tickets") if table_exists(config.db_path, "tickets") else pd.DataFrame()
    surveys = (
        read_table(config.db_path, "survey_responses")
        if table_exists(config.db_path, "survey_responses")
        else pd.DataFrame()
    )
    days_off = (
        read_table(config.db_path, "days_off")
        if table_exists(config.db_path, "days_off")
        else pd.DataFrame(columns=["holiday_date"])
    )

    snapshot = summarize_executive_snapshot(
        tickets, surveys, days_off=days_off, tdx_base_url=config.base_url
    )

    artifact_root = _artifact_root(config)
    email_path = artifact_root / "reports" / "executive_email.html"
    write_executive_email(snapshot, email_path)

    return {
        "email_written": int(email_path.exists()),
        "email_path": str(email_path),
        "new_tickets_this_week": snapshot["new_tickets_this_week"],
        "stale_open_count": snapshot["stale_open_count"],
    }


def generate_cfo_email(
    config: RuntimeConfig,
) -> dict[str, object]:
    """Render the CFO Update email from cached TDX data + live YouTrack projects."""
    tickets = read_table(config.db_path, "tickets") if table_exists(config.db_path, "tickets") else pd.DataFrame()
    surveys = (
        read_table(config.db_path, "survey_responses")
        if table_exists(config.db_path, "survey_responses")
        else pd.DataFrame()
    )

    youtrack_projects: list[dict] = []
    if config.youtrack_base and config.youtrack_token:
        try:
            youtrack_projects = fetch_youtrack_inprogress_projects(
                config.youtrack_base, config.youtrack_token,
                board_id=config.youtrack_board_id,
            )
        except Exception:
            youtrack_projects = []

    snapshot = summarize_cfo_snapshot(tickets, surveys, youtrack_projects=youtrack_projects)

    artifact_root = _artifact_root(config)
    email_path = artifact_root / "reports" / "cfo_email.html"
    write_cfo_email(snapshot, email_path)

    draft_id = None
    if credentials_available(config.gmail_token_path):
        try:
            week_label = snapshot.get("week_label", "")
            subject = f"CFO Update \u2013 IT | {week_label}"
            draft = create_draft(
                subject=subject,
                html_body=email_path.read_text(),
                to=config.gmail_draft_to or "",
                token_path_override=config.gmail_token_path,
            )
            draft_id = draft.get("id")
        except Exception as exc:
            print(f"[gmail] Draft creation skipped: {exc}")

    return {
        "email_written": int(email_path.exists()),
        "email_path": str(email_path),
        "tickets_created_this_week": snapshot["tickets_created_this_week"],
        "tickets_closed_this_week": snapshot["tickets_closed_this_week"],
        "total_open_tickets": snapshot["total_open_tickets"],
        "youtrack_project_count": len(youtrack_projects),
        "gmail_draft_id": draft_id,
    }


def refresh_survey_slice(
    config: RuntimeConfig,
    client: TeamDynamixClient,
    report_id: int,
    ticket_limit: int | None = None,
) -> dict[str, int]:
    survey_frame = cache_survey_report(config=config, client=client, report_id=report_id)
    ticket_app = discover_ticket_app(config=config, client=client, report_id=report_id)
    sync_result = sync_tickets(
        config=config,
        client=client,
        ticket_app_id=int(ticket_app["AppID"]),
    )
    model = materialize_ticket_linked_surveys(config)
    write_survey_health_report(model, config.report_output_path)
    notebook_path = write_survey_health_notebook(
        db_path=config.db_path,
        output_path=config.notebook_output_path,
    )
    return {
        "survey_rows": len(survey_frame),
        "ticket_app_id": int(ticket_app["AppID"]),
        "ticket_rows": sync_result["synced_tickets"],
        "linked_rows": int(model["ticket_linked"].sum()),
        "notebook_written": int(notebook_path.exists()),
    }


def backfill_ticket_links(
    config: RuntimeConfig,
    client: TeamDynamixClient,
    report_id: int,
    batch_size: int = 50,
    max_batches: int = 5,
) -> dict[str, int]:
    token = client.authenticate()
    survey_rows = client.fetch_report(report_id, token, with_data=True)
    survey_frame = _write_survey_rows(config, survey_rows)
    ticket_app = _discover_ticket_app_from_rows(client, token, survey_rows)
    ticket_ids = _survey_ticket_ids(survey_rows)

    ticket_frame, remaining_ticket_ids = _cache_ticket_context_from_rows(
        config=config,
        client=client,
        token=token,
        ticket_app_id=ticket_app["AppID"],
        ticket_app_name=ticket_app.get("Name"),
        survey_rows=survey_rows,
        ticket_ids=ticket_ids,
        limit=batch_size,
        max_attempts=1,
    )
    batches_run = 1
    previous_ticket_rows = len(ticket_frame)

    while batches_run < max_batches and remaining_ticket_ids:
        ticket_frame, remaining_ticket_ids = _cache_ticket_context_from_rows(
            config=config,
            client=client,
            token=token,
            ticket_app_id=ticket_app["AppID"],
            ticket_app_name=ticket_app.get("Name"),
            survey_rows=survey_rows,
            ticket_ids=remaining_ticket_ids,
            existing_tickets=ticket_frame,
            limit=batch_size,
            max_attempts=1,
        )
        batches_run += 1
        if len(ticket_frame) <= previous_ticket_rows:
            break
        previous_ticket_rows = len(ticket_frame)

    model = materialize_ticket_linked_surveys(config)
    write_survey_health_report(model, config.report_output_path)
    notebook_path = write_survey_health_notebook(
        db_path=config.db_path,
        output_path=config.notebook_output_path,
    )
    summary = {
        "survey_rows": len(survey_frame),
        "ticket_app_id": int(ticket_app["AppID"]),
        "ticket_rows": len(ticket_frame),
        "linked_rows": int(model["ticket_linked"].sum()),
        "notebook_written": int(notebook_path.exists()),
    }
    summary["batches_run"] = batches_run
    return summary
