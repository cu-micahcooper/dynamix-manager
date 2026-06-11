import pandas as pd
import requests

from dynamix_manager.config import RuntimeConfig
from dynamix_manager.pipeline import (
    backfill_ticket_links,
    cache_days_off,
    cache_ticket_quality_slice,
    cache_survey_report,
    cache_ticket_context,
    discover_ticket_app,
    generate_cfo_email,
    generate_executive_report,
    materialize_ticket_linked_surveys,
    refresh_survey_slice,
    sync_tickets,
    _fetch_live_open_ticket_summary,
)
from dynamix_manager.storage import read_table, replace_table


class StubClient:
    def __init__(self, rows):
        self.rows = rows
        self.auth_calls = 0
        self.report_calls = []
        self.ticket_calls = []
        self.search_calls = []
        self.app_fetch_calls = 0
        self.applications = []
        self.ticket_payloads = {}
        self.days_off_rows = []
        self.ticket_feed_payloads = {}
        self.feed_item_payloads = {}
        self.ticket_status_rows = []

    def authenticate(self):
        self.auth_calls += 1
        return "token"

    def fetch_report(self, report_id, token, with_data=True):
        self.report_calls.append((report_id, token, with_data))
        return self.rows

    def fetch_applications(self, token):
        self.app_fetch_calls += 1
        return self.applications

    def list_ticketing_applications(self, applications):
        return [app for app in applications if app.get("AppClass") == "TDTickets"]

    def fetch_days_off(self, token):
        return self.days_off_rows

    def search_tickets(self, token, payload, ticket_app_id=None):
        self.search_calls.append((token, payload, ticket_app_id))
        return self.search_rows if hasattr(self, "search_rows") else []

    def fetch_ticket_statuses(self, token, ticket_app_id):
        return self.ticket_status_rows

    def get_ticket(self, ticket_id, token, ticket_app_id, max_attempts=5):
        self.ticket_calls.append((ticket_id, token, ticket_app_id))
        return self.ticket_payloads[(ticket_app_id, ticket_id)]

    def get_ticket_feed(self, ticket_id, token, ticket_app_id):
        return self.ticket_feed_payloads[(ticket_app_id, ticket_id)]

    def get_feed_item(self, uri, token):
        return self.feed_item_payloads[uri]


