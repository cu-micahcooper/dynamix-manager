import pandas as pd

from dynamix_manager.ticket_quality import (
    build_ticket_quality_flags,
    normalize_ticket_quality_feed_rows,
)


def test_normalize_ticket_quality_feed_rows_flattens_entries_and_replies():
    feed = [
        {
            "ID": 101,
            "CreatedUid": "client-1",
            "CreatedFullName": "Pat Client",
            "CreatedDate": "2026-03-01T10:00:00Z",
            "Body": "Any update?",
            "IsPrivate": False,
            "IsCommunication": True,
            "UpdateType": 1,
            "RepliesCount": 1,
        }
    ]
    detailed_feed = {
        101: {
            "Replies": [
                {
                    "ID": 201,
                    "CreatedUid": "it-1",
                    "CreatedFullName": "Alex Analyst",
                    "CreatedDate": "2026-03-01T11:00:00Z",
                    "Body": "We are working on it.",
                }
            ]
        }
    }

    frame = normalize_ticket_quality_feed_rows(
        ticket_id=42,
        requestor_uid="client-1",
        requestor_name="Pat Client",
        feed_entries=feed,
        detailed_feed_entries=detailed_feed,
    )

    assert list(frame["interaction_id"]) == [101, 201]
    assert pd.isna(frame.loc[0, "parent_interaction_id"])
    assert frame.loc[1, "parent_interaction_id"] == 101
    assert list(frame["actor_type"]) == ["client", "it"]
    assert list(frame["interaction_source"]) == ["entry", "reply"]


