import asyncio
import json
from unittest.mock import Mock

import pytest
import requests

import dynamix_manager.plugin as plugin
from dynamix_manager.plugin import APP_URI, Connection, TenantSession, create_server, display_text


VALUES = {"TDX_BASE_URL": "https://example.test/TDWebApi", "TDX_APP_ID": "2045",
          "WORKBENCH_PERSONAL_TOKEN": "private-token"}
LOGIN_VALUES = {"TDX_BASE_URL": "https://example.test/TDWebApi", "TDX_APP_ID": "2045",
                "WORKBENCH_PERSONAL_USERNAME": "user", "WORKBENCH_PERSONAL_PASSWORD": "private-password"}


def connection(values=None):
    client = Mock()
    client.authenticate.return_value = "login-token"
    client.fetch_applications.return_value = [{"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"}]
    client.list_ticketing_applications.side_effect = lambda apps: apps
    return Connection(values=values or VALUES, client=client)


def server_for(c, **kwargs):
    return create_server(connection_provider=lambda: c, **kwargs)


def call(server, name, arguments=None):
    result = asyncio.run(server.call_tool(name, arguments or {}))
    return result[1] if isinstance(result, tuple) else result


def test_connection_uses_supplied_token_and_discovers_ticket_app_separately():
    c = connection()
    server = server_for(c)
    assert call(server, "connection_status")["ticket_app_id"] == 634
    assert call(server, "connection_status")["connected"]
    assert c.header_app_id == "2045"
    c.client.authenticate.assert_not_called()
    assert "private" not in json.dumps(call(server, "connection_status"))


def test_ready_never_authenticates_without_a_personal_token():
    c = connection(LOGIN_VALUES)
    assert c.auth_mode == "user"
    with pytest.raises(RuntimeError, match="token"):
        c.ready()
    c.client.authenticate.assert_not_called()
    c.client.fetch_applications.assert_not_called()


def test_application_discovery_is_cached_per_tenant_until_ttl(monkeypatch):
    first, second = connection(), connection()
    assert first.app_id == 634
    assert second.app_id == 634
    first.client.fetch_applications.assert_called_once()
    second.client.fetch_applications.assert_not_called()

    other = connection({**VALUES, "TDX_BASE_URL": "https://other.test/TDWebApi"})
    assert other.app_id == 634
    other.client.fetch_applications.assert_called_once()

    monkeypatch.setattr(plugin.time, "monotonic", lambda: 10 ** 9)
    stale = connection()
    assert stale.app_id == 634
    stale.client.fetch_applications.assert_called_once()


def test_capability_provider_controls_read_only_status():
    local = server_for(connection())
    assert local.instructions.startswith("Read-only")
    assert call(local, "connection_status")["read_only"] is True

    hosted = server_for(
        connection(),
        instructions="Hosted personal connector.",
        capability_provider=lambda: {"read_only": False, "write_available": True},
    )
    assert hosted.instructions == "Hosted personal connector."
    status = call(hosted, "connection_status")
    assert status["read_only"] is False
    assert status["write_available"] is True


def test_server_requires_a_per_request_connection_provider():
    with pytest.raises(TypeError):
        create_server()


@pytest.mark.parametrize("url", ["http://example.test/TDWebApi", "https://user:secret@example.test/TDWebApi",
                               "https://example.test/TDWebApi?token=secret", "https://example.test/other"])
def test_rejects_unsafe_tenant_config(url):
    with pytest.raises(ValueError):
        connection({**VALUES, "TDX_BASE_URL": url})


def test_missing_credentials_are_rejected_at_construction():
    with pytest.raises(ValueError, match="credentials"):
        connection({"TDX_BASE_URL": VALUES["TDX_BASE_URL"], "TDX_APP_ID": "2045"})


def test_admin_my_queue_does_not_impersonate_a_user():
    c = connection({**LOGIN_VALUES, "WORKBENCH_PERSONAL_USERNAME": "00000000-0000-0000-0000-000000000001",
                    "WORKBENCH_PERSONAL_PASSWORD": "00000000-0000-0000-0000-000000000002"})
    c.token = "admin-token"
    with pytest.raises(Exception, match="personal login"):
        call(server_for(c), "my_queue")
    c.client.search_tickets.assert_not_called()


def test_search_limits_results_and_marks_incomplete():
    c = connection()
    c.client.search_tickets.return_value = [{"ID": i, "Title": "Test"} for i in range(5)]
    result = call(server_for(c), "search_tickets", {"query": "wifi", "limit": 2, "status_ids": [1]})
    assert len(result["tickets"]) == 2
    assert result["complete"] is False
    assert "/Apps/634/" in result["tickets"][0]["url"]
    c.client.search_tickets.assert_called_once_with(
        "private-token", {"SearchText": "wifi", "MaxResults": 2, "StatusIDs": [1]}, 634, max_attempts=1)


