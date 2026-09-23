"""Ticket reads across the tenant's ticketing applications (default: InfoTech Tickets)."""
import asyncio
import json
from unittest.mock import Mock

import pytest

from dynamix_manager.plugin import Connection, create_server

VALUES = {"TDX_BASE_URL": "https://example.test/TDWebApi", "TDX_APP_ID": "634",
          "WORKBENCH_PERSONAL_TOKEN": "private-token"}
APPS = [
    {"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"},
    {"AppID": 1072, "Name": "CTL Tickets", "AppClass": "TDTickets"},
    {"AppID": 1729, "Name": "Operations WorkOrders", "AppClass": "TDTickets"},
    {"AppID": 928, "Name": "InfoTech Assets/CIs", "AppClass": "TDAssets"},
]


def ticket(ticket_id, app_id, title="T", modified="2026-09-01T00:00:00Z"):
    return {"ID": ticket_id, "AppID": app_id, "Title": title, "StatusID": 1, "StatusName": "New", "PriorityName": "Low",
            "ResponsibleFullName": None, "ResponsibleGroupName": None, "CreatedDate": "2026-09-01T00:00:00Z",
            "ModifiedDate": modified, "EndDate": None, "Description": "<p>d</p>"}


def connection():
    client = Mock()
    client.fetch_applications.return_value = [dict(a) for a in APPS]
    client.list_ticketing_applications.side_effect = lambda apps: [a for a in apps if a.get("AppClass") == "TDTickets"]
    client.fetch_ticket_statuses.side_effect = lambda token, app_id: [{"ID": app_id * 10, "Name": "New", "AppID": app_id}]
    tickets = {(634, 1): ticket(1, 634, "IT one"), (1072, 2): ticket(2, 1072, "CTL two", "2026-09-05T00:00:00Z"), (1729, 3): ticket(3, 1729, "Ops three")}

    def get_ticket(ticket_id, token, app_id, max_attempts=5):
        try:
            return dict(tickets[(app_id, ticket_id)])
        except KeyError:
            raise RuntimeError("not found") from None

    def search(token, payload, app_id, max_attempts=5):
        return [dict(t) for (app, _), t in tickets.items() if app == app_id]

    client.get_ticket.side_effect = get_ticket
    client.search_tickets.side_effect = search
    client.get_ticket_feed.side_effect = lambda ticket_id, token, app_id: [{"ID": 9, "Body": f"<p>feed {app_id}</p>"}]
    c = Connection(values=VALUES, client=client)
    c.routes, c.calls = {}, []

    def request(method, url, **kwargs):
        c.calls.append((method, url.removeprefix(c.base_url), kwargs))
        value = c.routes[(method, url.removeprefix(c.base_url))]
        if isinstance(value, Exception):
            raise value
        return Mock(json=lambda: json.loads(json.dumps(value)))

    client.session.get.side_effect = lambda url, **kw: request("GET", url, **kw)
    client.session.post.side_effect = lambda url, **kw: request("POST", url, **kw)
    return c


def call(c, name, arguments=None):
    result = asyncio.run(create_server(connection_provider=lambda: c).call_tool(name, arguments or {}))
    return result[1] if isinstance(result, tuple) else result


def test_connection_lists_ticketing_applications_and_resolves_by_id_or_name():
    c = connection()
    assert c.ticketing_applications() == [{"AppID": 634, "Name": "InfoTech Tickets"}, {"AppID": 1072, "Name": "CTL Tickets"},
                                          {"AppID": 1729, "Name": "Operations WorkOrders"}]
    assert c.resolve_ticket_app(None) == (634, "InfoTech Tickets")
    assert c.resolve_ticket_app(1072) == (1072, "CTL Tickets")
    assert c.resolve_ticket_app("ctl tickets") == (1072, "CTL Tickets")
    assert c.resolve_ticket_app("operations") == (1729, "Operations WorkOrders")
    for bad in (999, "tickets", "MarCom"):
        with pytest.raises(RuntimeError, match="ticketing application"):
            c.resolve_ticket_app(bad)
    status = call(c, "connection_status")
    assert status["ticketing_applications"] == [{"AppID": 634, "Name": "InfoTech Tickets"}, {"AppID": 1072, "Name": "CTL Tickets"},
                                                {"AppID": 1729, "Name": "Operations WorkOrders"}]


