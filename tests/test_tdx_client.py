import requests
import pytest

import dynamix_manager.tdx_client as tdx_client
from dynamix_manager.tdx_client import (
    TeamDynamixClient,
    build_auth_headers,
    parse_auth_token,
    uses_admin_auth,
)


def test_build_auth_headers_includes_bearer_and_app_id():
    headers = build_auth_headers("token", "1234")

    assert headers["Authorization"] == "Bearer token"
    assert headers["X-TDClient-ID"] == "1234"
    assert headers["Accept"] == "application/json"


def test_teamdynamix_client_builds_report_and_ticket_endpoints():
    client = TeamDynamixClient(
        base_url="https://example.teamdynamix.com/TDWebApi",
        app_id="1234",
        username="user",
        password="pass",
    )

    assert client.report_endpoint(100482) == (
        "https://example.teamdynamix.com/TDWebApi/api/reports/100482"
    )
    assert client.ticket_search_endpoint() == (
        "https://example.teamdynamix.com/TDWebApi/api/1234/tickets/search"
    )
    assert client.ticket_endpoint(42, ticket_app_id=634) == (
        "https://example.teamdynamix.com/TDWebApi/api/634/tickets/42"
    )
    assert client.ticket_feed_endpoint(42, ticket_app_id=634) == (
        "https://example.teamdynamix.com/TDWebApi/api/634/tickets/42/feed"
    )
    assert client.feed_item_endpoint("api/feed/22872139") == (
        "https://example.teamdynamix.com/TDWebApi/api/feed/22872139"
    )
    assert client.days_off_endpoint() == (
        "https://example.teamdynamix.com/TDWebApi/api/daysoff"
    )


def test_uses_admin_auth_requires_guid_credentials():
    assert uses_admin_auth(
        "01234567-89ab-cdef-0123-456789abcdef",
        "fedcba98-7654-3210-fedc-ba9876543210",
    )
    assert not uses_admin_auth("user@example.edu", "not-a-guid")


def test_parse_auth_token_accepts_json_or_plain_text():
    assert parse_auth_token("token-value") == "token-value"
    assert parse_auth_token({"Token": "token-value"}) == "token-value"


def test_list_ticketing_applications_filters_non_ticket_apps():
    client = TeamDynamixClient(
        base_url="https://example.teamdynamix.com/TDWebApi",
        app_id="1234",
        username="user",
        password="pass",
    )

    applications = [
        {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
        {"AppID": 2045, "Name": "Client Portal", "AppClass": "TDClient"},
    ]

    assert client.list_ticketing_applications(applications) == [
        {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"}
    ]


def test_get_ticket_retries_rate_limited_responses(monkeypatch):
    class FakeResponse:
        def __init__(self, status_code, payload, headers=None):
            self.status_code = status_code
            self._payload = payload
            self.headers = headers or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(response=self)

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.calls = 0

        def get(self, url, headers, timeout):
            self.calls += 1
            if self.calls == 1:
                return FakeResponse(429, {"Message": "Too many requests"}, {"Retry-After": "0"})
            return FakeResponse(200, {"ID": 42, "Title": "Recovered"})

    sleeps = []
    monkeypatch.setattr(tdx_client.time, "sleep", sleeps.append)
    client = TeamDynamixClient(
        base_url="https://example.teamdynamix.com/TDWebApi",
        app_id="1234",
        username="user",
        password="pass",
        session=FakeSession(),
    )

    ticket = client.get_ticket(42, token="token", ticket_app_id=634)

    assert ticket["ID"] == 42
    assert sleeps == [0.0]


def test_get_ticket_can_fail_fast_after_one_rate_limited_attempt(monkeypatch):
    class FakeResponse:
        def __init__(self, status_code, payload, headers=None):
            self.status_code = status_code
            self._payload = payload
            self.headers = headers or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(response=self)

        def json(self):
            return self._payload

    class FakeSession:
        def __init__(self):
            self.calls = 0

        def get(self, url, headers, timeout):
            self.calls += 1
            return FakeResponse(429, {"Message": "Too many requests"}, {"Retry-After": "0"})

    sleeps = []
    monkeypatch.setattr(tdx_client.time, "sleep", sleeps.append)
    client = TeamDynamixClient(
        base_url="https://example.teamdynamix.com/TDWebApi",
        app_id="1234",
        username="user",
        password="pass",
        session=FakeSession(),
    )

    with pytest.raises(requests.HTTPError):
        client.get_ticket(42, token="token", ticket_app_id=634, max_attempts=1)

    assert client.session.calls == 1
    assert sleeps == []


def test_fetch_days_off_returns_json_rows():
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return [{"ID": 6191, "Name": "Christmas", "Date": "2017-12-25T05:00:00Z"}]

    class FakeSession:
        def get(self, url, headers, timeout):
            return FakeResponse()

    client = TeamDynamixClient(
        base_url="https://example.teamdynamix.com/TDWebApi",
        app_id="1234",
        username="user",
        password="pass",
        session=FakeSession(),
    )

    rows = client.fetch_days_off("token")

    assert rows == [{"ID": 6191, "Name": "Christmas", "Date": "2017-12-25T05:00:00Z"}]


def test_fetch_ticket_feed_and_feed_item_return_json_rows():
    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeSession:
        def get(self, url, headers, timeout):
            if url.endswith("/feed"):
                return FakeResponse([{"ID": 101}])
            return FakeResponse({"ID": 101, "Replies": [{"ID": 201}]})

    client = TeamDynamixClient(
        base_url="https://example.teamdynamix.com/TDWebApi",
        app_id="1234",
        username="user",
        password="pass",
        session=FakeSession(),
    )

    feed = client.get_ticket_feed(42, token="token", ticket_app_id=634)
    detail = client.get_feed_item("api/feed/101", token="token")

    assert feed == [{"ID": 101}]
    assert detail["Replies"] == [{"ID": 201}]
