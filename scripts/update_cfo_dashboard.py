from __future__ import annotations

from pathlib import Path

import pandas as pd

from dynamix_manager.aha import fetch_aha_roadmap_pivot, parse_aha_roadmap_pivot
from dynamix_manager.cfo import summarize_cfo_snapshot
from dynamix_manager.cfo_dashboard import build_cfo_dashboard_data, write_cfo_dashboard_site
from dynamix_manager.config import RuntimeConfig, load_runtime_config
from dynamix_manager.pipeline import fetch_youtrack_inprogress_projects
from dynamix_manager.storage import read_table, table_exists


def main() -> None:
    config = load_runtime_config()
    as_of, period_start = _latest_cfo_period(config)
    tickets = read_table(config.db_path, "tickets") if table_exists(config.db_path, "tickets") else pd.DataFrame()
    surveys = (
        read_table(config.db_path, "survey_responses")
        if table_exists(config.db_path, "survey_responses")
        else pd.DataFrame()
    )
    youtrack_projects = _fetch_youtrack_projects(config)
    aha_roadmap = _fetch_aha_roadmap(config)
    snapshot = summarize_cfo_snapshot(
        tickets,
        surveys,
        youtrack_projects=youtrack_projects,
        as_of=as_of,
        period_start=period_start,
    )
    data = build_cfo_dashboard_data(snapshot, aha_roadmap=aha_roadmap)
    write_cfo_dashboard_site(Path("sites/cfo-dashboard"), data)
    print(
        {
            "period": data["period"],
            "major_project_count": data["projects"]["movement"]["in_progress_count"],
            "aha_initiative_count": data["ahaRoadmap"]["initiative_count"],
        }
    )


def _latest_cfo_period(config: RuntimeConfig) -> tuple[pd.Timestamp, pd.Timestamp]:
    if not table_exists(config.db_path, "cfo_email_runs"):
        as_of = pd.Timestamp.now("UTC")
        return as_of, as_of - pd.Timedelta(days=7)

    runs = read_table(config.db_path, "cfo_email_runs")
    if runs.empty:
        as_of = pd.Timestamp.now("UTC")
        return as_of, as_of - pd.Timedelta(days=7)

    latest = (
        runs.assign(_run_at=pd.to_datetime(runs["run_at"], utc=True, errors="coerce"))
        .sort_values("_run_at")
        .iloc[-1]
    )
    return (
        pd.to_datetime(latest["period_end"], utc=True, errors="coerce"),
        pd.to_datetime(latest["period_start"], utc=True, errors="coerce"),
    )


def _fetch_youtrack_projects(config: RuntimeConfig) -> list[dict] | dict[str, object]:
    if not config.youtrack_base or not config.youtrack_token:
        return []
    try:
        return fetch_youtrack_inprogress_projects(
            config.youtrack_base,
            config.youtrack_token,
            board_id=config.youtrack_board_id,
        )
    except Exception:
        return []


def _fetch_aha_roadmap(config: RuntimeConfig) -> dict[str, object]:
    if not config.aha_base or not config.aha_key or not config.aha_report_id:
        return {
            "workspace_count": 0,
            "goal_count": 0,
            "initiative_count": 0,
            "workspaces": [],
        }
    payload = fetch_aha_roadmap_pivot(
        config.aha_base,
        config.aha_key,
        report_id=config.aha_report_id,
    )
    return parse_aha_roadmap_pivot(payload)


if __name__ == "__main__":
    main()