def test_ticket_summary_links_to_the_ticket_s_own_application():
    c = connection()
    assert c.ticket_summary(ticket(2, 1072))["url"] == "https://example.test/TDNext/Apps/1072/Tickets/TicketDet.aspx?TicketID=2"
    assert c.ticket_summary({"ID": 5, "Title": "no app"})["url"].startswith("https://example.test/TDNext/Apps/634/")


def test_ticket_statuses_and_search_accept_an_application():
    c = connection()
    assert call(c, "ticket_statuses")["statuses"][0]["AppID"] == 634
    by_name = call(c, "ticket_statuses", {"app": "CTL Tickets"})
    assert by_name["statuses"][0]["AppID"] == 1072 and by_name["application"] == {"AppID": 1072, "Name": "CTL Tickets"}
    result = call(c, "search_tickets", {"query": "x", "app": 1729})
    assert [t["ID"] for t in result["tickets"]] == [3] and result["application"] == {"AppID": 1729, "Name": "Operations WorkOrders"}
    assert c.client.search_tickets.call_args.args[2] == 1729


def test_search_and_queue_across_all_applications_merge_newest_first():
    c = connection()
    c.routes[("GET", "/api/auth/getuser")] = {"UID": "aaaaaaaa-0000-4000-8000-000000000001"}
    result = call(c, "search_tickets", {"query": "x", "app": "all", "limit": 2})
    assert [t["ID"] for t in result["tickets"]] == [2, 1]  # sorted by ModifiedDate desc, then cut to the limit
    assert result["returned"] == 2 and result["complete"] is False and result["application"] == "all"
    assert result["applications_searched"] == ["InfoTech Tickets", "CTL Tickets", "Operations WorkOrders"]
    queue = call(c, "my_queue", {"app": "all"})
    assert [t["ID"] for t in queue["tickets"]] == [2, 1, 3] and queue["complete"] is True
    assert {call.args[2] for call in c.client.search_tickets.call_args_list} == {634, 1072, 1729}


def test_get_ticket_falls_back_to_the_cross_application_lookup():
    c = connection()
    default = call(c, "get_ticket", {"ticket_id": 1})
    assert default["detail"]["ID"] == 1 and default["application"] == {"AppID": 634, "Name": "InfoTech Tickets"}
    c.routes[("GET", "/api/tickets/2")] = ticket(2, 1072, "CTL two")
    found = call(c, "get_ticket", {"ticket_id": 2})
    assert found["detail"]["AppID"] == 1072 and found["application"] == {"AppID": 1072, "Name": "CTL Tickets"}
    assert found["tickets"][0]["url"].startswith("https://example.test/TDNext/Apps/1072/")
    explicit = call(c, "get_ticket", {"ticket_id": 3, "app": "Operations WorkOrders"})
    assert explicit["detail"]["ID"] == 3
    c.routes[("GET", "/api/tickets/4")] = {}
    with pytest.raises(Exception, match="not found"):
        call(c, "get_ticket", {"ticket_id": 4})


def test_feed_and_cards_follow_the_ticket_to_its_application():
    c = connection()
    c.routes[("GET", "/api/tickets/2")] = ticket(2, 1072, "CTL two")
    feed = call(c, "ticket_feed", {"ticket_id": 2})
    assert feed["items"][0]["body_text"] == "feed 1072" and feed["application"] == {"AppID": 1072, "Name": "CTL Tickets"}
    cards = call(c, "show_tickets", {"ticket_ids": [1, 2, 4]})
    assert [t["ID"] for t in cards["tickets"]] == [1, 2] and cards["missing"] == [4]
    assert cards["tickets"][1]["url"].startswith("https://example.test/TDNext/Apps/1072/")


