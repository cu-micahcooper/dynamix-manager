"""Fetch remaining missing tickets one-by-one, skipping 404s."""
from __future__ import annotations

import time

import duckdb
import pandas as pd
import requests as req_lib

from dynamix_manager.config import load_runtime_config
from dynamix_manager.pipeline import materialize_ticket_linked_surveys
from dynamix_manager.reporting import write_survey_health_report
from dynamix_manager.storage import read_table, replace_table
from dynamix_manager.tdx_client import TeamDynamixClient, build_auth_headers
from dynamix_manager.tickets import normalize_ticket_rows

TICKET_APP_ID = 634
DELAY = 1.05
SAVE_EVERY = 100
REPORT_EVERY = 50


def missing_ticket_ids(db_path) -> list[int]:
    conn = duckdb.connect(str(db_path), read_only=True)
    rows = conn.execute("""
        select distinct sr.ticket_id
        from survey_responses sr
        left join tickets t on sr.ticket_id = t.ticket_id
        where sr.ticket_id is not null and t.ticket_id is null
        order by sr.ticket_id
    """).fetchdf()
    conn.close()
    return [int(x) for x in rows["ticket_id"].tolist()]


def main() -> None:
    config = load_runtime_config()
    client = TeamDynamixClient(
        base_url=config.base_url,
        app_id=config.app_id,
        username=config.username,
        password=config.password,
    )
    token = client.authenticate()
    token_calls = 0

    ids = missing_ticket_ids(config.db_path)
    total = len(ids)
    print(f"Missing tickets to fetch individually: {total}")
    if not total:
        print("Nothing to do.")
        return

    buffer: list[dict] = []
    fetched = skipped = 0

    for i, tid in enumerate(ids):
        # Refresh token every 50 calls to avoid expiry
        if i > 0 and i % 50 == 0:
            token = client.authenticate()
            token_calls = 0

        for attempt in range(4):
            try:
                resp = client.session.get(
                    client.ticket_endpoint(tid, TICKET_APP_ID),
                    headers=build_auth_headers(token, str(TICKET_APP_ID)),
                    timeout=30,
                )
                if resp.status_code == 404:
                    skipped += 1
                    break
                if resp.status_code == 429:
                    wait = float(resp.headers.get("Retry-After", 61))
                    print(f"  429 — sleeping {int(wait)}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                buffer.append(resp.json())
                fetched += 1
                break
            except (req_lib.ReadTimeout, req_lib.ConnectionError, req_lib.exceptions.ChunkedEncodingError) as exc:
                if attempt < 3:
                    print(f"  {type(exc).__name__} on {tid} — retrying")
                    time.sleep(10)
                else:
                    print(f"  Giving up on {tid} after 4 attempts")
                    skipped += 1

        if fetched % REPORT_EVERY == 0 and fetched > 0:
            print(f"  {fetched} fetched, {skipped} skipped ({i+1}/{total})")

        if fetched % SAVE_EVERY == 0 and buffer:
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
            buffer = []
            print(f"  Saved checkpoint at {fetched} fetched.")

        time.sleep(DELAY)

    # Final flush
    if buffer:
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

    print(f"\nDone. Fetched {fetched}, skipped {skipped} (404/error).")
    print("Materializing ticket_linked_surveys ...")
    model = materialize_ticket_linked_surveys(config)
    write_survey_health_report(model, config.report_output_path)
    print(f"Linked rows: {int(model['ticket_linked'].sum())}")
    print("Report written.")


if __name__ == "__main__":
    main()