@pytest.mark.parametrize("arguments", [{"limit": 0}, {"limit": 101}, {"status_ids": [-1]}, {"query": "x" * 501}])
def test_mcp_rejects_invalid_search_arguments_before_network(arguments):
    c = connection()
    with pytest.raises(Exception):
        call(server_for(c), "search_tickets", arguments)
    c.client.fetch_applications.assert_not_called()


def test_ambiguous_app_discovery_fails_closed():
    c = connection()
    c.client.fetch_applications.return_value *= 2
    with pytest.raises(RuntimeError, match="uniquely"):
        c.ready()


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_http_failures_are_sanitized_and_redirects_disabled(monkeypatch, status):
    def request(self, method, url, **kwargs):
        assert kwargs["allow_redirects"] is False
        response = requests.Response()
        response.status_code = status
        response._content = b"private-password private-token"
        return response
    monkeypatch.setattr(requests.Session, "request", request)
    with pytest.raises(RuntimeError) as error:
        TenantSession(VALUES["TDX_BASE_URL"]).get(VALUES["TDX_BASE_URL"] + "/api/applications")
    assert "private" not in str(error.value)


def test_authenticated_requests_cannot_leave_tenant():
    with pytest.raises(RuntimeError, match="outside"):
        TenantSession(VALUES["TDX_BASE_URL"]).get("https://other.test/api/applications")


def test_read_tools_and_ui_resource_without_credentials():
    server = create_server(connection_provider=lambda: None)

    async def check():
        tools = await server.list_tools()
        assert len(tools) == 9
        assert all(t.annotations.readOnlyHint for t in tools)
        by_name = {t.name: t for t in tools}
        # Only the on-demand card tool renders the widget; lookups stay text-only.
        assert by_name["show_tickets"].meta["ui"]["resourceUri"] == APP_URI
        assert by_name["show_tickets"].meta["openai/outputTemplate"] == APP_URI
        for name in ("search_tickets", "my_queue", "get_ticket", "ticket_feed"):
            assert "openai/outputTemplate" not in (by_name[name].meta or {}), name
            assert "resourceUri" not in ((by_name[name].meta or {}).get("ui") or {}), name
        # The widget's drill-down still needs to call these two from inside the card view.
        for name in ("get_ticket", "ticket_feed"):
            assert by_name[name].meta["openai/widgetAccessible"] is True
            assert "app" in by_name[name].meta["ui"]["visibility"]
        resource = await server.read_resource(APP_URI)
        assert resource[0].mime_type == "text/html;profile=mcp-app"
        assert "TeamDynamix" in resource[0].content
    asyncio.run(check())


def test_detail_and_activity_provide_readable_text_and_ticket_identity():
    c = connection()
    c.client.get_ticket.return_value = {"ID": 42, "Description": "<p>Hello &amp; welcome</p><script>bad()</script>"}
    c.client.get_ticket_feed.return_value = [{"Body": "<p>Reply</p>", "IsPrivate": True}]
    server = server_for(c)
    assert call(server, "get_ticket", {"ticket_id": 42})["description_text"] == "Hello & welcome"
    feed = call(server, "ticket_feed", {"ticket_id": 42})
    assert feed["ticket_id"] == 42
    assert feed["items"][0]["body_text"] == "Reply"
    assert feed["items"][0]["IsPrivate"] is True
    assert not feed["complete"]


def test_rich_text_preserves_paragraphs_without_active_content():
    assert display_text('<p>First</p><p>Second<br>Third</p><style>bad</style>') == "First\nSecond\nThird"


ALAN = {"UID": "aaaaaaaa-0000-4000-8000-000000000001", "FullName": "Alan McCain",
        "PrimaryEmail": "mccaina@cedarville.edu", "UserName": "mccaina", "IsActive": True}
OTHER_ALAN = {"UID": "aaaaaaaa-0000-4000-8000-000000000002", "FullName": "Alan McCainster",
              "PrimaryEmail": "mccainster@cedarville.edu", "UserName": "mccainster", "IsActive": True}


def people_lookup(c, results):
    """Route the connection's people lookups to fixed results; record the search text."""
    searches = []

    def get(url, **kwargs):
        assert url == c.base_url + "/api/people/lookup"
        searches.append((kwargs["params"]["searchText"], kwargs["params"]["maxResults"]))
        return Mock(json=lambda: results)

    c.client.session.get.side_effect = get
    return searches


