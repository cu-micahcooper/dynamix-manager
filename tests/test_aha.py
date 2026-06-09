from dynamix_manager.aha import parse_aha_roadmap_pivot


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
                            "row_ref": 2,
                            "field_definition_ref": 17,
                        }
                    ],
                    [
                        {
                            "id": "initiative-2",
                            "plain_value": "Initial Vendor Demos",
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
                        {"id": "initiative-1", "name": "Software and SI RFP"},
                        {"id": "initiative-2", "name": "Initial Vendor Demos"},
                    ],
                }
            ],
        }
    ]
