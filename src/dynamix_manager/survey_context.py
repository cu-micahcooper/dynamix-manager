"""Keep response-level reporting dimensions, with no ticket archive."""

import duckdb
import pandas as pd
import requests


def elapsed(ticket, key):
    a, b = (
        pd.to_datetime(ticket.get(field), utc=True, errors="coerce")
        for field in ("CreatedDate", key)
    )
    return (b - a).total_seconds() / 3600 if pd.notna(a) and pd.notna(b) else None


def refresh_context(path, client, surveys, app=634):
    with duckdb.connect(str(path)) as db:
        db.execute("""create table if not exists survey_dimensions (
            response_id bigint primary key, team_name varchar, service_name varchar, assignee_name varchar,
            ticket_linked boolean, response_time_hours double, resolution_time_hours double, in_scope boolean)""")
        cached = {
            r[0]
            for r in db.execute("select response_id from survey_dimensions").fetchall()
        }
    pending = surveys.loc[
        ~surveys.response_id.isin(cached), ["response_id", "ticket_id"]
    ].drop_duplicates()
    if pending.empty:
        return
    token = client.authenticate()
    rows = []
    for response_id, ticket_id in pending.itertuples(index=False):
        excluded = (int(response_id), None, None, None, False, None, None, False)
        if pd.isna(ticket_id):
            rows.append(excluded)
            continue
        try:
            ticket = client.get_ticket(int(ticket_id), token, app)
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 404:
                raise
            rows.append(excluded)
            continue
        rows.append(
            (
                int(response_id),
                ticket.get("ResponsibleGroupName"),
                ticket.get("ServiceName"),
                ticket.get("RespondingFullName"),
                ticket.get("ServiceName") is not None,
                elapsed(ticket, "RespondedDate"),
                elapsed(ticket, "CompletedDate"),
                True,
            )
        )
    if rows:
        with duckdb.connect(str(path)) as db:
            db.execute("begin transaction")
            db.executemany(
                "insert or replace into survey_dimensions values(?,?,?,?,?,?,?,?)", rows
            )
            db.execute("commit")
