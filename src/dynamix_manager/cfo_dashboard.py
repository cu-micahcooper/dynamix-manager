from __future__ import annotations

import json
import re
from pathlib import Path


EXCLUDED_MAJOR_PROJECT_TEAMS = {"tech pros"}


def build_cfo_dashboard_data(
    snapshot: dict,
    *,
    aha_roadmap: dict[str, object] | None = None,
) -> dict[str, object]:
    projects = _major_it_projects(snapshot.get("youtrack_projects", []) or [])
    movement = dict(snapshot.get("youtrack_project_movement", {}) or {})
    movement["in_progress_count"] = len(projects)

    if "new_this_week" in movement:
        movement["new_this_week"] = sum(1 for project in projects if project.get("is_new_this_week"))

    team_counts: dict[str, int] = {}
    for project in projects:
        team = _project_team(project)
        team_counts[team] = team_counts.get(team, 0) + 1

    return {
        "period": str(snapshot.get("period_label") or snapshot.get("week_range_label") or ""),
        "generatedAt": str(snapshot.get("report_generated_at") or ""),
        "metrics": {
            "created": int(snapshot.get("tickets_created_this_week", 0) or 0),
            "createdPrior": int(snapshot.get("tickets_created_prior_week", 0) or 0),
            "createdYearAgo": int(snapshot.get("tickets_created_year_ago", 0) or 0),
            "createdDelta": snapshot.get("tickets_created_ww_delta_pct"),
            "closed": int(snapshot.get("tickets_closed_this_week", 0) or 0),
            "closedPrior": int(snapshot.get("tickets_closed_prior_week", 0) or 0),
            "closedYearAgo": int(snapshot.get("tickets_closed_year_ago", 0) or 0),
            "closedDelta": snapshot.get("tickets_closed_ww_delta_pct"),
            "open": int(snapshot.get("total_open_tickets", 0) or 0),
            "openPrior": int(snapshot.get("total_open_tickets_prior_week", 0) or 0),
            "openDelta": int(snapshot.get("total_open_tickets_ww_delta", 0) or 0),
        },
        "weekly": {
            "created": snapshot.get("created_weekly", []),
            "closed": snapshot.get("closed_weekly", []),
            "open": snapshot.get("open_weekly", []),
        },
        "survey": {
            "weekCounts": snapshot.get("survey_satisfaction_counts", {}),
            "weekTotal": int(snapshot.get("survey_satisfaction_total", 0) or 0),
            "yearCounts": snapshot.get("survey_satisfaction_counts_trailing_year", {}),
            "yearTotal": int(snapshot.get("survey_satisfaction_total_trailing_year", 0) or 0),
            "comments": [_dashboard_comment(comment) for comment in snapshot.get("survey_comments", [])],
        },
        "projects": {
            "movement": movement,
            "teamCounts": dict(sorted(team_counts.items(), key=lambda item: (-item[1], item[0]))),
            "samples": [
                {
                    "id": str(project.get("id") or ""),
                    "summary": str(project.get("summary") or project.get("name") or ""),
                    "team": _project_team(project),
                    "isNew": bool(project.get("is_new_this_week")),
                }
                for project in projects[:8]
            ],
        },
        "ahaRoadmap": aha_roadmap
        or {
            "workspace_count": 0,
            "goal_count": 0,
            "initiative_count": 0,
            "workspaces": [],
        },
    }


def write_cfo_dashboard_site(site_dir: Path, data: dict[str, object]) -> None:
    pretty = json.dumps(data, indent=2, ensure_ascii=True)
    (site_dir / "data.json").write_text(pretty + "\n")

    index_html = (site_dir / "index.html").read_text()
    index_js = (site_dir / "index.js").read_text()
    html_literal = json.dumps(index_html, ensure_ascii=True)
    index_js = re.sub(
        r"const HTML = .*?;\n",
        lambda _: f"const HTML = {html_literal};\n",
        index_js,
        count=1,
        flags=re.S,
    )
    index_js = re.sub(
        r"const DATA = \{.*?\};\n\nexport default \{",
        lambda _: f"const DATA = {pretty};\n\nexport default {{",
        index_js,
        count=1,
        flags=re.S,
    )
    (site_dir / "index.js").write_text(index_js)


def _major_it_projects(projects: list[dict]) -> list[dict]:
    filtered = [
        project
        for project in projects
        if _project_team(project).strip().lower() not in EXCLUDED_MAJOR_PROJECT_TEAMS
    ]
    return sorted(filtered, key=lambda project: (_project_team(project).lower(), str(project.get("summary") or "")))


def _project_team(project: dict) -> str:
    return str(
        project.get("it_team")
        or project.get("team_name")
        or project.get("short_name")
        or "Unassigned"
    ).strip()


def _dashboard_comment(comment: dict) -> dict[str, str]:
    excerpt = " ".join(str(comment.get("comment_text") or "").strip().split())
    if len(excerpt) > 220:
        excerpt = excerpt[:219].rstrip() + "..."
    return {
        "date": str(comment.get("survey_completed_at") or "")[:10],
        "sentiment": str(comment.get("satisfaction_label") or ""),
        "excerpt": excerpt,
    }