def test_build_ticket_quality_flags_finds_client_last_and_it_followup_streak():
    tickets = pd.DataFrame(
        [
            {
                "ticket_id": 42,
                "ticket_title": "Printer issue",
                "status_name": "Open",
                "team_name": "Tech Services",
                "service_name": "Printing",
                "requestor_name": "Pat Client",
                "requestor_uid": "client-1",
                "created_at": "2026-03-01T08:00:00Z",
            },
            {
                "ticket_id": 99,
                "ticket_title": "Laptop setup",
                "status_name": "Open",
                "team_name": "User Services",
                "service_name": "Endpoint",
                "requestor_name": "Riley Requestor",
                "requestor_uid": "client-2",
                "created_at": "2026-03-01T07:00:00Z",
            },
            {
                "ticket_id": 150,
                "ticket_title": "Account issue",
                "status_name": "Open",
                "team_name": "User Services",
                "service_name": "Identity",
                "requestor_name": "Jordan Client",
                "requestor_uid": "client-3",
                "created_at": "2026-03-02T07:00:00Z",
            },
        ]
    )
    interactions = pd.DataFrame(
        [
            {
                "ticket_id": 42,
                "interaction_id": 1,
                "parent_interaction_id": None,
                "created_at": "2026-03-01T09:00:00Z",
                "created_uid": "it-1",
                "created_full_name": "Alex Analyst",
                "body": "Checking now.",
                "is_private": False,
                "is_communication": True,
                "update_type": 1,
                "actor_type": "it",
                "interaction_source": "entry",
            },
            {
                "ticket_id": 42,
                "interaction_id": 2,
                "parent_interaction_id": None,
                "created_at": "2026-03-01T10:00:00Z",
                "created_uid": "client-1",
                "created_full_name": "Pat Client",
                "body": "Thanks, still waiting.",
                "is_private": False,
                "is_communication": True,
                "update_type": 1,
                "actor_type": "client",
                "interaction_source": "entry",
            },
            {
                "ticket_id": 99,
                "interaction_id": 10,
                "parent_interaction_id": None,
                "created_at": "2026-03-01T08:00:00Z",
                "created_uid": "client-2",
                "created_full_name": "Riley Requestor",
                "body": "Need a new laptop.",
                "is_private": False,
                "is_communication": True,
                "update_type": 1,
                "actor_type": "client",
                "interaction_source": "entry",
            },
            {
                "ticket_id": 99,
                "interaction_id": 11,
                "parent_interaction_id": None,
                "created_at": "2026-03-01T09:00:00Z",
                "created_uid": "it-1",
                "created_full_name": "Alex Analyst",
                "body": "Can you confirm pickup timing?",
                "is_private": False,
                "is_communication": True,
                "update_type": 1,
                "actor_type": "it",
                "interaction_source": "entry",
            },
            {
                "ticket_id": 99,
                "interaction_id": 12,
                "parent_interaction_id": None,
                "created_at": "2026-03-02T09:00:00Z",
                "created_uid": "it-1",
                "created_full_name": "Alex Analyst",
                "body": "Following up.",
                "is_private": False,
                "is_communication": True,
                "update_type": 1,
                "actor_type": "it",
                "interaction_source": "entry",
            },
            {
                "ticket_id": 99,
                "interaction_id": 13,
                "parent_interaction_id": None,
                "created_at": "2026-03-03T09:00:00Z",
                "created_uid": "it-1",
                "created_full_name": "Alex Analyst",
                "body": "Checking in again.",
                "is_private": False,
                "is_communication": True,
                "update_type": 1,
                "actor_type": "it",
                "interaction_source": "entry",
            },
            {
                "ticket_id": 99,
                "interaction_id": 14,
                "parent_interaction_id": None,
                "created_at": "2026-03-04T09:00:00Z",
                "created_uid": "it-1",
                "created_full_name": "Alex Analyst",
                "body": "One more follow-up.",
                "is_private": False,
                "is_communication": True,
                "update_type": 1,
                "actor_type": "it",
                "interaction_source": "entry",
            },
            {
                "ticket_id": 99,
                "interaction_id": 15,
                "parent_interaction_id": None,
                "created_at": "2026-03-05T09:00:00Z",
                "created_uid": "it-1",
                "created_full_name": "Alex Analyst",
                "body": "Closing soon without response.",
                "is_private": False,
                "is_communication": True,
                "update_type": 1,
                "actor_type": "it",
                "interaction_source": "entry",
            },
            {
                "ticket_id": 99,
                "interaction_id": 16,
                "parent_interaction_id": None,
                "created_at": "2026-03-05T10:00:00Z",
                "created_uid": "it-1",
                "created_full_name": "Alex Analyst",
                "body": "Changed article.",
                "is_private": False,
                "is_communication": False,
                "update_type": 3,
                "actor_type": "it",
                "interaction_source": "entry",
            },
            {
                "ticket_id": 150,
                "interaction_id": 20,
                "parent_interaction_id": None,
                "created_at": "2026-03-02T08:00:00Z",
                "created_uid": "client-3",
                "created_full_name": "Jordan Client",
                "body": "Any help?",
                "is_private": False,
                "is_communication": True,
                "update_type": 1,
                "actor_type": "client",
                "interaction_source": "entry",
            },
            {
                "ticket_id": 150,
                "interaction_id": 21,
                "parent_interaction_id": None,
                "created_at": "2026-03-03T09:00:00Z",
                "created_uid": "it-3",
                "created_full_name": "Morgan Analyst",
                "body": "Internal work log.",
                "is_private": True,
                "is_communication": False,
                "update_type": 2,
                "actor_type": "it",
                "interaction_source": "entry",
            },
        ]
    )

    days_off = pd.DataFrame([{"holiday_date": "2026-03-04"}])
    flags = build_ticket_quality_flags(
        tickets,
        interactions,
        days_off=days_off,
        as_of="2026-03-10T12:00:00Z",
    )

    row_42 = flags.loc[flags["ticket_id"] == 42].iloc[0]
    assert row_42["client_last_interaction_flag"]
    assert row_42["it_follow_up_streak"] == 0
    assert row_42["stale_public_update_flag"]
    assert row_42["stale_public_update_business_days"] == 5
    assert not row_42["private_activity_since_last_public_flag"]

    row_99 = flags.loc[flags["ticket_id"] == 99].iloc[0]
    assert not row_99["client_last_interaction_flag"]
    assert row_99["it_follow_up_streak"] == 5
    assert row_99["it_follow_up_without_client_response_flag"]
    assert row_99["stale_public_update_flag"]
    assert row_99["stale_public_update_business_days"] == 3
    assert not row_99["private_activity_since_last_public_flag"]

    row_150 = flags.loc[flags["ticket_id"] == 150].iloc[0]
    assert row_150["client_last_interaction_flag"]
    assert row_150["private_activity_since_last_public_flag"]
    assert row_150["last_private_interaction_by"] == "Morgan Analyst"
