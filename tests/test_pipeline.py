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
    materialize_ticket_linked_surveys,
    refresh_survey_slice,
)
from dynamix_manager.storage import read_table, replace_table


class StubClient:
    def __init__(self, rows):
        self.rows = rows
        self.auth_calls = 0
        self.report_calls = []
        self.ticket_calls = []
        self.app_fetch_calls = 0
        self.applications = []
        self.ticket_payloads = {}
        self.days_off_rows = []
        self.ticket_feed_payloads = {}
        self.feed_item_payloads = {}

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
    client = StubClient([])
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
    client = StubClient([])
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
