"""Backfill all missing tickets using bulk monthly search instead of one-by-one GETs.

Saves incrementally and retries on timeout/429 so progress is never lost.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import duckdb
import pandas as pd
import requests as req_lib

from dynamix_manager.config import load_runtime_config, survey_report_id
from dynamix_manager.pipeline import materialize_ticket_linked_surveys
from dynamix_manager.reporting import write_survey_health_report
from dynamix_manager.storage import read_table, replace_table
from dynamix_manager.tdx_client import TeamDynamixClient, build_auth_headers
from dynamix_manager.tickets import normalize_ticket_rows

TICKET_APP_ID = 634
MAX_RESULTS = 500
DELAY_BETWEEN_SEARCHES = 1.1  # stay safely under 60/min rate limit
SAVE_EVERY_MONTHS = 6         # flush buffer to DB every N months


def missing_ticket_ids(db_path) -> set[int]:
    conn = duckdb.connect(str(db_path), read_only=True)
    rows = conn.execute("""
        select distinct s.ticket_id
        from survey_responses s
        left join tickets t on s.ticket_id = t.ticket_id
        where s.ticket_id is not null and t.ticket_id is null
    """).fetchdf()
    conn.close()
    return {int(x) for x in rows["ticket_id"].tolist()}


def search_with_retry(client, token, url, payload, max_attempts=4):
    """POST search with retry on 429, ReadTimeout, and connection errors."""
    for attempt in range(max_attempts):
        try:
            resp = client.session.post(
                url, json=payload,
                headers=build_auth_headers(token, str(TICKET_APP_ID)),
                timeout=90,
            )
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", 61))
                print(f"  429 — sleeping {int(wait)}s then retrying")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except (req_lib.ReadTimeout, req_lib.ConnectionError, req_lib.exceptions.ChunkedEncodingError) as exc:
            if attempt + 1 < max_attempts:
                print(f"  {type(exc).__name__} — sleeping 15s then retrying (attempt {attempt+1})")
                time.sleep(15)
            else:
                raise
    raise RuntimeError("Max attempts exceeded")


def search_tickets_in_range(client, token, date_from, date_to):
    url = f"{client.base_url}/api/{TICKET_APP_ID}/tickets/search"
    payload = {
        "MaxResults": MAX_RESULTS,
        "CreatedDateFrom": date_from.isoformat(),
        "CreatedDateTo": date_to.isoformat(),
    }
    return search_with_retry(client, token, url, payload)


def month_ranges(start: date, end: date):
    current = start.replace(day=1)
    while current <= end:
        next_month = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield current, min(next_month - timedelta(days=1), end)
        current = next_month


def week_ranges(month_start: date, month_end: date):
    current = month_start
    while current <= month_end:
        week_end = min(current + timedelta(days=6), month_end)
        yield current, week_end
        current = week_end + timedelta(days=1)


def flush_to_db(config, buffer, found_ids):
    """Save collected ticket rows to DB and clear buffer."""
    if not buffer:
        return
    new_frame = normalize_ticket_rows(buffer)
    new_frame["ticket_app_id"] = TICKET_APP_ID
    new_frame["ticket_app_name"] = "InfoTech Tickets"
    existing = read_table(config.db_path, "tickets")
    if existing is not None and not existing.empty:
        combined = pd.concat([existing, new_frame], ignore_index=True)
        combined = combined.drop_duplicates(subset=["ticket_id"], keep="first")
    else:
        combined = new_frame
    replace_table(config.db_path, "tickets", combined)
    buffer.clear()


def main() -> None:
    config = load_runtime_config()
    client = TeamDynamixClient(
        base_url=config.base_url,
        app_id=config.app_id,
        username=config.username,
        password=config.password,
    )
    token = client.authenticate()

    missing = missing_ticket_ids(config.db_path)
    total_missing = len(missing)
    print(f"Missing tickets to find: {total_missing}")
    if not missing:
        print("Nothing to do.")
        return

    collected: list[dict] = []
    found_ids: set[int] = set()

    search_start = date(2023, 7, 1)
    search_end = date(2026, 12, 31)
    ranges = list(month_ranges(search_start, search_end))
    print(f"Searching {len(ranges)} monthly ranges ...")

    for i, (d_from, d_to) in enumerate(ranges):
        # Re-authenticate every 30 months to avoid token expiry
        if i > 0 and i % 30 == 0:
            print(f"  Refreshing auth token at month {i+1} ...")
            token = client.authenticate()

        rows = search_tickets_in_range(client, token, d_from, d_to)
        matched = [r for r in rows if r.get("ID") in missing and r.get("ID") not in found_ids]
        if matched:
            collected.extend(matched)
            found_ids.update(r["ID"] for r in matched)

        # If we hit MaxResults, search weekly sub-ranges to catch any missed tickets
        if len(rows) == MAX_RESULTS:
            for w_from, w_to in week_ranges(d_from, d_to):
                week_rows = search_tickets_in_range(client, token, w_from, w_to)
                wmatched = [r for r in week_rows if r.get("ID") in missing and r.get("ID") not in found_ids]
                if wmatched:
                    collected.extend(wmatched)
                    found_ids.update(r["ID"] for r in wmatched)
                time.sleep(DELAY_BETWEEN_SEARCHES)

        if matched or (i % 12 == 0):
            print(f"  [{i+1}/{len(ranges)}] {d_from} – {d_to}: {len(rows)} tickets, {len(matched)} matched. Total found: {len(found_ids)}/{total_missing}")

        # Save incrementally to avoid losing progress on crash
        if (i + 1) % SAVE_EVERY_MONTHS == 0 and collected:
            flush_to_db(config, collected, found_ids)
            print(f"  Saved checkpoint: {len(found_ids)} tickets in DB so far.")

        if found_ids == missing:
            print("All missing tickets found!")
            break

        time.sleep(DELAY_BETWEEN_SEARCHES)

    # Final flush
    flush_to_db(config, collected, found_ids)
    print(f"\nFound {len(found_ids)} of {total_missing} missing tickets.")

    # Rebuild linked model and report
    print("Materializing ticket_linked_surveys ...")
    model = materialize_ticket_linked_surveys(config)
    write_survey_health_report(model, config.report_output_path)
    print(f"Linked rows: {int(model['ticket_linked'].sum())}")
    print("Report written.")


if __name__ == "__main__":
    main()
