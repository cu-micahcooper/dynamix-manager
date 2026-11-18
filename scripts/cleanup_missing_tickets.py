"""Fetch remaining missing tickets one-by-one, skipping 404s."""
from __future__ import annotations

import time

import duckdb
import requests as req_lib

from dynamix_manager.config import load_runtime_config
from dynamix_manager.pipeline import materialize_ticket_linked_surveys
from dynamix_manager.reporting import write_survey_health_report
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


def save_checkpoint(db_path, rows):
    batch = normalize_ticket_rows(rows).drop_duplicates("ticket_id", keep="first")
    batch["ticket_app_id"] = TICKET_APP_ID
    batch["ticket_app_name"] = "InfoTech Tickets"
    with duckdb.connect(str(db_path)) as conn:
        conn.register("batch", batch)
        conn.execute("begin transaction")
        existing = {r[0]: r[1] for r in conn.execute("describe tickets").fetchall()}
        # Older snapshots inferred INTEGER for text fields containing only nulls.
        for name, dtype, *_ in conn.execute("describe batch").fetchall():
            if name not in existing:
                conn.execute(f'alter table tickets add column "{name}" {dtype}')
            elif dtype == "VARCHAR" and existing[name] in ("INTEGER", "BIGINT"):
                conn.execute(f'alter table tickets alter column "{name}" type varchar')
        conn.execute("""
            insert into tickets by name
            select b.* from batch b
            where not exists (select 1 from tickets t where t.ticket_id = b.ticket_id)
        """)
        conn.execute("commit")


def main() -> int:
    config = load_runtime_config()
    client = TeamDynamixClient(
        base_url=config.base_url,
        app_id=config.app_id,
        username=config.username,
        password=config.password,
    )
    token = client.authenticate()

    ids = missing_ticket_ids(config.db_path)
    total = len(ids)
    print(f"Missing tickets to fetch individually: {total}")
    retry_file = config.db_path.with_suffix(".unresolved-ticket-ids.txt")
    if not total:
        print("Nothing to do.")
        retry_file.unlink(missing_ok=True)
        return 0

    buffer: list[dict] = []
    fetched = missing = 0
    unresolved = []

    for i, tid in enumerate(ids):
        # Refresh token every 50 calls to avoid expiry
        if i > 0 and i % 50 == 0:
            token = client.authenticate()

        for attempt in range(4):
            try:
                resp = client.session.get(
                    client.ticket_endpoint(tid, TICKET_APP_ID),
                    headers=build_auth_headers(token, str(TICKET_APP_ID)),
                    timeout=30,
                )
                if resp.status_code == 404:
                    missing += 1
                    break
                if resp.status_code == 429:
                    if attempt == 3:
                        continue
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
        else:
            unresolved.append(tid)

        if fetched % REPORT_EVERY == 0 and fetched > 0:
            print(f"  {fetched} fetched, {missing} not found, {len(unresolved)} unresolved ({i+1}/{total})")

        if len(buffer) >= SAVE_EVERY:
            save_checkpoint(config.db_path, buffer)
            buffer.clear()
            print(f"  Saved checkpoint at {fetched} fetched.")

        time.sleep(DELAY)

    if buffer:
        save_checkpoint(config.db_path, buffer)

    print(f"Fetched {fetched}, confirmed 404: {missing}, unresolved: {len(unresolved)}.")
    if unresolved:
        temporary = retry_file.with_suffix(".tmp")
        temporary.write_text("".join(f"{tid}\n" for tid in unresolved))
        temporary.replace(retry_file)
        print(f"Unresolved ticket IDs saved to {retry_file}; rerun to retry.")
        return 1
    retry_file.unlink(missing_ok=True)
    print("Materializing ticket_linked_surveys ...")
    model = materialize_ticket_linked_surveys(config)
    write_survey_health_report(model, config.report_output_path)
    print(f"Linked rows: {int(model['ticket_linked'].sum())}")
    print("Report written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
