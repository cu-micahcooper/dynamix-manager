"""Sweep all tickets by created-date range and cache them — open and closed.

Unlike backfill_all_tickets.py (which only fetches survey-linked IDs), this
script collects every ticket returned by the search API across all date ranges
and upserts them into the tickets table.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import pandas as pd

from dynamix_manager.config import load_runtime_config
from dynamix_manager.storage import read_table, replace_table
from dynamix_manager.tdx_client import TeamDynamixClient, build_auth_headers
from dynamix_manager.tickets import normalize_ticket_rows
import requests as req_lib

TICKET_APP_ID = 634
MAX_RESULTS = 500
DELAY = 1.1          # stay safely under 60/min
SAVE_EVERY_MONTHS = 6
TOKEN_REFRESH_EVERY = 30  # months


def existing_ticket_ids(db_path) -> set[int]:
    existing = read_table(db_path, "tickets")
    if existing is None or existing.empty:
        return set()
    return set(int(x) for x in existing["ticket_id"].tolist())


def search_with_retry(session, token, url, payload, max_attempts=4):
    for attempt in range(max_attempts):
        try:
            resp = session.post(
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
                print(f"  {type(exc).__name__} — sleeping 15s then retrying (attempt {attempt + 1})")
                time.sleep(15)
            else:
                raise
    raise RuntimeError("Max attempts exceeded")


def search_range(session, token, base_url, date_from, date_to):
    url = f"{base_url}/api/{TICKET_APP_ID}/tickets/search"
    payload = {
        "MaxResults": MAX_RESULTS,
        "CreatedDateFrom": date_from.isoformat(),
        "CreatedDateTo": date_to.isoformat(),
    }
    return search_with_retry(session, token, url, payload)


def month_ranges(start: date, end: date):
    current = start.replace(day=1)
    while current <= end:
        next_month = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
        yield current, min(next_month - timedelta(days=1), end)
        current = next_month


def week_ranges(month_start: date, month_end: date):
    current = month_start
    while current <= month_end:
        yield current, min(current + timedelta(days=6), month_end)
        current = current + timedelta(days=7)


def flush_to_db(config, buffer):
    if not buffer:
        return
    new_frame = normalize_ticket_rows(buffer)
    new_frame["ticket_app_id"] = TICKET_APP_ID
    new_frame["ticket_app_name"] = "InfoTech Tickets"
    existing = read_table(config.db_path, "tickets")
    if existing is not None and not existing.empty:
        combined = pd.concat([existing, new_frame], ignore_index=True)
        combined = combined.drop_duplicates(subset=["ticket_id"], keep="last")
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

    cached_ids = existing_ticket_ids(config.db_path)
    print(f"Already cached: {len(cached_ids)} tickets")

    search_start = date(2018, 1, 1)
    search_end = date.today()
    ranges = list(month_ranges(search_start, search_end))
    print(f"Sweeping {len(ranges)} monthly ranges ({search_start} – {search_end}) ...")

    buffer: list[dict] = []
    total_new = 0

    for i, (d_from, d_to) in enumerate(ranges):
        if i > 0 and i % TOKEN_REFRESH_EVERY == 0:
            print(f"  Refreshing auth token at month {i + 1} ...")
            token = client.authenticate()

        rows = search_range(client.session, token, client.base_url, d_from, d_to)
        new_rows = [r for r in rows if r.get("ID") not in cached_ids]
        if new_rows:
            buffer.extend(new_rows)
            cached_ids.update(r["ID"] for r in new_rows)
            total_new += len(new_rows)

        # If MaxResults hit, sweep weekly sub-ranges to catch any overflow
        if len(rows) == MAX_RESULTS:
            for w_from, w_to in week_ranges(d_from, d_to):
                week_rows = search_range(client.session, token, client.base_url, w_from, w_to)
                week_new = [r for r in week_rows if r.get("ID") not in cached_ids]
                if week_new:
                    buffer.extend(week_new)
                    cached_ids.update(r["ID"] for r in week_new)
                    total_new += len(week_new)
                time.sleep(DELAY)

        if new_rows or i % 12 == 0:
            print(f"  [{i + 1}/{len(ranges)}] {d_from} – {d_to}: {len(rows)} returned, {len(new_rows)} new. Total new: {total_new}")

        if (i + 1) % SAVE_EVERY_MONTHS == 0 and buffer:
            flush_to_db(config, buffer)
            print(f"  Checkpoint: saved {total_new} new tickets so far.")

        time.sleep(DELAY)

    flush_to_db(config, buffer)
    print(f"\nDone. Added {total_new} new tickets. Total cached: {len(cached_ids)}.")


if __name__ == "__main__":
    main()
