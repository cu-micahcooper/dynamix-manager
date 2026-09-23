import asyncio
import json
from unittest.mock import Mock

import pytest

from dynamix_manager.plugin import Connection, create_server

VALUES = {"TDX_BASE_URL": "https://example.test/TDWebApi", "TDX_APP_ID": "634",
          "WORKBENCH_PERSONAL_TOKEN": "private-token"}
APPS = [{"AppID": 634, "Name": "InfoTech Tickets", "AppClass": "TDTickets"}]
INFO = {"ID": 100482, "Name": "Survey Responses", "CreatedFullName": "Micah Cooper", "CreatedUid": "u", "CreatedDate": "2024-01-01T00:00:00Z",
        "OwningGroupID": None, "OwningGroupName": None, "PlatformAppID": 0, "PlatformAppName": "None", "ReportSourceID": 12,
        "ReportSourceName": "Survey Response", "SystemAppName": "TDSurveys", "Uri": "api/reports/100482"}
REPORT = {**INFO, "Description": "Information on surveys", "MaxResults": 50000, "ChartType": "None", "SortOrder": [],
          "DisplayedColumns": [{"HeaderText": "Ticket ID", "ColumnName": "TicketID", "DataType": 2},
                               {"HeaderText": "Item Title", "ColumnName": "ItemTitle", "DataType": 1},
                               {"HeaderText": "Effort", "ColumnName": "168242", "DataType": 1}],
          "DataRows": [{"TicketID": 1, "ItemTitle": "<b>Printer</b>", "168242": "Easy", "Hidden": "x"},
                       {"TicketID": 2, "ItemTitle": "Wifi", "168242": "Hard", "Hidden": "y"},
                       {"TicketID": 3, "ItemTitle": "VPN", "168242": None, "Hidden": "z"}]}


def connection():
    client = Mock()
    client.fetch_applications.return_value = [dict(a) for a in APPS]
    client.list_ticketing_applications.side_effect = lambda apps: [a for a in apps if a.get("AppClass") == "TDTickets"]
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


def test_list_reports_searches_server_side_and_projects_report_info():
    c = connection()
    c.routes[("POST", "/api/reports/search")] = [INFO, {**INFO, "ID": 154778, "Name": "Surveys Over Last Week ISS", "PlatformAppName": "InfoTech Tickets"}]
    result = call(c, "list_reports", {"search": "survey", "app_id": 634, "limit": 5})
    assert c.calls[-1][2]["json"] == {"SearchText": "survey", "ForAppID": 634}
    assert result["returned"] == 2 and result["complete"] is True and result["resolved_people"] == []
    assert result["reports"][0] == {"ID": 100482, "Name": "Survey Responses", "PlatformAppName": "None", "ReportSourceName": "Survey Response",
                                    "CreatedFullName": "Micah Cooper", "OwningGroupName": None, "CreatedDate": "2024-01-01T00:00:00Z"}
    c.routes[("GET", "/api/people/lookup")] = [{"UID": "aaaaaaaa-0000-4000-8000-000000000001", "FullName": "Micah Cooper",
                                                "PrimaryEmail": "micahcooper@example.test", "IsActive": True}]
    result = call(c, "list_reports", {"owner": "Micah Cooper"})
    assert c.calls[-1][2]["json"] == {"OwnerUid": "aaaaaaaa-0000-4000-8000-000000000001"}
    assert result["resolved_people"][0]["matched"][0]["email"] == "micahcooper@example.test"
    c.routes[("GET", "/api/people/lookup")] = []
    result = call(c, "list_reports", {"owner": "Nobody"})
    assert result["reports"] == [] and "No report search was run" in result["warning"]


def test_run_report_returns_columns_and_rows_keyed_by_header_with_a_limit():
    c = connection()
    c.routes[("GET", "/api/reports/100482")] = REPORT
    result = call(c, "run_report", {"report_id": 100482, "limit": 2})
    method, path, kwargs = c.calls[-1]
    assert kwargs["params"] == {"withData": "true"}
    assert result["report"] == {"ID": 100482, "Name": "Survey Responses", "Description": "Information on surveys", "PlatformAppName": "None",
                                "ReportSourceName": "Survey Response", "CreatedFullName": "Micah Cooper", "MaxResults": 50000}
    assert result["columns"] == [{"header": "Ticket ID", "column": "TicketID", "data_type": 2},
                                 {"header": "Item Title", "column": "ItemTitle", "data_type": 1},
                                 {"header": "Effort", "column": "168242", "data_type": 1}]
    assert result["rows"] == [{"Ticket ID": 1, "Item Title": "Printer", "Effort": "Easy"}, {"Ticket ID": 2, "Item Title": "Wifi", "Effort": "Hard"}]
    assert result["returned"] == 2 and result["total_rows"] == 3 and result["complete"] is False
    assert "Hidden" not in json.dumps(result)


def test_run_report_passes_a_validated_sort_expression_and_fails_closed():
    c = connection()
    c.routes[("GET", "/api/reports/100482")] = REPORT
    result = call(c, "run_report", {"report_id": 100482, "sort": "TicketID DESC"})
    assert c.calls[-1][2]["params"] == {"withData": "true", "dataSortExpression": "TicketID DESC"}
    assert result["complete"] is True and result["returned"] == 3
    with pytest.raises(Exception):
        call(c, "run_report", {"report_id": 100482, "sort": "TicketID; DROP"})
    c.routes[("GET", "/api/reports/7")] = {"ID": 8}
    with pytest.raises(Exception, match="could not be read"):
        call(c, "run_report", {"report_id": 7})
