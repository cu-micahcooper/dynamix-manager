"""Validate reporting aggregates before retiring the local tickets table."""

import argparse

import duckdb
import pandas as pd

from dynamix_manager.cfo import summarize_cfo_snapshot
from dynamix_manager.cfo_counts import load_metrics, save_counts
from dynamix_manager.config import load_runtime_config


def migrate(path, *, retire=False):
    # Hold the writer connection throughout; another process cannot update the
    # source between verification and the final transactional cutover.
    with duckdb.connect(str(path)) as db:
        as_of, start = db.execute("""
            select period_end,period_start from cfo_email_runs order by run_at desc limit 1
        """).fetchone()
        end, start = pd.Timestamp(as_of), pd.Timestamp(start)
        if not db.execute(
            "select count(*) from duckdb_tables() where table_name='tickets'"
        ).fetchone()[0]:
            return {"already_retired": True}
        tickets = db.execute("select * from tickets").fetchdf()
        legacy = summarize_cfo_snapshot(
            tickets, pd.DataFrame(), as_of=end, period_start=start
        )
        save_counts(path, tickets, start, end, include_archive_history=True)
        # Read on the writer connection's settings while the process owns the DB.
        metrics = load_metrics(path, start, end)
        if any(legacy[k] != v for k, v in metrics.items()):
            raise ValueError("CFO volume or backlog comparison failed")
        result = {
            "source_rows": len(tickets),
            "matched_cfo_fields": len(metrics),
            "retired": retire,
        }
        if not retire:
            return result
        db.execute("begin transaction")
        db.execute("""create table survey_dimensions as select response_id,team_name,service_name,
            assignee_name,ticket_linked,response_time_hours,resolution_time_hours,true as in_scope from ticket_linked_surveys""")
        db.execute("alter table survey_dimensions add primary key(response_id)")
        db.execute("""insert into survey_dimensions(response_id, in_scope)
                      select distinct response_id,false from survey_responses
                      where response_id not in (select response_id from survey_dimensions)""")
        join = "select s.*, d.* exclude(response_id,in_scope) from survey_responses s join survey_dimensions d using(response_id) where d.in_scope"
        if db.execute(
            f"select exists ((select * from ticket_linked_surveys except all {join}) union all ({join} except all select * from ticket_linked_surveys))"
        ).fetchone()[0]:
            raise ValueError("Survey reporting rows changed")
        db.execute("create or replace view ticket_linked_surveys as " + join)
        db.execute("drop table tickets")
        db.execute("commit")
        db.execute("checkpoint")
        return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--retire",
        action="store_true",
        help="Remove the archive after report verification",
    )
    args = parser.parse_args()
    print(migrate(load_runtime_config().db_path, retire=args.retire))