def test_search_maps_every_filter_onto_the_api_and_reports_completeness():
    c = connection()
    c.client.search_tickets.return_value = [{"ID": 1, "Title": "One"}]
    result = call(server_for(c), "search_tickets", {
        "query": "chassis", "ticket_id": 30605254, "status_ids": [30794], "status_classes": [1, 2],
        "is_on_hold": False, "responsible_uids": [ALAN["UID"]], "responsible_group_ids": [14405],
        "requestor_uids": [ALAN["UID"]], "priority_ids": [7], "type_ids": [3], "service_ids": [9],
        "account_ids": [11], "form_ids": [13], "created_from": "2026-08-01", "created_to": "2026-08-31T23:59:59Z",
        "modified_from": "2026-08-01T00:00:00", "closed_to": "2026-09-01", "days_old_from": 1, "days_old_to": 90,
        "limit": 10,
    })
    payload = c.client.search_tickets.call_args.args[1]
    assert payload == {
        "SearchText": "chassis", "MaxResults": 10, "TicketID": 30605254, "StatusIDs": [30794],
        "StatusClassIDs": [1, 2], "IsOnHold": False, "PrimaryResponsibilityUids": [ALAN["UID"]],
        "PrimaryResponsibilityGroupIDs": [14405], "RequestorUids": [ALAN["UID"]], "PriorityIDs": [7],
        "TypeIDs": [3], "ServiceIDs": [9], "AccountIDs": [11], "FormIDs": [13],
        "CreatedDateFrom": "2026-08-01", "CreatedDateTo": "2026-08-31T23:59:59Z",
        "ModifiedDateFrom": "2026-08-01T00:00:00", "ClosedDateTo": "2026-09-01",
        "DaysOldFrom": 1, "DaysOldTo": 90,
    }
    assert result["returned"] == 1 and result["complete"] is True
    assert result["resolved_people"] == []
    c.client.search_tickets.return_value = [{"ID": i, "Title": "x"} for i in range(10)]
    assert call(server_for(c), "search_tickets", {"limit": 10})["complete"] is False


@pytest.mark.parametrize("arguments", [
    {"created_from": "yesterday"}, {"closed_to": "2026-13-01"}, {"status_classes": [7]},
    {"responsible_uids": ["not-a-uid"]}, {"days_old_to": -1}, {"requestor": "x" * 101},
])
def test_search_rejects_invalid_filters_before_any_network_call(arguments):
    c = connection()
    with pytest.raises(Exception):
        call(server_for(c), "search_tickets", arguments)
    c.client.search_tickets.assert_not_called()
    c.client.session.get.assert_not_called()


def test_search_by_person_resolves_an_exact_name_through_the_people_api_and_reflects_it_back():
    c = connection()
    searches = people_lookup(c, [OTHER_ALAN, ALAN])
    c.client.search_tickets.return_value = [{"ID": 30605254, "Title": "SHACK Dell chassis support contract"}]
    result = call(server_for(c), "search_tickets", {"requestor": "Alan McCain", "limit": 5})
    assert searches == [("Alan McCain", 25)]
    payload = c.client.search_tickets.call_args.args[1]
    assert payload["RequestorUids"] == [ALAN["UID"]] and "SearchText" not in payload
    assert result["resolved_people"] == [{
        "role": "requestor", "search": "Alan McCain", "matched": [
            {"uid": ALAN["UID"], "name": "Alan McCain", "email": "mccaina@cedarville.edu"}],
    }]
    assert result["returned"] == 1


def test_search_by_person_accepts_email_or_username_and_resolves_responsible_people():
    c = connection()
    people_lookup(c, [OTHER_ALAN, ALAN])
    c.client.search_tickets.return_value = []
    for search in ("mccaina@cedarville.edu", "MCCAINA", "alan mccain"):
        call(server_for(c), "search_tickets", {"responsible": search})
        assert c.client.search_tickets.call_args.args[1]["PrimaryResponsibilityUids"] == [ALAN["UID"]]