def test_cache_survey_report_persists_normalized_rows(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    client = StubClient(
        [
            {
                "ResponseID": 1,
                "TicketID": 42,
                "SurveyRequestedDate": "2026-03-01T11:00:00Z",
                "SurveyCompletedDate": "2026-03-01T12:00:00Z",
                "48398": "Very Satisfied",
                "48399": "Helpful support",
            }
        ]
    )

    df = cache_survey_report(config=config, client=client, report_id=100482)

    persisted = read_table(config.db_path, "survey_responses")
    assert client.auth_calls == 1
    assert client.report_calls == [(100482, "token", True)]
    assert df.loc[0, "ticket_id"] == 42
    assert persisted.loc[0, "comment_text"] == "Helpful support"


def test_cache_days_off_persists_normalized_rows(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    client = StubClient([{"ResponseID": 1, "TicketID": 42}])
    client.days_off_rows = [
        {"ID": 6191, "Name": "Christmas", "Date": "2017-12-25T05:00:00Z"},
    ]

    result = cache_days_off(config=config, client=client)

    persisted = read_table(config.db_path, "days_off")
    assert result == {"rows_written": len(persisted)}
    assert "2017-12-25" in set(persisted["holiday_date"])
    assert "2026-04-03" in set(persisted["holiday_date"])
    assert persisted.loc[0, "name"] == "Christmas"


def test_cache_days_off_includes_planned_future_holidays_and_deduplicates(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    client = StubClient([{"ResponseID": 1, "TicketID": None}])
    client.days_off_rows = [
        {"ID": 9001, "Name": "Good Friday", "Date": "2026-04-03T04:00:00Z"},
    ]

    cache_days_off(config=config, client=client)

    persisted = read_table(config.db_path, "days_off")
    planned_dates = set(persisted["holiday_date"])
    assert "2026-04-03" in planned_dates
    assert "2026-05-25" in planned_dates
    assert "2026-11-27" in planned_dates
    assert "2027-01-01" in planned_dates
    assert len(persisted.loc[persisted["holiday_date"] == "2026-04-03"]) == 1


def test_discover_ticket_app_finds_ticket_application_from_report_rows(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    client = StubClient([{"ResponseID": 1, "TicketID": 42, "48398": "Satisfied"}])
    client.applications = [
        {"AppID": 2045, "Name": "Client Portal", "AppClass": "TDClient"},
        {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
    ]
    client.ticket_payloads[(634, 42)] = {"ID": 42, "Title": "Laptop issue"}

    app = discover_ticket_app(config=config, client=client, report_id=100482)

    assert app["AppID"] == 634
    assert client.ticket_calls == []


def test_discover_ticket_app_prefers_infotech_tickets_by_name(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    client = StubClient([{"ResponseID": 1, "TicketID": 42, "48398": "Satisfied"}])
    client.applications = [
        {"AppID": 2045, "Name": "Facilities Tickets", "AppClass": "TDTickets"},
        {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
    ]

    app = discover_ticket_app(config=config, client=client, report_id=100482)

    assert app["AppID"] == 634
    assert client.ticket_calls == []


def test_cache_ticket_context_persists_ticket_details_for_survey_rows(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    client = StubClient(
        [
            {"ResponseID": 1, "TicketID": 42, "48398": "Very Satisfied"},
            {"ResponseID": 2, "TicketID": 99, "48398": "Dissatisfied"},
        ]
    )
    client.ticket_payloads[(634, 42)] = {
        "ID": 42,
        "Title": "Laptop issue",
        "StatusName": "Closed",
        "ServiceName": "Desktop Support",
        "ResponsibleGroupName": "Client Services",
        "RespondingFullName": "Analyst One",
        "CreatedDate": "2026-03-01T08:00:00Z",
        "RespondedDate": "2026-03-01T08:30:00Z",
        "CompletedDate": "2026-03-01T09:00:00Z",
    }
    client.ticket_payloads[(634, 99)] = {
        "ID": 99,
        "Title": "Wi-Fi issue",
        "StatusName": "Open",
        "ServiceName": "Network",
        "ResponsibleGroupName": "Infrastructure",
        "RespondingFullName": "Analyst Two",
        "CreatedDate": "2026-03-01T10:00:00Z",
        "RespondedDate": "2026-03-01T10:15:00Z",
        "CompletedDate": None,
    }

    df = cache_ticket_context(config=config, client=client, ticket_app_id=634)

    persisted = read_table(config.db_path, "tickets")
    assert set(df["ticket_id"]) == {42, 99}
    assert set(persisted["team_name"]) == {"Client Services", "Infrastructure"}


def test_cache_ticket_context_honors_limit(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    client = StubClient(
        [
            {"ResponseID": 1, "TicketID": 42, "48398": "Very Satisfied"},
            {"ResponseID": 2, "TicketID": 99, "48398": "Dissatisfied"},
        ]
    )
    client.ticket_payloads[(634, 42)] = {
        "ID": 42,
        "Title": "Laptop issue",
        "StatusName": "Closed",
    }
    client.ticket_payloads[(634, 99)] = {
        "ID": 99,
        "Title": "Wi-Fi issue",
        "StatusName": "Open",
    }

    df = cache_ticket_context(config=config, client=client, ticket_app_id=634, limit=1)

    assert list(df["ticket_id"]) == [42]


def test_cache_ticket_quality_slice_persists_flags_and_artifacts(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    replace_table(
        config.db_path,
        "tickets",
        pd.DataFrame(
            [
                {
                    "ticket_id": 42,
                    "ticket_title": "Printer issue",
                    "modified_at": "2026-03-05T10:00:00Z",
                }
            ]
        ),
    )
    client = StubClient([])
    client.ticket_payloads[(634, 42)] = {
        "ID": 42,
        "Title": "Printer issue",
        "StatusName": "Open",
        "ServiceName": "Printing",
        "ResponsibleGroupName": "Tech Services",
        "RespondingFullName": "Alex Analyst",
        "RequestorName": "Pat Client",
        "RequestorUid": "client-1",
        "CreatedDate": "2026-03-01T08:00:00Z",
        "ModifiedDate": "2026-03-05T10:00:00Z",
    }
    client.ticket_feed_payloads[(634, 42)] = [
        {
            "ID": 101,
            "CreatedUid": "client-1",
            "CreatedFullName": "Pat Client",
            "CreatedDate": "2026-03-05T09:00:00Z",
            "Body": "Any update?",
            "IsPrivate": False,
            "IsCommunication": True,
            "UpdateType": 1,
            "RepliesCount": 1,
            "Replies": [],
            "Uri": "api/feed/101",
        }
    ]
    client.feed_item_payloads["api/feed/101"] = {
        "Replies": [
            {
                "ID": 201,
                "CreatedUid": "it-1",
                "CreatedFullName": "Alex Analyst",
                "CreatedDate": "2026-03-05T10:00:00Z",
                "Body": "Working on it.",
            }
        ]
    }

    summary = cache_ticket_quality_slice(config=config, client=client, ticket_app_id=634)

    flags = read_table(config.db_path, "ticket_quality_flags")
    interactions = read_table(config.db_path, "ticket_quality_interactions")
    assert summary["ticket_rows"] == 1
    assert summary["interaction_rows"] == 2
    assert len(flags) == 1
    assert len(interactions) == 2
    assert (tmp_path / "reports" / "ticket_quality.html").exists()
    assert (tmp_path / "notebooks" / "ticket_quality.ipynb").exists()


def test_cache_ticket_quality_slice_writes_ticket_health_report(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.teamdynamix.com/TDWebApi",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    replace_table(
        config.db_path,
        "tickets",
        pd.DataFrame(
            [
                {
                    "ticket_id": 42,
                    "ticket_title": "Printer issue",
                    "ticket_app_id": 634,
                    "modified_at": "2026-03-05T10:00:00Z",
                    "resolved_at": None,
                }
            ]
        ),
    )
    client = StubClient([])
    client.ticket_payloads[(634, 42)] = {
        "ID": 42,
        "Title": "Printer issue",
        "StatusName": "Open",
        "StatusClass": 1,
        "ServiceName": "Printing",
        "ResponsibleGroupName": "Tech Services",
        "RespondingFullName": "Alex Analyst",
        "RequestorName": "Pat Client",
        "RequestorUid": "client-1",
        "CreatedDate": "2020-01-01T08:00:00Z",
        "ModifiedDate": "2026-03-05T10:00:00Z",
    }
    client.ticket_feed_payloads[(634, 42)] = [
        {
            "ID": 101,
            "CreatedUid": "it-1",
            "CreatedFullName": "Alex Analyst",
            "CreatedDate": "2026-03-05T10:00:00Z",
            "Body": "Working on it.",
            "IsPrivate": False,
            "IsCommunication": True,
            "UpdateType": 1,
            "RepliesCount": 0,
            "Replies": [],
            "Uri": "api/feed/101",
        }
    ]

    summary = cache_ticket_quality_slice(config=config, client=client, ticket_app_id=634)

    health_report = tmp_path / "reports" / "ticket_health.html"
    assert health_report.exists()
    content = health_report.read_text()
    assert "Ticket Health" in content
    assert summary["ticket_health_report_written"] == 1


def test_cache_ticket_quality_slice_only_uses_open_tickets(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    replace_table(
        config.db_path,
        "tickets",
        pd.DataFrame(
            [
                {
                    "ticket_id": 42,
                    "ticket_title": "Open printer issue",
                    "status_name": "Open",
                    "modified_at": "2026-03-05T10:00:00Z",
                    "resolved_at": None,
                },
                {
                    "ticket_id": 99,
                    "ticket_title": "Closed laptop issue",
                    "status_name": "Closed",
                    "modified_at": "2026-03-05T09:00:00Z",
                    "resolved_at": "2026-03-04T12:00:00Z",
                },
            ]
        ),
    )
    client = StubClient([])
    client.ticket_payloads[(634, 42)] = {
        "ID": 42,
        "Title": "Open printer issue",
        "StatusName": "Open",
        "ServiceName": "Printing",
        "ResponsibleGroupName": "Tech Services",
        "RespondingFullName": "Alex Analyst",
        "RequestorName": "Pat Client",
        "RequestorUid": "client-1",
        "CreatedDate": "2026-03-01T08:00:00Z",
        "ModifiedDate": "2026-03-05T10:00:00Z",
        "CompletedDate": None,
    }
    client.ticket_payloads[(634, 99)] = {
        "ID": 99,
        "Title": "Closed laptop issue",
        "StatusName": "Closed",
        "ServiceName": "Endpoint",
        "ResponsibleGroupName": "User Services",
        "RespondingFullName": "Alex Analyst",
        "RequestorName": "Riley Requestor",
        "RequestorUid": "client-2",
        "CreatedDate": "2026-03-01T08:00:00Z",
        "ModifiedDate": "2026-03-05T09:00:00Z",
        "CompletedDate": "2026-03-04T12:00:00Z",
    }
    client.ticket_feed_payloads[(634, 42)] = [
        {
            "ID": 101,
            "CreatedUid": "client-1",
            "CreatedFullName": "Pat Client",
            "CreatedDate": "2026-03-05T09:00:00Z",
            "Body": "Any update?",
            "IsPrivate": False,
            "IsCommunication": True,
            "UpdateType": 1,
            "RepliesCount": 0,
            "Replies": [],
            "Uri": "api/feed/101",
        }
    ]

    summary = cache_ticket_quality_slice(config=config, client=client, ticket_app_id=634)

    flags = read_table(config.db_path, "ticket_quality_flags")
    assert summary["ticket_rows"] == 1
    assert list(flags["ticket_id"]) == [42]


def test_cache_ticket_quality_slice_drops_tickets_closed_in_fresh_detail(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    replace_table(
        config.db_path,
        "tickets",
        pd.DataFrame(
            [
                {
                    "ticket_id": 42,
                    "ticket_title": "Stale open ticket",
                    "status_name": "Open",
                    "modified_at": "2026-03-05T10:00:00Z",
                    "resolved_at": None,
                }
            ]
        ),
    )
    client = StubClient([])
    client.ticket_payloads[(634, 42)] = {
        "ID": 42,
        "Title": "Stale open ticket",
        "StatusName": "Closed",
        "StatusClass": 3,
        "ServiceName": "Printing",
        "ResponsibleGroupName": "Tech Services",
        "RespondingFullName": "Alex Analyst",
        "RequestorName": "Pat Client",
        "RequestorUid": "client-1",
        "CreatedDate": "2026-03-01T08:00:00Z",
        "ModifiedDate": "2026-03-05T10:00:00Z",
        "CompletedDate": "2026-03-05T09:00:00Z",
    }
    client.ticket_feed_payloads[(634, 42)] = [
        {
            "ID": 101,
            "CreatedUid": "client-1",
            "CreatedFullName": "Pat Client",
            "CreatedDate": "2026-03-05T09:00:00Z",
            "Body": "Any update?",
            "IsPrivate": False,
            "IsCommunication": True,
            "UpdateType": 1,
            "RepliesCount": 0,
            "Replies": [],
            "Uri": "api/feed/101",
        }
    ]

    summary = cache_ticket_quality_slice(config=config, client=client, ticket_app_id=634)

    flags = read_table(config.db_path, "ticket_quality_flags")
    interactions = read_table(config.db_path, "ticket_quality_interactions")
    assert summary["ticket_rows"] == 0
    assert summary["flag_rows"] == 0
    assert flags.empty
    assert interactions.empty


def test_cache_ticket_context_reuses_existing_ticket_rows(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    replace_table(
        config.db_path,
        "tickets",
        pd.DataFrame(
            [
                {
                    "ticket_id": 42,
                    "ticket_title": "Cached ticket",
                    "status_name": "Closed",
                }
            ]
        ),
    )
    client = StubClient(
        [
            {"ResponseID": 1, "TicketID": 42, "48398": "Very Satisfied"},
            {"ResponseID": 2, "TicketID": 99, "48398": "Dissatisfied"},
        ]
    )
    client.ticket_payloads[(634, 99)] = {
        "ID": 99,
        "Title": "Wi-Fi issue",
        "StatusName": "Open",
    }

    df = cache_ticket_context(config=config, client=client, ticket_app_id=634)

    assert set(df["ticket_id"]) == {42, 99}
    assert client.ticket_calls == [(99, "token", 634)]


def test_cache_ticket_context_keeps_partial_progress_on_rate_limit(tmp_path):
    class RateLimitedClient(StubClient):
        def get_ticket(self, ticket_id, token, ticket_app_id, max_attempts=5):
            self.ticket_calls.append((ticket_id, token, ticket_app_id))
            if ticket_id == 99:
                response = requests.Response()
                response.status_code = 429
                response.url = "https://example.test/api/634/tickets/99"
                raise requests.HTTPError(response=response)
            return self.ticket_payloads[(ticket_app_id, ticket_id)]

    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    client = RateLimitedClient(
        [
            {"ResponseID": 1, "TicketID": 42, "48398": "Very Satisfied"},
            {"ResponseID": 2, "TicketID": 99, "48398": "Dissatisfied"},
        ]
    )
    client.ticket_payloads[(634, 42)] = {
        "ID": 42,
        "Title": "Laptop issue",
        "StatusName": "Closed",
    }

    df = cache_ticket_context(config=config, client=client, ticket_app_id=634)

    assert list(df["ticket_id"]) == [42]
    assert client.ticket_calls == [(42, "token", 634), (99, "token", 634)]


def test_materialize_ticket_linked_surveys_persists_joined_model(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    replace_table(
        config.db_path,
        "survey_responses",
        pd.DataFrame(
            [
                {
                    "response_id": 1,
                    "ticket_id": 42,
                    "survey_completed_at": "2026-03-01T12:00:00Z",
                    "satisfaction_label": "Very Satisfied",
                    "comment_text": "Helpful support",
                }
            ]
        ),
    )
    replace_table(
        config.db_path,
        "tickets",
        pd.DataFrame(
            [
                {
                    "ticket_id": 42,
                    "service_name": "Desktop Support",
                    "team_name": "Client Services",
                    "assignee_name": "Analyst One",
                    "ticket_app_name": "InfoTech Tickets",
                    "created_at": "2026-03-01T08:00:00Z",
                    "responded_at": "2026-03-01T08:30:00Z",
                    "resolved_at": "2026-03-01T09:00:00Z",
                }
            ]
        ),
    )

    model = materialize_ticket_linked_surveys(config)

    persisted = read_table(config.db_path, "ticket_linked_surveys")
    assert model.loc[0, "ticket_linked"]
    assert persisted.loc[0, "response_time_hours"] == 0.5
    assert len(model) == 1


def test_sync_tickets_upserts_modified_tickets(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    replace_table(config.db_path, "tickets", pd.DataFrame([
        {"ticket_id": 1, "ticket_title": "Old", "modified_at": "2026-03-20T10:00:00Z"},
    ]))
    client = StubClient([])
    client.search_rows = [
        {"ID": 2, "Title": "New ticket", "CreatedDate": "2026-04-01T10:00:00Z",
         "ModifiedDate": "2026-04-01T10:00:00Z", "StatusName": "Open"},
    ]

    result = sync_tickets(config, client, ticket_app_id=634)

    tickets = read_table(config.db_path, "tickets")
    assert result["synced_tickets"] == 1
    assert len(tickets) == 2
    assert set(tickets["ticket_id"]) == {1, 2}
    assert client.search_calls[0][1]["ModifiedDateFrom"] == "2026-03-06T10:00:00+00:00"


def test_sync_tickets_catches_up_created_and_closed_volume_windows(tmp_path):
    class CatchupClient(StubClient):
        def search_tickets(self, token, payload, ticket_app_id=None):
            self.search_calls.append((token, payload, ticket_app_id))
            if "CreatedDateFrom" in payload:
                return [
                    {
                        "ID": 2,
                        "Title": "Missed created ticket",
                        "StatusName": "Open",
                        "CreatedDate": "2026-04-10T10:00:00Z",
                        "ModifiedDate": "2026-04-10T10:00:00Z",
                        "CompletedDate": None,
                    }
                ]
            if "ClosedDateFrom" in payload:
                return [
                    {
                        "ID": 3,
                        "Title": "Missed closed ticket",
                        "StatusName": "Closed",
                        "CreatedDate": "2026-03-15T10:00:00Z",
                        "ModifiedDate": "2026-04-12T10:00:00Z",
                        "CompletedDate": "2026-04-12T10:00:00Z",
                    }
                ]
            return []

    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    replace_table(
        config.db_path,
        "tickets",
        pd.DataFrame(
            [
                {
                    "ticket_id": 1,
                    "ticket_title": "Already cached",
                    "modified_at": "2026-04-29T10:00:00Z",
                },
            ]
        ),
    )
    client = CatchupClient([])

    result = sync_tickets(
        config,
        client,
        ticket_app_id=634,
        catchup_start=pd.Timestamp("2026-04-08 15:00:00", tz="UTC"),
        catchup_end=pd.Timestamp("2026-04-29 15:00:00", tz="UTC"),
    )

    tickets = read_table(config.db_path, "tickets")
    payloads = [call[1] for call in client.search_calls]
    assert set(tickets["ticket_id"]) == {1, 2, 3}
    assert result["synced_tickets"] == 0
    assert result["catchup_created_tickets"] == 1
    assert result["catchup_closed_tickets"] == 1
    assert any("CreatedDateFrom" in payload and "CreatedDateTo" in payload for payload in payloads)
    assert any("ClosedDateFrom" in payload and "ClosedDateTo" in payload for payload in payloads)


def test_sync_tickets_catchup_splits_capped_windows_to_avoid_search_caps(tmp_path):
    class CappedClient(StubClient):
        def search_tickets(self, token, payload, ticket_app_id=None):
            self.search_calls.append((token, payload, ticket_app_id))
            if "CreatedDateFrom" not in payload and "ClosedDateFrom" not in payload:
                return []
            from_key = "CreatedDateFrom" if "CreatedDateFrom" in payload else "ClosedDateFrom"
            to_key = "CreatedDateTo" if "CreatedDateTo" in payload else "ClosedDateTo"
            start = pd.Timestamp(payload[from_key])
            end = pd.Timestamp(payload[to_key])
            if end - start > pd.Timedelta(days=1):
                return [
                    {
                        "ID": idx,
                        "Title": f"Capped {idx}",
                        "CreatedDate": start.isoformat(),
                        "ModifiedDate": start.isoformat(),
                    }
                    for idx in range(300)
                ]
            return [
                {
                    "ID": int(start.strftime("%d")),
                    "Title": f"Daily {start:%Y-%m-%d}",
                    "CreatedDate": start.isoformat(),
                    "ModifiedDate": start.isoformat(),
                }
            ]

    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    client = CappedClient([])
    replace_table(config.db_path, "tickets", pd.DataFrame([{"ticket_id": 1}]).iloc[0:0])

    sync_tickets(
        config,
        client,
        ticket_app_id=634,
        catchup_start=pd.Timestamp("2026-04-08 00:00:00", tz="UTC"),
        catchup_end=pd.Timestamp("2026-04-11 00:00:00", tz="UTC"),
    )

    created_payloads = [
        payload for _, payload, _ in client.search_calls if "CreatedDateFrom" in payload
    ]
    closed_payloads = [
        payload for _, payload, _ in client.search_calls if "ClosedDateFrom" in payload
    ]
    assert [payload["CreatedDateFrom"] for payload in created_payloads] == [
        "2026-04-08T00:00:00+00:00",
        "2026-04-08T00:00:00+00:00",
        "2026-04-09T00:00:00+00:00",
        "2026-04-10T00:00:00+00:00",
    ]
    assert [payload["ClosedDateFrom"] for payload in closed_payloads] == [
        "2026-04-08T00:00:00+00:00",
        "2026-04-08T00:00:00+00:00",
        "2026-04-09T00:00:00+00:00",
        "2026-04-10T00:00:00+00:00",
    ]


def test_generate_cfo_email_automatically_expands_period_after_missed_run(tmp_path, monkeypatch):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    replace_table(config.db_path, "tickets", pd.DataFrame([{"ticket_id": 1}]).iloc[0:0])
    replace_table(config.db_path, "survey_responses", pd.DataFrame([{"response_id": 1}]).iloc[0:0])
    last_period_end = pd.Timestamp.now("UTC") - pd.Timedelta(days=21)
    replace_table(
        config.db_path,
        "cfo_email_runs",
        pd.DataFrame(
            [
                {
                    "run_at": last_period_end.isoformat(),
                    "period_start": (last_period_end - pd.Timedelta(days=7)).isoformat(),
                    "period_end": last_period_end.isoformat(),
                    "tickets_created": 12,
                    "tickets_closed": 9,
                    "total_open_tickets": 100,
                    "gmail_draft_id": "old-draft",
                }
            ]
        ),
    )

    monkeypatch.setattr(
        "dynamix_manager.pipeline.fetch_youtrack_inprogress_projects",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr("dynamix_manager.pipeline.credentials_available", lambda path: False)
    monkeypatch.setattr("dynamix_manager.pipeline.render_header_burst_png", lambda tagline: b"png")
    captured: dict[str, object] = {}

    def fake_summarize_cfo_snapshot(
        tickets,
        surveys,
        youtrack_projects=None,
        as_of=None,
        period_start=None,
    ):
        captured["period_start"] = period_start
        captured["as_of"] = as_of
        return {
            "period_label": "catchup period",
            "tickets_created_this_week": 2,
            "tickets_closed_this_week": 1,
            "total_open_tickets": 101,
            "youtrack_projects": [],
            "header_burst_tagline": "Board of Trustee Edition",
        }

    monkeypatch.setattr("dynamix_manager.pipeline.summarize_cfo_snapshot", fake_summarize_cfo_snapshot)

    result = generate_cfo_email(config)

    runs = read_table(config.db_path, "cfo_email_runs")
    assert captured["period_start"] == last_period_end
    assert result["tickets_created_this_week"] == 2
    assert len(runs) == 2
    assert set(runs["tickets_created"]) == {12, 2}


def test_generate_cfo_email_backfills_full_chart_horizon(tmp_path, monkeypatch):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    replace_table(config.db_path, "tickets", pd.DataFrame([{"ticket_id": 1}]).iloc[0:0])
    replace_table(config.db_path, "survey_responses", pd.DataFrame([{"response_id": 1}]).iloc[0:0])
    client = StubClient(
        [
            {
                "ResponseID": 1,
                "TicketID": 42,
                "SurveyCompletedDate": "2026-06-03T12:00:00Z",
            }
        ]
    )
    client.applications = [
        {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
    ]
    client.search_rows = []
    as_of = pd.Timestamp("2026-06-03 19:31:06", tz="UTC")

    monkeypatch.setattr("dynamix_manager.pipeline._now_utc", lambda: as_of)
    monkeypatch.setattr(
        "dynamix_manager.pipeline.fetch_youtrack_inprogress_projects",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr("dynamix_manager.pipeline.credentials_available", lambda path: False)
    monkeypatch.setattr("dynamix_manager.pipeline.render_header_burst_png", lambda tagline: b"png")

    generate_cfo_email(config, client=client)

    created_from_values = [
        pd.Timestamp(payload["CreatedDateFrom"])
        for _, payload, _ in client.search_calls
        if "CreatedDateFrom" in payload
    ]
    assert min(created_from_values) == as_of - pd.Timedelta(days=56)


def test_refresh_survey_slice_runs_end_to_end_and_writes_report(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    client = StubClient([{"ResponseID": 1, "TicketID": 42, "48398": "Very Satisfied"}])
    client.applications = [
        {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
    ]
    client.search_rows = [
        {"ID": 42, "Title": "Laptop issue", "StatusName": "Closed",
         "ServiceName": "Desktop Support", "ResponsibleGroupName": "Client Services",
         "RespondingFullName": "Analyst One", "CreatedDate": "2026-03-01T08:00:00Z",
         "ModifiedDate": "2026-03-01T09:00:00Z", "CompletedDate": "2026-03-01T09:00:00Z"},
    ]

    summary = refresh_survey_slice(config=config, client=client, report_id=100482)

    assert summary["ticket_app_id"] == 634
    assert summary["survey_rows"] == 1
    assert summary["ticket_rows"] == 1
    assert summary["linked_rows"] == 1
    assert config.report_output_path.exists()
    assert config.notebook_output_path.exists()


def test_backfill_ticket_links_runs_multiple_batches_until_no_progress(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    client = StubClient(
        [
            {"ResponseID": 1, "TicketID": 42, "48398": "Very Satisfied"},
            {"ResponseID": 2, "TicketID": 99, "48398": "Dissatisfied"},
            {"ResponseID": 3, "TicketID": 100, "48398": "Satisfied"},
        ]
    )
    client.applications = [
        {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
    ]
    client.ticket_payloads[(634, 42)] = {"ID": 42, "Title": "One", "StatusName": "Closed"}
    client.ticket_payloads[(634, 99)] = {"ID": 99, "Title": "Two", "StatusName": "Closed"}
    client.ticket_payloads[(634, 100)] = {"ID": 100, "Title": "Three", "StatusName": "Closed"}

    summary = backfill_ticket_links(
        config=config,
        client=client,
        report_id=100482,
        batch_size=2,
        max_batches=3,
    )

    persisted = read_table(config.db_path, "tickets")
    assert summary["batches_run"] == 2
    assert summary["ticket_rows"] == 3
    assert set(persisted["ticket_id"]) == {42, 99, 100}
    assert client.auth_calls == 1
    assert client.report_calls == [(100482, "token", True)]
    assert client.app_fetch_calls == 1


def test_backfill_ticket_links_materializes_outputs_once_after_batching(tmp_path, monkeypatch):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    client = StubClient(
        [
            {"ResponseID": 1, "TicketID": 42, "48398": "Very Satisfied"},
            {"ResponseID": 2, "TicketID": 99, "48398": "Dissatisfied"},
            {"ResponseID": 3, "TicketID": 100, "48398": "Satisfied"},
        ]
    )
    client.applications = [
        {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
    ]
    client.ticket_payloads[(634, 42)] = {"ID": 42, "Title": "One", "StatusName": "Closed"}
    client.ticket_payloads[(634, 99)] = {"ID": 99, "Title": "Two", "StatusName": "Closed"}
    client.ticket_payloads[(634, 100)] = {"ID": 100, "Title": "Three", "StatusName": "Closed"}

    report_calls = []
    notebook_calls = []

    def fake_report(model, output_path):
        report_calls.append((len(model), output_path))

    def fake_notebook(db_path, output_path):
        notebook_calls.append((db_path, output_path))

        class _NotebookPath:
            def exists(self):
                return True

        return _NotebookPath()

    monkeypatch.setattr("dynamix_manager.pipeline.write_survey_health_report", fake_report)
    monkeypatch.setattr("dynamix_manager.pipeline.write_survey_health_notebook", fake_notebook)

    summary = backfill_ticket_links(
        config=config,
        client=client,
        report_id=100482,
        batch_size=2,
        max_batches=3,
    )

    assert summary["ticket_rows"] == 3
    assert summary["batches_run"] == 2
    assert len(report_calls) == 1
    assert len(notebook_calls) == 1


def test_generate_executive_report_writes_html_and_returns_summary(tmp_path):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    replace_table(
        config.db_path,
        "tickets",
        pd.DataFrame([
            {
                "ticket_id": 1,
                "created_at": "2026-03-30T10:00:00Z",
                "resolved_at": None,
                "service_name": "Printing",
            }
        ]),
    )
    replace_table(
        config.db_path,
        "survey_responses",
        pd.DataFrame([
            {
                "response_id": 1,
                "satisfaction_label": "Very Satisfied",
                "survey_completed_at": "2026-03-31T12:00:00Z",
            }
        ]),
    )

    result = generate_executive_report(config)

    report_path = tmp_path / "reports" / "executive_report.html"
    assert report_path.exists()
    assert "IT Executive Report" in report_path.read_text()
    assert result["report_written"] == 1
    assert "new_tickets_this_week" in result


def test_generate_cfo_email_uses_period_label_in_draft_subject(tmp_path, monkeypatch):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
        gmail_token_path=str(tmp_path / "gmail-token.json"),
        gmail_draft_to="cfo@example.test",
    )
    replace_table(config.db_path, "tickets", pd.DataFrame([{"ticket_id": 1}]).iloc[0:0])
    replace_table(config.db_path, "survey_responses", pd.DataFrame([{"response_id": 1}]).iloc[0:0])

    monkeypatch.setattr(
        "dynamix_manager.pipeline.fetch_youtrack_inprogress_projects",
        lambda *args, **kwargs: [],
    )

    captured: dict[str, object] = {}

    def fake_create_draft(
        *,
        subject: str,
        html_body: str,
        to: str,
        token_path_override: str | None = None,
        inline_attachments: list[dict[str, object]] | None = None,
    ):
        captured["subject"] = subject
        captured["to"] = to
        captured["html_body"] = html_body
        captured["token_path_override"] = token_path_override or ""
        captured["inline_attachments"] = inline_attachments or []
        return {"id": "draft-123"}

    monkeypatch.setattr("dynamix_manager.pipeline.credentials_available", lambda path: True)
    monkeypatch.setattr("dynamix_manager.pipeline.create_draft", fake_create_draft)
    monkeypatch.setattr("dynamix_manager.pipeline.render_header_burst_png", lambda tagline: b"png")
    monkeypatch.setattr(
        "dynamix_manager.pipeline.summarize_cfo_snapshot",
        lambda tickets, surveys, youtrack_projects=None, as_of=None, period_start=None: {
            "period_label": "Apr 8 – Apr 15",
            "tickets_created_this_week": 0,
            "tickets_closed_this_week": 0,
            "total_open_tickets": 0,
            "youtrack_projects": [],
            "header_burst_tagline": "IT'S THE FINAL COUNTDOWN",
        },
    )

    result = generate_cfo_email(config, header_burst_text="IT'S THE FINAL COUNTDOWN")

    assert captured["subject"] == "CFO Update – IT | Apr 8 – Apr 15"
    assert captured["to"] == "cfo@example.test"
    assert 'src="cid:cfo-header-burst"' in str(captured["html_body"])
    assert captured["inline_attachments"] == [
        {
            "filename": "cfo-header-burst.png",
            "content_type": "image/png",
            "content_id": "cfo-header-burst",
            "data": b"png",
        }
    ]
    assert "data:image/png;base64,cG5n" in (tmp_path / "reports" / "cfo_email.html").read_text()
    assert result["gmail_draft_id"] == "draft-123"


def test_generate_cfo_email_omits_header_burst_without_cli_text(tmp_path, monkeypatch):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
        gmail_token_path=str(tmp_path / "gmail-token.json"),
        gmail_draft_to="cfo@example.test",
    )
    replace_table(config.db_path, "tickets", pd.DataFrame([{"ticket_id": 1}]).iloc[0:0])
    replace_table(config.db_path, "survey_responses", pd.DataFrame([{"response_id": 1}]).iloc[0:0])

    monkeypatch.setattr(
        "dynamix_manager.pipeline.fetch_youtrack_inprogress_projects",
        lambda *args, **kwargs: [],
    )
    monkeypatch.setattr("dynamix_manager.pipeline.credentials_available", lambda path: True)
    monkeypatch.setattr(
        "dynamix_manager.pipeline.render_header_burst_png",
        lambda tagline: (_ for _ in ()).throw(AssertionError("header burst should be disabled")),
    )
    captured: dict[str, object] = {}

    def fake_create_draft(
        *,
        subject: str,
        html_body: str,
        to: str,
        token_path_override: str | None = None,
        inline_attachments: list[dict[str, object]] | None = None,
    ):
        captured["html_body"] = html_body
        captured["inline_attachments"] = inline_attachments or []
        return {"id": "draft-123"}

    monkeypatch.setattr("dynamix_manager.pipeline.create_draft", fake_create_draft)
    monkeypatch.setattr(
        "dynamix_manager.pipeline.summarize_cfo_snapshot",
        lambda tickets, surveys, youtrack_projects=None, as_of=None, period_start=None: {
            "period_label": "Apr 8 – Apr 15",
            "tickets_created_this_week": 0,
            "tickets_closed_this_week": 0,
            "total_open_tickets": 0,
            "youtrack_projects": [],
            "header_burst_tagline": "IT'S THE FINAL COUNTDOWN",
        },
    )

    generate_cfo_email(config)

    assert 'src="cid:cfo-header-burst"' not in str(captured["html_body"])
    assert captured["inline_attachments"] == []


def test_generate_cfo_email_passes_datatype_sparklines_to_file_and_draft(tmp_path, monkeypatch):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
        gmail_token_path=str(tmp_path / "gmail-token.json"),
        gmail_draft_to="cfo@example.test",
    )
    replace_table(config.db_path, "tickets", pd.DataFrame([{"ticket_id": 1}]).iloc[0:0])
    replace_table(config.db_path, "survey_responses", pd.DataFrame([{"response_id": 1}]).iloc[0:0])
    captured: dict[str, object] = {}

    monkeypatch.setattr("dynamix_manager.pipeline.credentials_available", lambda path: True)
    monkeypatch.setattr("dynamix_manager.pipeline.fetch_youtrack_inprogress_projects", lambda *args, **kwargs: [])
    monkeypatch.setattr("dynamix_manager.pipeline.build_cfo_discussion_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "dynamix_manager.pipeline.summarize_cfo_snapshot",
        lambda tickets, surveys, youtrack_projects=None, as_of=None, period_start=None: {
            "period_label": "Apr 8 – Apr 15",
            "tickets_created_this_week": 0,
            "tickets_closed_this_week": 0,
            "total_open_tickets": 0,
            "youtrack_projects": [],
        },
    )

    def fake_write_cfo_email(snapshot, output_path, header_burst_img_src=None, datatype_sparklines=False):
        captured["file_datatype_sparklines"] = datatype_sparklines
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("email")

    def fake_render_cfo_email_html(snapshot, header_burst_img_src=None, datatype_sparklines=False):
        captured["draft_datatype_sparklines"] = datatype_sparklines
        return "html"

    monkeypatch.setattr("dynamix_manager.pipeline.write_cfo_email", fake_write_cfo_email)
    monkeypatch.setattr("dynamix_manager.pipeline.render_cfo_email_html", fake_render_cfo_email_html)
    monkeypatch.setattr(
        "dynamix_manager.pipeline.create_draft",
        lambda **kwargs: {"id": "draft-123"},
    )

    result = generate_cfo_email(config, datatype_sparklines=True)

    assert captured["file_datatype_sparklines"] is True
    assert captured["draft_datatype_sparklines"] is True
    assert result["datatype_sparklines"] is True


def test_generate_cfo_email_includes_enriched_aha_roadmap(tmp_path, monkeypatch):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
        aha_base="https://example.aha.io",
        aha_key="test-key",
        aha_report_id="report-123",
    )
    replace_table(config.db_path, "tickets", pd.DataFrame([{"ticket_id": 1}]).iloc[0:0])
    replace_table(config.db_path, "survey_responses", pd.DataFrame([{"response_id": 1}]).iloc[0:0])

    raw_roadmap = {"payload": "pivot"}
    parsed_roadmap = {
        "workspace_count": 1,
        "goal_count": 1,
        "initiative_count": 1,
        "workspaces": [
            {
                "id": "workspace-1",
                "name": "Strategic Technology Projects",
                "goals": [
                    {
                        "id": "goal-1",
                        "name": "Roadmap",
                        "initiatives": [
                            {"id": "initiative-1", "name": "ERP Selection", "reference_num": "ERP-S-1"}
                        ],
                    }
                ],
            }
        ],
    }
    enriched_roadmap = {
        **parsed_roadmap,
        "workspaces": [
            {
                "id": "workspace-1",
                "name": "Strategic Technology Projects",
                "goals": [
                    {
                        "id": "goal-1",
                        "name": "Roadmap",
                        "initiatives": [
                            {
                                "id": "initiative-1",
                                "name": "ERP Selection",
                                "reference_num": "ERP-S-1",
                                "end_date": "2026-09-30",
                                "progress": 72,
                            }
                        ],
                    }
                ],
            }
        ],
    }
    captured: dict[str, object] = {}

    def fake_fetch_aha_roadmap_pivot(base_url, api_key, *, report_id):
        assert base_url == "https://example.aha.io"
        assert api_key == "test-key"
        assert report_id == "report-123"
        return raw_roadmap

    monkeypatch.setattr("dynamix_manager.pipeline.credentials_available", lambda path: False)
    monkeypatch.setattr("dynamix_manager.pipeline.fetch_youtrack_inprogress_projects", lambda *args, **kwargs: [])
    monkeypatch.setattr("dynamix_manager.pipeline.fetch_aha_roadmap_pivot", fake_fetch_aha_roadmap_pivot, raising=False)
    monkeypatch.setattr("dynamix_manager.pipeline.parse_aha_roadmap_pivot", lambda payload: parsed_roadmap, raising=False)
    monkeypatch.setattr(
        "dynamix_manager.pipeline.enrich_aha_roadmap_details",
        lambda roadmap, base_url, api_key: enriched_roadmap,
        raising=False,
    )
    monkeypatch.setattr(
        "dynamix_manager.pipeline.summarize_cfo_snapshot",
        lambda tickets, surveys, youtrack_projects=None, as_of=None, period_start=None: {
            "period_label": "Apr 8 – Apr 15",
            "tickets_created_this_week": 0,
            "tickets_closed_this_week": 0,
            "total_open_tickets": 0,
            "youtrack_projects": [],
        },
    )
    monkeypatch.setattr(
        "dynamix_manager.pipeline.write_cfo_email",
        lambda snapshot, output_path, header_burst_img_src=None, datatype_sparklines=False: captured.setdefault("snapshot", snapshot),
    )

    result = generate_cfo_email(config)

    assert captured["snapshot"]["aha_roadmap"] == enriched_roadmap
    assert result["aha_initiative_count"] == 1


def test_generate_cfo_email_includes_noteplan_discussion_items(tmp_path, monkeypatch):
    notes_dir = tmp_path / "notes"
    notes_dir.mkdir()
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
        cfo_notes_dir=str(notes_dir),
    )
    replace_table(config.db_path, "tickets", pd.DataFrame([{"ticket_id": 1}]).iloc[0:0])
    replace_table(config.db_path, "survey_responses", pd.DataFrame([{"response_id": 1}]).iloc[0:0])
    captured: dict[str, object] = {}
    discussion_items = [{"source": "Next agenda", "text": "Kyle Medical Center"}]

    monkeypatch.setattr("dynamix_manager.pipeline.credentials_available", lambda path: False)
    monkeypatch.setattr("dynamix_manager.pipeline.fetch_youtrack_inprogress_projects", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "dynamix_manager.pipeline.build_cfo_discussion_items",
        lambda notes_path, as_of: discussion_items,
        raising=False,
    )
    monkeypatch.setattr(
        "dynamix_manager.pipeline.summarize_cfo_snapshot",
        lambda tickets, surveys, youtrack_projects=None, as_of=None, period_start=None: {
            "period_label": "Apr 8 – Apr 15",
            "tickets_created_this_week": 0,
            "tickets_closed_this_week": 0,
            "total_open_tickets": 0,
            "youtrack_projects": [],
        },
    )
    monkeypatch.setattr(
        "dynamix_manager.pipeline.write_cfo_email",
        lambda snapshot, output_path, header_burst_img_src=None, datatype_sparklines=False: captured.setdefault("snapshot", snapshot),
    )

    result = generate_cfo_email(config)

    assert captured["snapshot"]["discussion_items"] == discussion_items
    assert result["discussion_item_count"] == 1


def test_fetch_live_open_ticket_summary_counts_cfo_buckets():
    client = StubClient([])
    client.ticket_status_rows = [
        {"ID": 1, "Name": "New", "StatusClass": 1},
        {"ID": 2, "Name": "Open", "StatusClass": 2},
        {"ID": 3, "Name": "Closed", "StatusClass": 3},
        {"ID": 4, "Name": "Cancelled", "StatusClass": 4},
        {"ID": 5, "Name": "On Hold", "StatusClass": 5},
    ]
    client.search_rows = [
        {"ID": 101, "ClassificationName": "Incident", "TypeName": "Incident", "StatusName": "New"},
        {
            "ID": 102,
            "ClassificationName": "Service Request",
            "TypeName": "Service Request",
            "StatusName": "Open",
        },
        {"ID": 103, "ClassificationName": "Change", "TypeName": "Campus Upgrades", "StatusName": "New"},
        {"ID": 104, "ClassificationName": "Change", "TypeName": "Change", "StatusName": "New"},
        {
            "ID": 105,
            "ClassificationName": "Change",
            "TypeName": "Change Management",
            "StatusName": "Open",
        },
        {"ID": 106, "ClassificationName": "Change", "TypeName": "IT Internal", "StatusName": "On Hold"},
        {"ID": 107, "ClassificationName": "Problem", "TypeName": "IT Staff only - Problem", "StatusName": "On Hold"},
    ]

    summary = _fetch_live_open_ticket_summary(client, "token", ticket_app_id=634)

    assert summary["all_open_count"] == 7
    assert summary["scoped_open_count"] == 2
    assert summary["scope_label"] == "Incident/Service Requests"
    assert summary["buckets"] == [
        {
            "key": "incident_service_requests",
            "label": "Incident/Service Requests",
            "count": 2,
            "description": "Incident + Service Request",
        },
        {
            "key": "computer_refresh",
            "label": "Computer Refresh",
            "count": 1,
            "description": "Campus Upgrades",
        },
        {
            "key": "scheduled_changes",
            "label": "Scheduled Changes",
            "count": 3,
            "description": "Change, Change Management, IT Internal",
        },
    ]
    assert client.search_calls == [
        (
            "token",
            {"StatusIDs": [1, 2, 5], "MaxResults": 10000},
            634,
        )
    ]


def test_generate_cfo_email_overrides_open_count_with_live_incident_service_request_count(tmp_path, monkeypatch):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    replace_table(config.db_path, "tickets", pd.DataFrame([{"ticket_id": 1}]).iloc[0:0])
    replace_table(config.db_path, "survey_responses", pd.DataFrame([{"response_id": 1}]).iloc[0:0])
    client = StubClient([{"ResponseID": 1, "TicketID": 42}])
    client.applications = [
        {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
    ]
    captured: dict[str, object] = {}

    monkeypatch.setattr("dynamix_manager.pipeline.credentials_available", lambda path: False)
    monkeypatch.setattr("dynamix_manager.pipeline.fetch_youtrack_inprogress_projects", lambda *args, **kwargs: [])
    monkeypatch.setattr("dynamix_manager.pipeline.build_cfo_discussion_items", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "dynamix_manager.pipeline._fetch_live_open_ticket_summary",
        lambda client, token, ticket_app_id: {
            "all_open_count": 805,
            "scoped_open_count": 340,
            "scope_label": "Incident/Service Requests",
            "buckets": [
                {
                    "key": "incident_service_requests",
                    "label": "Incident/Service Requests",
                    "count": 340,
                    "description": "Incident + Service Request",
                },
                {
                    "key": "computer_refresh",
                    "label": "Computer Refresh",
                    "count": 411,
                    "description": "Campus Upgrades",
                },
                {
                    "key": "scheduled_changes",
                    "label": "Scheduled Changes",
                    "count": 31,
                    "description": "Change, Change Management, IT Internal",
                },
            ],
        },
    )
    monkeypatch.setattr(
        "dynamix_manager.pipeline.summarize_cfo_snapshot",
        lambda tickets, surveys, youtrack_projects=None, as_of=None, period_start=None: {
            "period_label": "Apr 8 – Apr 15",
            "tickets_created_this_week": 0,
            "tickets_closed_this_week": 0,
            "total_open_tickets": 580,
            "total_open_tickets_prior_week": 610,
            "total_open_tickets_ww_delta": -30,
            "open_weekly": [{"week": "Apr 8", "count": 580}],
            "youtrack_projects": [],
        },
    )
    monkeypatch.setattr(
        "dynamix_manager.pipeline.write_cfo_email",
        lambda snapshot, output_path, header_burst_img_src=None, datatype_sparklines=False: captured.setdefault("snapshot", snapshot),
    )

    result = generate_cfo_email(config, client=client)

    assert captured["snapshot"]["total_open_tickets"] == 340
    assert captured["snapshot"]["total_open_tickets_scope_label"] == "Incident/Service Requests"
    assert captured["snapshot"]["total_open_tickets_live_all_open"] == 805
    assert captured["snapshot"]["open_ticket_summary_buckets"] == [
        {
            "key": "incident_service_requests",
            "label": "Incident/Service Requests",
            "count": 340,
            "description": "Incident + Service Request",
        },
        {
            "key": "computer_refresh",
            "label": "Computer Refresh",
            "count": 411,
            "description": "Campus Upgrades",
        },
        {
            "key": "scheduled_changes",
            "label": "Scheduled Changes",
            "count": 31,
            "description": "Change, Change Management, IT Internal",
        },
    ]
    assert "total_open_tickets_prior_week" not in captured["snapshot"]
    assert captured["snapshot"]["open_weekly"] == [{"week": "Apr 8", "count": 580}]
    assert result["total_open_tickets"] == 340


def test_generate_cfo_email_refreshes_live_tdx_data_when_client_provided(tmp_path, monkeypatch):
    config = RuntimeConfig(
        base_url="https://example.test",
        app_id="1234",
        username="user",
        password="pass",
        db_path=tmp_path / "analytics.duckdb",
        report_output_path=tmp_path / "survey_health.html",
        notebook_output_path=tmp_path / "survey_health.ipynb",
    )
    replace_table(
        config.db_path,
        "tickets",
        pd.DataFrame(
            [
                {
                    "ticket_id": 1,
                    "created_at": "2026-04-01T10:00:00Z",
                    "modified_at": "2026-04-01T10:00:00Z",
                }
            ]
        ),
    )
    replace_table(
        config.db_path,
        "survey_responses",
        pd.DataFrame([{"response_id": 1, "survey_completed_at": "2026-04-01T10:00:00Z"}]),
    )

    client = StubClient(
        [
            {
                "ResponseID": 2,
                "TicketID": 42,
                "SurveyCompletedDate": "2026-04-22T12:00:00Z",
                "48398": "Very Satisfied",
                "48399": "Helpful support",
            }
        ]
    )
    client.applications = [
        {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
    ]
    client.search_rows = [
        {
            "ID": 42,
            "Title": "New ticket",
            "StatusName": "Open",
            "CreatedDate": "2026-04-22T10:00:00Z",
            "ModifiedDate": "2026-04-22T10:30:00Z",
            "CompletedDate": None,
        }
    ]

    monkeypatch.setattr(
        "dynamix_manager.pipeline.fetch_youtrack_inprogress_projects",
        lambda *args, **kwargs: [],
    )

    captured: dict[str, object] = {}

    def fake_summarize_cfo_snapshot(
        tickets,
        surveys,
        youtrack_projects=None,
        as_of=None,
        period_start=None,
    ):
        captured["ticket_ids"] = set(tickets["ticket_id"].tolist()) if not tickets.empty else set()
        captured["survey_ticket_ids"] = (
            set(surveys["ticket_id"].dropna().astype(int).tolist())
            if not surveys.empty and "ticket_id" in surveys.columns
            else set()
        )
        return {
            "period_label": "Apr 15 – Apr 22",
            "tickets_created_this_week": 1,
            "tickets_closed_this_week": 0,
            "total_open_tickets": 1,
            "youtrack_projects": [],
            "header_burst_tagline": "IT'S THE FINAL COUNTDOWN",
        }

    monkeypatch.setattr("dynamix_manager.pipeline.summarize_cfo_snapshot", fake_summarize_cfo_snapshot)
    monkeypatch.setattr("dynamix_manager.pipeline.credentials_available", lambda path: False)
    monkeypatch.setattr("dynamix_manager.pipeline.render_header_burst_png", lambda tagline: b"png")

    result = generate_cfo_email(config, client=client)

    payloads = [call[1] for call in client.search_calls]
    assert client.report_calls == [(100482, "token", True), (100482, "token", True)]
    assert any("CreatedDateFrom" in payload and "CreatedDateTo" in payload for payload in payloads)
    assert any("ClosedDateFrom" in payload and "ClosedDateTo" in payload for payload in payloads)
    assert captured["ticket_ids"] == {1, 42}
    assert captured["survey_ticket_ids"] == {42}
    assert result["email_written"] == 1
