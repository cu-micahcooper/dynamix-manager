from dynamix_manager.cfo_dashboard import build_cfo_dashboard_data


def test_build_cfo_dashboard_data_filters_tech_pros_from_major_projects():
    snapshot = {
        "period_label": "Jun 2 – Jun 9",
        "report_generated_at": "2026-06-09T12:00:00+00:00",
        "tickets_created_this_week": 10,
        "tickets_created_prior_week": 8,
        "tickets_created_year_ago": 5,
        "tickets_created_ww_delta_pct": 25.0,
        "tickets_closed_this_week": 9,
        "tickets_closed_prior_week": 6,
        "tickets_closed_year_ago": 4,
        "tickets_closed_ww_delta_pct": 50.0,
        "total_open_tickets": 20,
        "total_open_tickets_prior_week": 22,
        "total_open_tickets_ww_delta": -2,
        "created_weekly": [],
        "closed_weekly": [],
        "open_weekly": [],
        "survey_satisfaction_counts": {},
        "survey_satisfaction_total": 0,
        "survey_satisfaction_counts_trailing_year": {},
        "survey_satisfaction_total_trailing_year": 0,
        "survey_comments": [],
        "youtrack_project_movement": {
            "in_progress_count": 3,
            "new_this_week": 1,
            "completed_this_week": 0,
        },
        "youtrack_projects": [
            {"id": "TOPS-1", "summary": "Core network", "it_team": "Tech Ops"},
            {"id": "TP-1", "summary": "Endpoint cleanup", "it_team": "Tech Pros"},
            {"id": "SEC-1", "summary": "Security posture", "it_team": "Security"},
        ],
    }
    aha_roadmap = {
        "workspace_count": 1,
        "goal_count": 1,
        "initiative_count": 1,
        "workspaces": [
            {
                "id": "workspace-1",
                "name": "ERP Project",
                "goals": [
                    {
                        "id": "goal-1",
                        "name": "ERP Selection",
                        "initiatives": [{"id": "initiative-1", "name": "Software RFP"}],
                    }
                ],
            }
        ],
    }

    data = build_cfo_dashboard_data(snapshot, aha_roadmap=aha_roadmap)

    assert data["projects"]["movement"]["in_progress_count"] == 2
    assert data["projects"]["teamCounts"] == {"Security": 1, "Tech Ops": 1}
    assert [project["id"] for project in data["projects"]["samples"]] == ["SEC-1", "TOPS-1"]
    assert data["ahaRoadmap"] == aha_roadmap