def test_search_by_person_uses_a_single_partial_match_but_never_guesses_between_several():
    c = connection()
    people_lookup(c, [OTHER_ALAN])
    c.client.search_tickets.return_value = []
    single = call(server_for(c), "search_tickets", {"requestor": "McCainst"})
    assert c.client.search_tickets.call_args.args[1]["RequestorUids"] == [OTHER_ALAN["UID"]]
    assert single["resolved_people"][0]["matched"][0]["email"] == "mccainster@cedarville.edu"

    c = connection()
    people_lookup(c, [OTHER_ALAN, ALAN])
    ambiguous = call(server_for(c), "search_tickets", {"requestor": "McCain"})
    c.client.search_tickets.assert_not_called()
    assert ambiguous["tickets"] == [] and ambiguous["complete"] is True
    assert [m["email"] for m in ambiguous["resolved_people"][0]["matched"]] == [
        "mccainster@cedarville.edu", "mccaina@cedarville.edu"]
    assert "ambiguous" in ambiguous["warning"].lower()

    c = connection()
    people_lookup(c, [])
    nobody = call(server_for(c), "search_tickets", {"requestor": "Nobody Here"})
    c.client.search_tickets.assert_not_called()
    assert nobody["tickets"] == [] and nobody["resolved_people"][0]["matched"] == []
    assert "no person" in nobody["warning"].lower()


def test_search_by_person_skips_inactive_accounts_and_never_returns_private_fields():
    c = connection()
    people_lookup(c, [{**ALAN, "IsActive": False, "HomePhone": "555-0100"}, {**OTHER_ALAN, "FullName": "Alan McCain"}])
    c.client.search_tickets.return_value = []
    result = call(server_for(c), "search_tickets", {"requestor": "Alan McCain"})
    assert c.client.search_tickets.call_args.args[1]["RequestorUids"] == [OTHER_ALAN["UID"]]
    assert "555-0100" not in json.dumps(result)
    assert set(result["resolved_people"][0]["matched"][0]) == {"uid", "name", "email"}


def test_my_queue_filters_by_status_class_on_the_server_without_fetching_statuses():
    c = connection()
    c.client.session.get.return_value.json.return_value = {"UID": ALAN["UID"]}
    c.client.search_tickets.return_value = [{"ID": 1, "Title": "Mine"}]
    result = call(server_for(c), "my_queue", {"limit": 5})
    c.client.fetch_ticket_statuses.assert_not_called()
    payload = c.client.search_tickets.call_args.args[1]
    assert payload == {"MaxResults": 5, "StatusClassIDs": [1, 2, 5, 6], "PrimaryResponsibilityUids": [ALAN["UID"]]}
    assert result["returned"] == 1 and result["complete"] is True


def test_search_by_username_matches_the_email_local_part_when_tdx_omits_usernames():
    """Live lookups return an empty UserName, so 'mccaina' must resolve via mccaina@cedarville.edu."""
    alise = {"UID": "aaaaaaaa-0000-4000-8000-000000000003", "FullName": "Alise McCain",
             "PrimaryEmail": "mccainaa@cedarville.edu", "UserName": "", "IsActive": True}
    c = connection()
    people_lookup(c, [alise, {**ALAN, "UserName": ""}])
    c.client.search_tickets.return_value = []
    result = call(server_for(c), "search_tickets", {"requestor": "mccaina"})
    assert c.client.search_tickets.call_args.args[1]["RequestorUids"] == [ALAN["UID"]]
    assert [m["email"] for m in result["resolved_people"][0]["matched"]] == ["mccaina@cedarville.edu"]


def test_show_tickets_renders_known_ids_and_reports_missing_ones():
    c = connection()
    tickets = {1: {"ID": 1, "Title": "First", "StatusName": "Open"}, 3: {"ID": 3, "Title": "Third", "StatusName": "Closed"}}

    def get_ticket(ticket_id, token, app_id, max_attempts):
        if ticket_id not in tickets:
            raise RuntimeError("TeamDynamix request failed (HTTP 404).")
        return dict(tickets[ticket_id])

    c.client.get_ticket.side_effect = get_ticket
    result = call(server_for(c), "show_tickets", {"ticket_ids": [1, 2, 3, 1]})
    assert [t["ID"] for t in result["tickets"]] == [1, 3]
    assert result["tickets"][0]["url"].endswith("TicketID=1") and result["tickets"][0]["StatusName"] == "Open"
    assert result["returned"] == 2 and result["missing"] == [2] and result["complete"] is True
    assert "2" in result["warning"]
    assert c.client.get_ticket.call_count == 3
    c.client.search_tickets.assert_not_called()


@pytest.mark.parametrize("arguments", [{"ticket_ids": []}, {"ticket_ids": [0]}, {"ticket_ids": list(range(1, 12))}, {}])
def test_show_tickets_is_bounded(arguments):
    c = connection()
    with pytest.raises(Exception):
        call(server_for(c), "show_tickets", arguments)
    c.client.get_ticket.assert_not_called()