def test_ticket_contacts_slas_and_workflow_reads():
    c = connection()
    c.routes[("GET", "/api/634/tickets/1/contacts")] = [{"UID": "u1", "FullName": "Payable Accounts", "PrimaryEmail": "ap@example.test",
                                                         "Title": "", "DefaultAccountName": "None", "HomePhone": "secret"}]
    contacts = call(c, "ticket_contacts", {"ticket_id": 1})
    assert contacts["contacts"] == [{"UID": "u1", "FullName": "Payable Accounts", "PrimaryEmail": "ap@example.test", "Title": "", "DefaultAccountName": "None"}]
    assert contacts["application"]["AppID"] == 634
    c.routes[("GET", "/api/1072/tickets/slas")] = [{"ID": 1095, "Name": "7 day", "IsActive": True, "ResponseDurationMinutes": 60,
                                                    "ResolutionDurationMinutes": 10080, "Description": "d", "ShouldUseOperationalHours": True}]
    slas = call(c, "ticket_slas", {"app": "CTL"})
    assert slas["slas"][0] == {"ID": 1095, "Name": "7 day", "Description": "d", "ResponseDurationMinutes": 60, "ResolutionDurationMinutes": 10080,
                               "ShouldUseOperationalHours": True}
    c.routes[("GET", "/api/634/tickets/1/workflow")] = {"ID": 77, "Name": "Purchase approval", "Status": 1, "IsComplete": False,
                                                        "CurrentStepIDs": ["step-2"], "Steps": [
                                                            {"ID": "step-1", "Name": "Request", "IsCurrent": False, "TypeName": "Start"},
                                                            {"ID": "step-2", "Name": "Manager approval", "IsCurrent": True, "TypeName": "Approval"}],
                                                        "History": [{"ActionName": "Submitted", "PersonFullName": "A", "ActionDateUtc": "2026-09-01T00:00:00Z",
                                                                     "StepID": "step-1", "Comments": "<p>hi</p>", "PersonUid": "x"}]}
    c.routes[("GET", "/api/634/tickets/1/workflow/actions")] = [{"ID": "act-approve", "Name": "Approve", "Tooltip": "t"}]
    wf = call(c, "ticket_workflow", {"ticket_id": 1})
    assert wf["workflow"]["Name"] == "Purchase approval" and [s["ID"] for s in wf["steps"]] == ["step-1", "step-2"]
    assert wf["current_steps"] == [{"ID": "step-2", "Name": "Manager approval", "actions": [{"ID": "act-approve", "Name": "Approve", "Tooltip": "t"}]}]
    assert c.calls[-1][2]["params"] == {"stepId": "step-2"}
    assert wf["history"] == [{"ActionName": "Submitted", "PersonFullName": "A", "ActionDateUtc": "2026-09-01T00:00:00Z", "StepID": "step-1", "comments_text": "hi"}]
    c.routes[("GET", "/api/1729/tickets/3/workflow")] = RuntimeError("404")
    c.routes[("GET", "/api/tickets/3")] = ticket(3, 1729)
    none = call(c, "ticket_workflow", {"ticket_id": 3})
    assert none["workflow"] is None and none["application"]["AppID"] == 1729


def test_ticket_feed_expands_replies_for_entries_that_have_them():
    c = connection()
    c.client.get_ticket_feed.side_effect = lambda ticket_id, token, app_id: [
        {"ID": 9, "Body": "<p>Question</p>", "RepliesCount": 2, "Replies": []},
        {"ID": 10, "Body": "<p>No replies</p>", "RepliesCount": 0, "Replies": []}]
    c.routes[("GET", "/api/feed/9")] = {"ID": 9, "Body": "<p>Question</p>", "RepliesCount": 2, "Replies": [
        {"ID": 91, "Body": "<p>First <b>answer</b></p>", "CreatedFullName": "Alan McCain", "CreatedDate": "2026-09-01T00:00:00Z", "CreatedUid": "u"},
        {"ID": 92, "Body": "Thanks", "CreatedFullName": "Micah Cooper", "CreatedDate": "2026-09-02T00:00:00Z", "CreatedUid": "v"}]}
    feed = call(c, "ticket_feed", {"ticket_id": 1})
    assert feed["items"][0]["replies"] == [
        {"ID": 91, "CreatedFullName": "Alan McCain", "CreatedDate": "2026-09-01T00:00:00Z", "body_text": "First answer"},
        {"ID": 92, "CreatedFullName": "Micah Cooper", "CreatedDate": "2026-09-02T00:00:00Z", "body_text": "Thanks"}]
    assert feed["items"][1]["replies"] == []
    assert "not expanded" not in feed["warning"]
    assert sum(1 for m, p, _ in c.calls if p == "/api/feed/9") == 1 and not any(p == "/api/feed/10" for _, p, _ in c.calls)
