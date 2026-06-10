from dynamix_manager.aha import enrich_aha_roadmap_details, parse_aha_roadmap_pivot


def test_parse_aha_roadmap_pivot_groups_initiatives_by_workspace_and_goal():
    payload = {
        "top_level_rows": [1],
        "rows": {
            "1": {
                "ref": 1,
                "parent_ref": None,
                "child_refs": [2],
                "id": "workspace-1",
                "plain_value": "ERP Project",
            },
            "2": {
                "ref": 2,
                "parent_ref": 1,
                "child_refs": [],
                "id": "goal-1",
                "plain_value": "ERP Selection",
            },
        },
        "cells": [
            [
                [
                    [
                        {
                            "id": "initiative-1",
                            "plain_value": "Software and SI RFP",
                            "html_value": '<a href="/initiatives/ERP-S-1">Software and SI RFP</a>',
                            "row_ref": 2,
                            "field_definition_ref": 17,
                        }
                    ],
                    [
                        {
                            "id": "initiative-2",
                            "plain_value": "Initial Vendor Demos",
                            "html_value": '<a href="/initiatives/ERP-S-2">Initial Vendor Demos</a>',
                            "row_ref": 2,
                            "field_definition_ref": 17,
                        }
                    ],
                ]
            ]
        ],
    }

    roadmap = parse_aha_roadmap_pivot(payload)

    assert roadmap["workspace_count"] == 1
    assert roadmap["goal_count"] == 1
    assert roadmap["initiative_count"] == 2
    assert roadmap["workspaces"] == [
        {
            "id": "workspace-1",
            "name": "ERP Project",
            "goals": [
                {
                    "id": "goal-1",
                    "name": "ERP Selection",
                    "initiatives": [
                        {
                            "id": "initiative-1",
                            "name": "Software and SI RFP",
                            "reference_num": "ERP-S-1",
                        },
                        {
                            "id": "initiative-2",
                            "name": "Initial Vendor Demos",
                            "reference_num": "ERP-S-2",
                        },
                    ],
                }
            ],
        }
    ]


def test_enrich_aha_roadmap_details_adds_end_dates_progress_and_status():
    roadmap = {
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
                        "initiatives": [
                            {
                                "id": "initiative-1",
                                "name": "Software and SI RFP",
                                "reference_num": "ERP-S-1",
                            }
                        ],
                    }
                ],
            }
        ],
    }

    def fake_fetcher(base_url, api_key, reference_num):
        assert base_url == "https://example.aha.io"
        assert api_key == "test-key"
        assert reference_num == "ERP-S-1"
        return {
            "reference_num": "ERP-S-1",
            "end_date": "2026-09-30",
            "progress": 72,
            "url": "https://example.aha.io/initiatives/ERP-S-1",
            "workflow_status": {"name": "On track", "color": "#5c9ded"},
        }

    enriched = enrich_aha_roadmap_details(
        roadmap,
        "https://example.aha.io",
        "test-key",
        fetcher=fake_fetcher,
    )

    initiative = enriched["workspaces"][0]["goals"][0]["initiatives"][0]
    assert initiative["end_date"] == "2026-09-30"
    assert initiative["progress"] == 72
    assert initiative["status"] == "On track"
    assert initiative["status_color"] == "#5c9ded"
    assert initiative["url"] == "https://example.aha.io/initiatives/ERP-S-1"
