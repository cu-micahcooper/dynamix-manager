import json
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
import requests

from dynamix_manager.ticket_writes.models import parse_action

UID = "11111111-1111-4111-8111-111111111111"
TICKET = dict(ID=1001, AppID=42, Title="Before", Description="old", PriorityID=2,
              StatusID=1, ResponsibleUid=None, ResponsibleGroupID=9, ModifiedDate="version")


def setup_adapter(**overrides):
    from dynamix_manager.ticket_writes.adapter import WriteAdapter
    records = {
        "/api/42/tickets/1001": TICKET.copy(),
        "/api/42/tickets/statuses": [dict(ID=5, Name="Ordered", IsActive=True,
                                          StatusClass=5, RequireGoesOffHold=False)],
        "/api/42/tickets/priorities": [dict(ID=7, Name="High", IsActive=True)],
        f"/api/people/{UID}": dict(UID=UID, IsActive=True, FullName="Person", PrimaryEmail="person@example.invalid", Applications=["TDPeople"], OrgApplications=[dict(ID=42, IsActive=True)]),
        "/api/groups/4": dict(ID=4, Name="Team", IsActive=True),
        "/api/groups/4/applications": [dict(AppID=42, GroupID=4)],
    }
    records.update(overrides)
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: TICKET.copy())

    adapter = WriteAdapter("https://tenant.example/TDWebApi", 42, "secret",
                           request=request, read=lambda path: records[path])
    return adapter, calls, records


def test_comment_explicit_wire_and_feed_success_without_json_schema():
    adapter, calls, _ = setup_adapter()
    prepared = adapter.validate(parse_action(dict(kind="comment", ticket_id=1001, comments="Hello")))
    adapter.request = lambda *args, **kwargs: (calls.append((args, kwargs)) or SimpleNamespace(status_code=200))
    assert adapter.apply_once(prepared).outcome == "applied"
    args, kwargs = calls[0]
    assert args == ("POST", "https://tenant.example/TDWebApi/api/42/tickets/1001/feed")
    assert kwargs["json"] == dict(Comments="Hello", IsPrivate=True, IsRichHtml=False, Notify=[], NewStatusID=0, CascadeStatus=False)
    assert kwargs["allow_redirects"] is False and kwargs["timeout"] == (5, 30)


def test_status_requires_verified_flag_but_not_all_hold_classes_rejected():
    adapter, _, records = setup_adapter()
    action = parse_action(dict(kind="status", ticket_id=1001, comments="Ordered", status_id=5))
    assert json.loads(adapter.validate(action).payload_json)["NewStatusID"] == 5
    for replacement in (True, None):
        records["/api/42/tickets/statuses"][0]["RequireGoesOffHold"] = replacement
        with pytest.raises(ValueError):
            adapter.validate(action)


def test_edit_partial_patch_preserves_description_null():
    adapter, calls, _ = setup_adapter()
    prepared = adapter.validate(parse_action(dict(kind="edit", ticket_id=1001, title="After", description=None, priority_id=7)))
    assert adapter.apply_once(prepared).outcome == "applied"
    method, url, kwargs = calls[0]
    assert method == "PATCH" and url.endswith("/api/42/tickets/1001?notifyNewResponsible=false")
    assert kwargs["json"] == [dict(op="replace", path="/Title", value="After"), dict(op="replace", path="/Description", value=None), dict(op="replace", path="/PriorityID", value=7)]
    assert prepared.preview.fields[-1].after == "High"


def test_assignment_preserves_unselected_field_and_names_preview():
    adapter, calls, _ = setup_adapter()
    prepared = adapter.validate(parse_action(dict(kind="assign", ticket_id=1001, responsible_uid=UID)))
    adapter.apply_once(prepared)
    assert calls[0][2]["json"] == [dict(op="replace", path="/ResponsibleUid", value=UID)]
    assert prepared.preview.fields[0].after == "Person"
    assert prepared.preview.fields[1].before == prepared.preview.fields[1].after


@pytest.mark.parametrize("action", [
    dict(kind="comment", ticket_id=1001, comments="Hello"),
    dict(kind="status", ticket_id=1001, comments="Done", status_id=5),
])
def test_feed_created_response_is_applied_once_without_response_body(action):
    adapter, calls, _ = setup_adapter()
    prepared = adapter.validate(parse_action(action))
    adapter.request = lambda *args, **kwargs: (calls.append((args, kwargs)) or SimpleNamespace(status_code=201))
    result = adapter.apply_once(prepared)
    assert result.outcome == "applied"
    assert result.status_code == 201
    assert len(calls) == 1
    assert calls[0][0][0] == "POST"
    assert calls[0][0][1].endswith("/feed")


def test_patch_created_response_remains_unknown():
    adapter, calls, _ = setup_adapter()
    prepared = adapter.validate(parse_action(dict(kind="edit", ticket_id=1001, title="After")))
    adapter.request = lambda *args, **kwargs: (calls.append(1) or SimpleNamespace(status_code=201))
    assert adapter.apply_once(prepared).outcome == "unknown"
    assert len(calls) == 1


@pytest.mark.parametrize("code,outcome", [(401,"rejected"),(403,"rejected"),(429,"rejected"),(500,"unknown"),(503,"unknown"),(302,"unknown"),(202,"unknown"),(204,"unknown")])
def test_one_attempt_and_classified_outcomes(code, outcome):
    adapter, calls, _ = setup_adapter()
    prepared = adapter.validate(parse_action(dict(kind="comment", ticket_id=1001, comments="Hello")))
    adapter.request = lambda *args, **kwargs: (calls.append(1) or SimpleNamespace(status_code=code))
    assert adapter.apply_once(prepared).outcome == outcome
    assert len(calls) == 1


def test_timeout_is_unknown_and_not_retried():
    adapter, calls, _ = setup_adapter()
    prepared = adapter.validate(parse_action(dict(kind="comment", ticket_id=1001, comments="Hello")))
    def timeout(*args, **kwargs):
        calls.append(1)
        raise requests.Timeout("contains secret")
    adapter.request = timeout
    result = adapter.apply_once(prepared)
    assert result.outcome == "unknown" and "secret" not in result.message
    assert len(calls) == 1


@pytest.mark.parametrize("field,value", [("AppID",99),("AppID",None),("ModifiedDate",None)])
def test_missing_or_wrong_baseline_rejected(field, value):
    adapter, _, records = setup_adapter()
    records["/api/42/tickets/1001"][field] = value
    with pytest.raises(ValueError):
        adapter.validate(parse_action(dict(kind="comment", ticket_id=1001, comments="Hello")))


def test_missing_inactive_or_wrong_app_assignee_rejected():
    adapter, _, records = setup_adapter()
    action = parse_action(dict(kind="assign", ticket_id=1001, responsible_uid=UID))
    user = records[f"/api/people/{UID}"]
    for field, value in [("IsActive",False),("OrgApplications",[]),("OrgApplications",None), ("OrgApplications",[dict(ID=42, IsActive=False)])]:
        before = user[field]
        user[field] = value
        with pytest.raises(ValueError):
            adapter.validate(action)
        user[field] = before


def test_group_notification_true_blocked_without_exact_recipient_contract():
    adapter, _, _ = setup_adapter()
    action = parse_action(dict(kind="assign", ticket_id=1001, responsible_group_id=4, notify_new_responsible=True))
    with pytest.raises(ValueError, match="recipient"):
        adapter.validate(action)


def test_unavailable_metadata_fails_closed_without_error_disclosure():
    adapter, _, _ = setup_adapter()
    def denied(path):
        raise RuntimeError("secret denied")
    adapter.read = denied
    with pytest.raises(ValueError, match="unavailable") as error:
        adapter.validate(parse_action(dict(kind="edit", ticket_id=1001, priority_id=7)))
    assert "secret" not in str(error.value)


def test_connection_preserves_client_header_separate_from_ticket_app_id():
    from dynamix_manager.ticket_writes.adapter import WriteAdapter
    connection = SimpleNamespace(base_url="https://tenant.example/TDWebApi", app_id=42,
                                 header_app_id="8", token="secret", auth_mode="user", ready=lambda: None)
    adapter = WriteAdapter.from_connection(connection)
    assert adapter.app_id == 42 and adapter._headers["X-TDClient-ID"] == "8"
    connection.auth_mode = "admin"
    with pytest.raises(ValueError):
        WriteAdapter.from_connection(connection)


def test_prepared_change_roundtrip_and_explicit_recipients():
    from dynamix_manager.ticket_writes.models import PreparedChange
    adapter, _, _ = setup_adapter()
    for value in [dict(kind="assign", ticket_id=1001, responsible_group_id=4),
                  dict(kind="edit", ticket_id=1001, description=None),
                  dict(kind="comment", ticket_id=1001, comments="Hello", is_private=False, notify=["person@example.invalid"])]:
        prepared = adapter.validate(parse_action(value))
        restored = PreparedChange.model_validate_json(prepared.model_dump_json())
        assert restored == prepared
        if value["kind"] == "comment":
            assert restored.preview.recipients == ("person@example.invalid",)
            assert restored.preview.visibility == "public"


def test_partial_edit_missing_or_unrecognized_success_is_unknown():
    adapter, _, _ = setup_adapter()
    prepared = adapter.validate(parse_action(dict(kind="edit", ticket_id=1001, title="After")))
    adapter.request = lambda *args, **kwargs: SimpleNamespace(status_code=200, json=lambda: {"ID": 2, "AppID":42})
    assert adapter.apply_once(prepared).outcome == "unknown"


@pytest.mark.parametrize("collection", ["statuses", "priorities"])
def test_inactive_or_missing_metadata_rejected(collection):
    adapter, _, records = setup_adapter()
    action = parse_action(dict(kind="status", ticket_id=1001, comments="Done", status_id=5) if collection == "statuses" else dict(kind="edit", ticket_id=1001, priority_id=7))
    records[f"/api/42/tickets/{collection}"][0]["IsActive"] = False
    with pytest.raises(ValueError):
        adapter.validate(action)
    records[f"/api/42/tickets/{collection}"] = []
    with pytest.raises(ValueError):
        adapter.validate(action)


@pytest.mark.parametrize("change", [dict(kind="assign", responsible_group_id=4), dict(kind="status", status_id=5, comments="Done")])
def test_converted_project_task_changes_rejected_before_dispatch(change):
    adapter, calls, records = setup_adapter()
    records["/api/42/tickets/1001"]["IsConvertedToTask"] = True
    with pytest.raises(ValueError, match="converted"):
        adapter.validate(parse_action(dict(ticket_id=1001, **change)))
    assert calls == []


def test_prepared_change_cannot_dispatch_to_different_tenant_with_same_app_id():
    from dynamix_manager.ticket_writes.adapter import WriteAdapter
    from dynamix_manager.ticket_writes.models import PreparedChange
    adapter, calls, _ = setup_adapter()
    prepared = adapter.validate(parse_action(dict(kind="comment", ticket_id=1001, comments="Hello")))
    restored = PreparedChange.model_validate_json(prepared.model_dump_json())
    other = WriteAdapter("https://other.example/TDWebApi", 42, "another-secret", request=adapter.request)
    assert other.apply_once(restored).outcome == "rejected"
    assert calls == []
    assert restored.base_url == "https://tenant.example/TDWebApi"


class MetadataResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def metadata_adapter(routes):
    from dynamix_manager.ticket_writes.adapter import WriteAdapter
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        value = routes[(method, url.removeprefix("https://tenant.example/TDWebApi"))]
        return value if isinstance(value, MetadataResponse) else MetadataResponse(value)

    return WriteAdapter("https://tenant.example/TDWebApi", 42, "secret", request=request), calls


def test_discovers_active_statuses_and_priorities_with_minimal_output():
    routes = {
        ("GET", "/api/42/tickets/statuses"): [
            {"ID": 5, "Name": "Done", "IsActive": True, "StatusClass": 3,
             "RequireGoesOffHold": False, "SensitiveExtra": "omit"},
            {"ID": 8, "Name": "Unsupported hold", "IsActive": True,
             "StatusClass": 5, "RequireGoesOffHold": True},
            {"ID": 6, "Name": "Old", "IsActive": False, "StatusClass": 4,
             "RequireGoesOffHold": False},
        ],
        ("GET", "/api/42/tickets/priorities"): [
            {"ID": 7, "Name": "High", "IsActive": True, "SensitiveExtra": "omit"},
        ],
    }
    adapter, calls = metadata_adapter(routes)
    assert adapter.discover_metadata("statuses", limit=10) == {
        "kind": "statuses",
        "results": [{"ID": 5, "Name": "Done", "StatusClass": 3,
                     "RequireGoesOffHold": False}],
        "returned": 1,
        "complete": False,
    }
    assert adapter.discover_metadata("priorities", limit=10)["results"] == [
        {"ID": 7, "Name": "High"}
    ]
    assert all(call[2]["timeout"] == (5, 30) and call[2]["allow_redirects"] is False
               for call in calls)


def test_people_lookup_is_encoded_bounded_and_validates_org_application_membership():
    valid = "11111111-1111-4111-8111-111111111111"
    wrong_app = "22222222-2222-4222-8222-222222222222"
    inactive = "33333333-3333-4333-8333-333333333333"
    query = urlencode({"searchText": "A&B Person", "maxResults": 50})
    routes = {
        ("GET", "/api/people/lookup?" + query): [
            {"UID": valid, "FullName": "Untrusted lookup name"},
            {"UID": wrong_app},
            {"UID": inactive},
            {"UID": "44444444-4444-4444-8444-444444444444"},
        ],
        ("GET", f"/api/people/{valid}"): {
            "UID": valid, "FullName": "Verified Person", "IsActive": True,
            "PrimaryEmail": "private@example.invalid",
            "Applications": ["TDPeople"],
            "OrgApplications": [{"ID": 42, "IsActive": True}],
        },
        ("GET", f"/api/people/{wrong_app}"): {
            "UID": wrong_app, "FullName": "Wrong app", "IsActive": True,
            "OrgApplications": [{"ID": 99, "IsActive": True}],
        },
        ("GET", f"/api/people/{inactive}"): {
            "UID": inactive, "FullName": "Inactive", "IsActive": False,
            "OrgApplications": [{"ID": 42, "IsActive": True}],
        },
        ("GET", "/api/people/44444444-4444-4444-8444-444444444444"): {
            "UID": "44444444-4444-4444-8444-444444444444", "FullName": "Customer",
            "IsActive": True, "OrgApplications": [],
        },
    }
    adapter, calls = metadata_adapter(routes)
    result = adapter.discover_metadata("people", search="A&B Person", limit=3)
    assert result == {
        "kind": "people",
        "results": [{"ID": valid, "Name": "Verified Person", "AppID": 42}],
        "returned": 1,
        "complete": False,
    }
    assert calls[0][0:2] == ("GET", "https://tenant.example/TDWebApi/api/people/lookup?" + query)
    assert len(calls) == 5
    assert "private@example.invalid" not in json.dumps(result)


def test_group_lookup_uses_exact_readonly_post_contract_and_bounded_validation():
    routes = {
        ("POST", "/api/groups/search"): [
            {"ID": 4, "Name": "Untrusted"}, {"ID": 5}, {"ID": 6},
        ],
        ("GET", "/api/groups/4"): {"ID": 4, "Name": "Verified Team", "IsActive": True},
        ("GET", "/api/groups/4/applications"): [{"AppID": 42, "GroupID": 4}],
        ("GET", "/api/groups/5"): {"ID": 5, "Name": "Wrong app", "IsActive": True},
        ("GET", "/api/groups/5/applications"): [{"AppID": 99, "GroupID": 5}],
    }
    adapter, calls = metadata_adapter(routes)
    result = adapter.discover_metadata("groups", search="Desk", limit=2)
    assert result == {
        "kind": "groups",
        "results": [{"ID": 4, "Name": "Verified Team", "AppID": 42}],
        "returned": 1,
        "complete": False,
    }
    assert calls[0][0:2] == ("POST", "https://tenant.example/TDWebApi/api/groups/search")
    assert calls[0][2]["json"] == {"NameLike": "Desk", "IsActive": True, "HasAppID": 42}
    assert "MaxResults" not in calls[0][2]["json"]
    assert len(calls) == 5


@pytest.mark.parametrize(
    ("kind", "search", "limit"),
    [("people", None, 10), ("people", "x", 10), ("groups", "x" * 101, 10),
     ("people", "  ", 10), ("groups", " x ", 10),
     ("statuses", "unexpected", 10), ("priorities", None, 0), ("unknown", None, 10)],
)
def test_metadata_lookup_rejects_unbounded_or_inapplicable_inputs(kind, search, limit):
    adapter, calls = metadata_adapter({})
    with pytest.raises(ValueError):
        adapter.discover_metadata(kind, search=search, limit=limit)
    assert calls == []


@pytest.mark.parametrize(
    "response",
    [MetadataResponse({}), MetadataResponse([], status_code=500),
     MetadataResponse(ValueError("private upstream body"))],
)
def test_metadata_lookup_malformed_or_failed_response_is_safely_redacted(response):
    adapter, calls = metadata_adapter({("GET", "/api/42/tickets/statuses"): response})
    with pytest.raises(ValueError, match="unavailable") as error:
        adapter.discover_metadata("statuses")
    assert "private" not in str(error.value)
    assert len(calls) == 1


def test_people_lookup_scans_a_wide_candidate_window_and_stops_at_limit():
    """Common surnames return many customers first; eligible technicians may sit past ``limit``."""
    customers = [f"00000000-0000-4000-8000-{i:012d}" for i in range(1, 8)]
    techs = [f"11111111-1111-4111-8111-{i:012d}" for i in range(1, 4)]
    routes = {("GET", "/api/people/lookup?" + urlencode({"searchText": "Cooper", "maxResults": 50})):
              [{"UID": uid} for uid in customers + techs]}
    for uid in customers:
        routes[("GET", f"/api/people/{uid}")] = {"UID": uid, "FullName": "Customer", "IsActive": True,
                                                  "OrgApplications": []}
    for index, uid in enumerate(techs):
        routes[("GET", f"/api/people/{uid}")] = {"UID": uid, "FullName": f"Tech {index}", "IsActive": True,
                                                  "OrgApplications": [{"ID": 42, "IsActive": True}]}
    adapter, calls = metadata_adapter(routes)
    result = adapter.discover_metadata("people", search="Cooper", limit=2)
    assert [item["Name"] for item in result["results"]] == ["Tech 0", "Tech 1"]
    assert result["returned"] == 2
    # One lookup, seven customer detail reads, then exactly two technician reads: the third is never fetched.
    assert len(calls) == 1 + len(customers) + 2


TASK = dict(ID=77, TicketID=1001, Title="Approve payment", IsActive=True, PercentComplete=0,
            CompletedDate=None, ResponsibleFullName="Micah Cooper", ModifiedDate="task-v1", TypeID=1)


def test_task_completion_validates_open_task_and_posts_percent_complete_to_task_feed():
    adapter, calls, records = setup_adapter(**{"/api/42/tickets/1001/tasks/77": TASK.copy()})
    prepared = adapter.validate(parse_action(dict(kind="task", ticket_id=1001, task_id=77)))
    assert json.loads(prepared.payload_json) == dict(IsPrivate=True, IsRichHtml=False, Notify=[], PercentComplete=100)
    baseline = json.loads(prepared.baseline_json)
    assert baseline["ticket"]["ID"] == 1001 and baseline["task"]["ModifiedDate"] == "task-v1"
    assert prepared.preview.action == "task"
    assert [(f.name, f.before, f.after) for f in prepared.preview.fields] == [
        ("Task", "Approve payment", "Approve payment"), ("PercentComplete", "0", "100")]
    adapter.request = lambda *args, **kwargs: (calls.append((args, kwargs)) or SimpleNamespace(status_code=201))
    assert adapter.apply_once(prepared).outcome == "applied"
    args, kwargs = calls[-1]
    assert args == ("POST", "https://tenant.example/TDWebApi/api/42/tickets/1001/tasks/77/feed")
    assert kwargs["json"] == dict(IsPrivate=True, IsRichHtml=False, Notify=[], PercentComplete=100)

    with_comment = adapter.validate(parse_action(dict(kind="task", ticket_id=1001, task_id=77, comments="Approved")))
    assert json.loads(with_comment.payload_json)["Comments"] == "Approved"


@pytest.mark.parametrize("change", [
    dict(IsActive=False), dict(PercentComplete=100), dict(CompletedDate="2026-09-20T00:00:00Z"),
    dict(TicketID=1002), dict(ID=78),
])
def test_task_completion_rejects_completed_inactive_or_mismatched_tasks(change):
    adapter, _, _ = setup_adapter(**{"/api/42/tickets/1001/tasks/77": {**TASK, **change}})
    with pytest.raises(ValueError):
        adapter.validate(parse_action(dict(kind="task", ticket_id=1001, task_id=77)))


def test_list_tasks_returns_bounded_minimal_projection():
    routes = {("GET", "/api/42/tickets/1001/tasks"): [
        {**TASK, "Description": "private detail", "ResponsibleEmail": "person@example.invalid"},
        {**TASK, "ID": 78, "Title": "Second", "IsActive": False, "PercentComplete": 100,
         "CompletedDate": "2026-09-19T00:00:00Z"},
        {"ID": "bad"},
    ]}
    adapter, calls = metadata_adapter(routes)
    result = adapter.list_tasks(1001, limit=1)
    assert result == {"ticket_id": 1001, "tasks": [
        {"ID": 77, "Title": "Approve payment", "IsActive": True, "PercentComplete": 0, "CompletedDate": None,
         "ResponsibleFullName": "Micah Cooper", "ResponsibleGroupName": None, "TypeID": 1},
    ], "returned": 1, "complete": False}
    assert "private detail" not in json.dumps(result) and "person@example.invalid" not in json.dumps(result)
    full = adapter.list_tasks(1001, limit=10)
    assert [t["ID"] for t in full["tasks"]] == [77, 78] and full["complete"] is True
    assert calls[0][0:2] == ("GET", "https://tenant.example/TDWebApi/api/42/tickets/1001/tasks")
    with pytest.raises(ValueError):
        adapter.list_tasks(1001, limit=0)


def test_task_completion_treats_tdx_min_date_as_not_completed():
    """Live TDX reports an incomplete task's CompletedDate as 0001-01-01T00:00:00, not null."""
    task = {**TASK, "CompletedDate": "0001-01-01T00:00:00"}
    adapter, _, _ = setup_adapter(**{"/api/42/tickets/1001/tasks/77": task})
    prepared = adapter.validate(parse_action(dict(kind="task", ticket_id=1001, task_id=77)))
    assert json.loads(prepared.payload_json)["PercentComplete"] == 100
    routes = {("GET", "/api/42/tickets/1001/tasks"): [task]}
    listing, _ = metadata_adapter(routes)
    assert listing.list_tasks(1001)["tasks"][0]["CompletedDate"] is None


CREATE_ROUTES = {
    "/api/42/tickets/types": [dict(ID=3, Name="Hardware", IsActive=True, CategoryName="Support"),
                              dict(ID=4, Name="Retired", IsActive=False, CategoryName="Support")],
    "/api/accounts/11": dict(ID=11, Name="Information Technology", IsActive=True),
    "/api/42/tickets/forms": [dict(ID=2, Name="Standard", IsActive=True, IsDefaultForApp=True)],
    "/api/42/tickets/sources": [dict(ID=6, Name="Web", IsActive=True)],
    "/api/42/services/9": dict(ID=9, Name="Classroom AV", IsActive=True),
}
CREATE = dict(kind="create", title="Replace projector", description="Room 101 projector is dead.",
              type_id=3, account_id=11, requestor_uid=UID)
CREATED = dict(ID=5555, AppID=42, Title="Replace projector", StatusName="New", PriorityName="Normal",
               ResponsibleFullName=None, ResponsibleGroupName="Team", FormName="Standard", TypeName="Hardware",
               RequestorName="Person", AccountName="Information Technology", Description="private body")


def test_create_validates_metadata_and_posts_with_fixed_safety_flags():
    adapter, calls, _ = setup_adapter(**CREATE_ROUTES)
    prepared = adapter.validate(parse_action(dict(CREATE, form_id=2, status_id=5, priority_id=7, service_id=9,
                                                  source_id=6, responsible_group_id=4)))
    assert json.loads(prepared.payload_json) == dict(
        TypeID=3, Title="Replace projector", Description="Room 101 projector is dead.", IsRichHtml=False,
        AccountID=11, RequestorUid=UID, FormID=2, StatusID=5, PriorityID=7, ServiceID=9, SourceID=6,
        ResponsibleGroupID=4)
    baseline = json.loads(prepared.baseline_json)
    assert baseline["type"] == "Hardware" and baseline["account"] == "Information Technology"
    assert baseline["requestor"] == "Person" and baseline["responsible_group"] == "Team"
    assert prepared.preview.action == "create" and prepared.preview.ticket_id == 0
    fields = {f.name: f.after for f in prepared.preview.fields}
    assert fields["Title"] == "Replace projector" and fields["Type"] == "Hardware"
    assert fields["Requestor"] == "Person" and fields["Status"] == "Ordered" and fields["ResponsibleGroupID"] == "Team"

    adapter.request = lambda *args, **kwargs: (calls.append((args, kwargs)) or
                                               SimpleNamespace(status_code=201, json=lambda: CREATED.copy()))
    result = adapter.apply_once(prepared)
    args, kwargs = calls[-1]
    assert args == ("POST", "https://tenant.example/TDWebApi/api/42/tickets")
    assert kwargs["params"] == {"EnableNotifyReviewer": "false", "NotifyRequestor": "false",
                                "NotifyResponsible": "false", "AllowRequestorCreation": "false",
                                "applyDefaults": "true"}
    assert kwargs["json"] == json.loads(prepared.payload_json)
    assert result.outcome == "applied" and result.status_code == 201
    assert result.detail == {"ticket_id": 5555, "status": "New", "priority": "Normal", "responsible": None,
                             "responsible_group": "Team", "form": "Standard", "type": "Hardware",
                             "requestor": "Person", "account": "Information Technology"}
    assert "private body" not in json.dumps(result.model_dump())


def test_create_defaults_omitted_fields_to_tenant_defaults_and_can_notify_requestor():
    adapter, calls, _ = setup_adapter(**CREATE_ROUTES)
    prepared = adapter.validate(parse_action(dict(CREATE, notify_requestor=True)))
    payload = json.loads(prepared.payload_json)
    assert set(payload) == {"TypeID", "Title", "Description", "IsRichHtml", "AccountID", "RequestorUid"}
    fields = {f.name: f.after for f in prepared.preview.fields}
    assert fields["Status"] == "Tenant default" and fields["Priority"] == "Tenant default"
    adapter.request = lambda *args, **kwargs: (calls.append((args, kwargs)) or
                                               SimpleNamespace(status_code=201, json=lambda: CREATED.copy()))
    adapter.apply_once(prepared)
    assert calls[-1][1]["params"]["NotifyRequestor"] == "true"


@pytest.mark.parametrize("change", [
    dict(type_id=4), dict(type_id=99), dict(account_id=12), dict(form_id=9), dict(status_id=99),
    dict(priority_id=99), dict(source_id=99), dict(service_id=99), dict(responsible_group_id=99),
    dict(requestor_uid="22222222-2222-4222-8222-222222222222"),
])
def test_create_rejects_unverified_or_inactive_metadata(change):
    adapter, _, _ = setup_adapter(**CREATE_ROUTES)
    with pytest.raises(ValueError):
        adapter.validate(parse_action(dict(CREATE, **change)))


@pytest.mark.parametrize("status,body,outcome", [
    (201, CREATED, "applied"), (200, CREATED, "applied"),
    (201, dict(ID=5555, AppID=99), "unknown"), (201, "not json", "unknown"),
    (400, None, "rejected"), (403, None, "rejected"), (500, None, "unknown"),
])
def test_create_outcomes_require_a_verified_created_ticket(status, body, outcome):
    adapter, _, _ = setup_adapter(**CREATE_ROUTES)
    prepared = adapter.validate(parse_action(CREATE))

    def json_body():
        if body == "not json":
            raise ValueError("no json")
        return body

    adapter.request = lambda *args, **kwargs: SimpleNamespace(status_code=status, json=json_body)
    result = adapter.apply_once(prepared)
    assert result.outcome == outcome
    assert (result.detail or {}).get("ticket_id") == (5555 if outcome == "applied" else None)


def test_discover_create_metadata_is_bounded_and_uses_account_search():
    routes = {
        ("GET", "/api/42/tickets/types"): CREATE_ROUTES["/api/42/tickets/types"] + [{"ID": "bad"}],
        ("GET", "/api/42/tickets/forms"): CREATE_ROUTES["/api/42/tickets/forms"],
        ("GET", "/api/42/tickets/sources"): CREATE_ROUTES["/api/42/tickets/sources"],
        ("POST", "/api/accounts/search"): [dict(ID=11, Name="Information Technology", IsActive=True, ManagerFullName="private"),
                                           dict(ID=12, Name="Old", IsActive=False)],
    }
    adapter, calls = metadata_adapter(routes)
    types = adapter.discover_create_metadata("types", limit=10)
    assert types == {"kind": "types", "results": [{"ID": 3, "Name": "Hardware", "CategoryName": "Support"}],
                     "returned": 1, "complete": True}
    assert adapter.discover_create_metadata("types", search="hard", limit=10)["returned"] == 1
    assert adapter.discover_create_metadata("types", search="zzz", limit=10)["returned"] == 0
    forms = adapter.discover_create_metadata("forms", limit=10)
    assert forms["results"] == [{"ID": 2, "Name": "Standard", "IsDefaultForApp": True}]
    assert adapter.discover_create_metadata("sources", limit=10)["results"] == [{"ID": 6, "Name": "Web"}]
    accounts = adapter.discover_create_metadata("accounts", search="Info", limit=5)
    assert accounts["results"] == [{"ID": 11, "Name": "Information Technology"}]
    assert "private" not in json.dumps(accounts)
    post = next(c for c in calls if c[0] == "POST")
    assert post[2]["json"] == {"SearchText": "Info", "IsActive": True, "MaxResults": 5}
    with pytest.raises(ValueError):
        adapter.discover_create_metadata("accounts", limit=5)
    with pytest.raises(ValueError):
        adapter.discover_create_metadata("unknown", limit=5)


ASSET_APP = 928
ASSET_REC = dict(ID=1973209, AppID=ASSET_APP, Name="Micah Cooper MacBook", Tag="CU-1", SerialNumber="SN1", StatusID=1447,
                 StatusName="In Use", ConfigurationItemID=77009, ModifiedDate="asset-v1", ExpectedReplacementDate="2028-01-01T00:00:00Z",
                 OwningCustomerName="Micah Cooper", ExternalID=None)
ASSET_ROUTES = {
    f"/api/{ASSET_APP}/assets/1973209": ASSET_REC.copy(),
    f"/api/{ASSET_APP}/assets/statuses": [dict(ID=1447, Name="In Use", IsActive=True, IsOutOfService=False),
                                          dict(ID=1448, Name="Retired", IsActive=True, IsOutOfService=True),
                                          dict(ID=9, Name="Old", IsActive=False)],
    "/api/accounts/56883": dict(ID=56883, Name="Information Technology", IsActive=True),
}


def asset_adapter(**overrides):
    from dynamix_manager.ticket_writes.adapter import WriteAdapter
    records = {**ASSET_ROUTES, "/api/42/tickets/1001": TICKET.copy(), f"/api/people/{UID}": dict(UID=UID, IsActive=True, FullName="Person", OrgApplications=[dict(ID=42, IsActive=True)])}
    records.update(overrides)
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: ASSET_REC.copy())

    adapter = WriteAdapter("https://tenant.example/TDWebApi", 42, "secret", request=request,
                           read=lambda path: records[path], asset_app_id=ASSET_APP)
    return adapter, calls, records


def test_asset_comment_snapshots_the_asset_and_posts_to_its_feed():
    adapter, calls, _ = asset_adapter()
    prepared = adapter.validate(parse_action(dict(kind="asset_comment", asset_id=1973209, comments="Racked", is_private=False)))
    assert json.loads(prepared.payload_json) == dict(Comments="Racked", IsPrivate=False, IsRichHtml=False, Notify=[])
    assert json.loads(prepared.baseline_json)["ModifiedDate"] == "asset-v1"
    assert prepared.preview.action == "asset_comment" and prepared.preview.ticket_title == "Micah Cooper MacBook"
    assert prepared.asset_app_id == ASSET_APP and prepared.preview.ticket_id == 0
    adapter.request = lambda *a, **k: (calls.append((a, k)) or SimpleNamespace(status_code=201))
    assert adapter.apply_once(prepared).outcome == "applied"
    assert calls[-1][0] == ("POST", f"https://tenant.example/TDWebApi/api/{ASSET_APP}/assets/1973209/feed")


def test_asset_link_verifies_both_items_and_posts_the_association():
    adapter, calls, _ = asset_adapter()
    prepared = adapter.validate(parse_action(dict(kind="asset_link", asset_id=1973209, ticket_id=1001)))
    baseline = json.loads(prepared.baseline_json)
    assert baseline["ticket"]["ID"] == 1001 and baseline["asset"]["ID"] == 1973209
    assert prepared.preview.ticket_id == 1001 and prepared.preview.action == "asset_link"
    adapter.request = lambda *a, **k: (calls.append((a, k)) or SimpleNamespace(status_code=200, json=lambda: {"Message": "ok"}))
    assert adapter.apply_once(prepared).outcome == "applied"
    assert calls[-1][0] == ("POST", "https://tenant.example/TDWebApi/api/42/tickets/1001/assets/1973209")
    assert calls[-1][1]["json"] is None
    with pytest.raises(ValueError):
        adapter.validate(parse_action(dict(kind="asset_link", asset_id=1973209, ticket_id=1002)))


def test_asset_link_already_present_returns_204_and_counts_as_applied():
    # Observed live 2026-09-22: the first link returned 200; repeating it for an asset already on
    # the ticket returned 204 with exactly one association on read-back. The desired end state
    # holds, so the pipeline must not leave the ticket locked behind an "unknown" outcome.
    adapter, calls, _ = asset_adapter()
    prepared = adapter.validate(parse_action(dict(kind="asset_link", asset_id=1973209, ticket_id=1001)))
    adapter.request = lambda *a, **k: (calls.append((a, k)) or SimpleNamespace(status_code=204))
    result = adapter.apply_once(prepared)
    assert result.outcome == "applied" and result.status_code == 204
    assert result.message == "The asset was already linked to the ticket."


def test_only_the_link_endpoint_treats_204_as_applied():
    adapter, _, _ = asset_adapter()
    for action in (dict(kind="asset_comment", asset_id=1973209, comments="Racked"),
                   dict(kind="asset_edit", asset_id=1973209, tag="CU-2")):
        prepared = adapter.validate(parse_action(action))
        adapter.request = lambda *a, **k: SimpleNamespace(status_code=204)
        assert adapter.apply_once(prepared).outcome == "unknown"


def test_asset_edit_builds_a_verified_patch_and_confirms_the_updated_asset():
    adapter, calls, _ = asset_adapter()
    prepared = adapter.validate(parse_action(dict(kind="asset_edit", asset_id=1973209, status_id=1448, tag="CU-2",
                                                  owning_department_id=56883, owner_uid=UID, external_id=None,
                                                  expected_replacement_date="2029-06-30")))
    assert json.loads(prepared.payload_json) == [
        dict(op="replace", path="/Tag", value="CU-2"), dict(op="replace", path="/StatusID", value=1448),
        dict(op="replace", path="/OwningCustomerID", value=UID), dict(op="replace", path="/OwningDepartmentID", value=56883),
        dict(op="replace", path="/ExternalID", value=None), dict(op="replace", path="/ExpectedReplacementDate", value="2029-06-30")]
    fields = {f.name: (f.before, f.after) for f in prepared.preview.fields}
    assert fields["StatusID"] == ("In Use", "Retired") and fields["Tag"] == ("CU-1", "CU-2")
    assert fields["OwningCustomerID"] == ("Micah Cooper", "Person") and fields["OwningDepartmentID"][1] == "Information Technology"
    adapter.request = lambda *a, **k: (calls.append((a, k)) or SimpleNamespace(status_code=200, json=lambda: {**ASSET_REC, "Tag": "CU-2"}))
    result = adapter.apply_once(prepared)
    assert result.outcome == "applied" and calls[-1][0] == ("PATCH", f"https://tenant.example/TDWebApi/api/{ASSET_APP}/assets/1973209")
    assert result.detail == {"asset_id": 1973209, "status": "In Use", "tag": "CU-2", "name": "Micah Cooper MacBook"}
    adapter.request = lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: {"ID": 5, "AppID": ASSET_APP})
    assert adapter.apply_once(prepared).outcome == "unknown"


@pytest.mark.parametrize("change", [dict(status_id=9), dict(status_id=77), dict(owning_department_id=1), dict(owner_uid="22222222-2222-4222-8222-222222222222")])
def test_asset_edit_rejects_unverified_metadata(change):
    adapter, _, _ = asset_adapter()
    with pytest.raises(ValueError):
        adapter.validate(parse_action(dict(kind="asset_edit", asset_id=1973209, **change)))


def test_asset_actions_need_an_asset_application():
    from dynamix_manager.ticket_writes.adapter import WriteAdapter
    adapter = WriteAdapter("https://tenant.example/TDWebApi", 42, "secret", request=lambda *a, **k: None, read=lambda p: {})
    with pytest.raises(ValueError, match="asset application"):
        adapter.validate(parse_action(dict(kind="asset_comment", asset_id=1, comments="x")))


def test_asset_comment_preview_warns_that_asset_feeds_ignore_private_visibility():
    adapter, _, _ = asset_adapter()
    prepared = adapter.validate(parse_action(dict(kind="asset_comment", asset_id=1973209, comments="Racked")))
    assert any("do not honor private" in notice for notice in prepared.preview.notices)
    assert prepared.preview.visibility is None


PORTAL_APP = 2045
ARTICLE_REC = dict(ID=95821, AppID=PORTAL_APP, Subject="Mac password", Summary="Sync", Body="<p>Old</p>", CategoryID=9208,
                   CategoryName="Office Devices", Tags=["macos"], Status=3, StatusName="Approved", IsPublished=True, IsPublic=False,
                   RevisionNumber=4, ReviewDateUtc="2027-02-01T00:00:00Z", OwnerUid=UID, OwnerFullName="Person",
                   OwningGroupID=None, ModifiedDate="art-v1")
CATEGORY_REC = dict(ID=9208, AppID=PORTAL_APP, Name="Office Devices", Description="Desk gear", ParentID=0, Order=1.0,
                    IsPublic=True, InheritPermissions=False, WhitelistGroups=False, ModifiedDate="cat-v1")
CATEGORY_TREE = [dict(CATEGORY_REC, Subcategories=[]),
                 dict(ID=10548, AppID=PORTAL_APP, Name="Tech FAQ", ParentID=0, Order=2.0, IsPublic=True, ModifiedDate="cat-v2", Subcategories=[])]
ARTICLE_ROUTES = {
    f"/api/{PORTAL_APP}/knowledgebase/95821": ARTICLE_REC,
    f"/api/{PORTAL_APP}/knowledgebase/84764": dict(ARTICLE_REC, ID=84764, Subject="Password reset"),
    f"/api/{PORTAL_APP}/knowledgebase/categories": CATEGORY_TREE,
    f"/api/{PORTAL_APP}/knowledgebase/categories/9208": CATEGORY_REC,
    f"/api/{PORTAL_APP}/knowledgebase/categories/10548": CATEGORY_TREE[1],
    f"/api/{ASSET_APP}/assets/1973209": ASSET_REC,
    f"/api/people/{UID}": dict(UID=UID, IsActive=True, FullName="Person", OrgApplications=[dict(ID=42, IsActive=True)]),
    "/api/groups/77": dict(ID=77, IsActive=True, Name="Service Desk"),
}


def kb_adapter(**overrides):
    from dynamix_manager.ticket_writes.adapter import WriteAdapter
    records = {**ARTICLE_ROUTES, **overrides}
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return SimpleNamespace(status_code=200, json=lambda: ARTICLE_REC.copy())

    adapter = WriteAdapter("https://tenant.example/TDWebApi", 42, "secret", request=request,
                           read=lambda path: records[path], asset_app_id=ASSET_APP, portal_app_id=PORTAL_APP)
    return adapter, calls, records


def test_article_create_builds_a_sanitised_draft_payload():
    adapter, _, _ = kb_adapter()
    prepared = adapter.validate(parse_action(dict(
        kind="article_create", subject="Reset MFA", body="Step one\n\nStep <two>", category_id=9208,
        tags=["mfa", "auth"], owner_uid=UID, summary="How to reset")))
    payload = json.loads(prepared.payload_json)
    assert payload == dict(Subject="Reset MFA", Body="<p>Step one</p><p>Step &lt;two&gt;</p>", CategoryID=9208, Summary="How to reset",
                           Tags=["mfa", "auth"], OwnerUid=UID, Status=1, IsPublished=False, IsPublic=False)
    grouped = adapter.validate(parse_action(dict(kind="article_create", subject="G", body="b", category_id=9208, owning_group_id=77)))
    assert json.loads(grouped.payload_json)["OwningGroupID"] == 77
    assert {f.name: f.after for f in grouped.preview.fields}["Owning group"] == "Service Desk"
    assert prepared.portal_app_id == PORTAL_APP and prepared.preview.application == "Knowledge Base"
    assert prepared.preview.ticket_id == 0 and prepared.preview.action == "article_create"
    fields = {f.name: f.after for f in prepared.preview.fields}
    assert fields["Category"] == "Office Devices" and fields["Owner"] == "Person" and "Owning group" not in fields
    assert fields["Status"] == "Not Submitted" and fields["Published"] == "False"


def test_article_create_strips_scripts_and_says_so():
    adapter, _, _ = kb_adapter()
    prepared = adapter.validate(parse_action(dict(kind="article_create", subject="S", category_id=9208, owner_uid=UID,
                                                  body='<p onclick="x()">Hi</p><script>evil()</script>')))
    assert json.loads(prepared.payload_json)["Body"] == "<p>Hi</p>"
    assert any("script or frame markup was removed" in n.lower() for n in prepared.preview.notices)
    with pytest.raises(ValueError, match="category"):
        adapter.validate(parse_action(dict(kind="article_create", subject="S", body="b", category_id=999, owner_uid=UID)))


def test_article_edit_builds_a_patch_with_baseline_and_publication_notices():
    adapter, _, _ = kb_adapter()
    prepared = adapter.validate(parse_action(dict(kind="article_edit", article_id=95821, status="archived",
                                                  is_published=False, is_public=True, tags=["macos", "password"], body="New text")))
    assert json.loads(prepared.payload_json) == [
        dict(op="replace", path="/Body", value="<p>New text</p>"), dict(op="replace", path="/Tags", value=["macos", "password"]),
        dict(op="replace", path="/IsPublic", value=True), dict(op="replace", path="/IsPublished", value=False),
        dict(op="replace", path="/Status", value=5)]
    baseline = json.loads(prepared.baseline_json)
    assert baseline["ModifiedDate"] == "art-v1" and baseline["RevisionNumber"] == 4
    fields = {f.name: (f.before, f.after) for f in prepared.preview.fields}
    assert fields["Status"] == ("Approved", "Archived") and fields["Published"] == ("True", "False")
    notices = " ".join(prepared.preview.notices)
    assert "archives the article" in notices and "visible without signing in" in notices and "publishes" not in notices
    assert prepared.preview.ticket_title == "Mac password"


def test_article_edit_verifies_category_owner_and_group():
    adapter, _, _ = kb_adapter()
    prepared = adapter.validate(parse_action(dict(kind="article_edit", article_id=95821, category_id=9208, owner_uid=UID, owning_group_id=77)))
    fields = {f.name: (f.before, f.after) for f in prepared.preview.fields}
    assert fields["Category"] == ("Office Devices", "Office Devices") and fields["Owner"] == ("Person", "Person")
    assert fields["Owning group"] == (None, "Service Desk")
    with pytest.raises(ValueError):
        adapter.validate(parse_action(dict(kind="article_edit", article_id=95821, owning_group_id=78)))
    with pytest.raises(ValueError, match="article"):
        adapter.validate(parse_action(dict(kind="article_edit", article_id=1, subject="x")))


def test_article_link_and_unlink_verify_both_ends():
    adapter, _, _ = kb_adapter()
    to_asset = adapter.validate(parse_action(dict(kind="article_link", article_id=95821, asset_id=1973209)))
    assert json.loads(to_asset.baseline_json)["target"]["ID"] == 1973209 and to_asset.preview.action == "article_link"
    assert {f.name: f.after for f in to_asset.preview.fields} == {"Asset": "Micah Cooper MacBook"}
    to_article = adapter.validate(parse_action(dict(kind="article_unlink", article_id=95821, related_article_id=84764)))
    assert {f.name: f.before for f in to_article.preview.fields} == {"Related article": "Password reset"}
    with pytest.raises(ValueError):
        adapter.validate(parse_action(dict(kind="article_link", article_id=95821, related_article_id=1)))


def test_category_create_and_edit_payloads():
    adapter, _, _ = kb_adapter()
    created = adapter.validate(parse_action(dict(kind="category_create", name="Scratch", parent_id=10548, description="Test")))
    assert json.loads(created.payload_json) == dict(Name="Scratch", ParentID=10548, Description="Test", IsPublic=False, InheritPermissions=False)
    assert {f.name: f.after for f in created.preview.fields}["Parent"] == "Tech FAQ"
    edited = adapter.validate(parse_action(dict(kind="category_edit", category_id=9208, name="Office devices", parent_id=10548)))
    payload = json.loads(edited.payload_json)
    assert payload["Name"] == "Office devices" and payload["ParentID"] == 10548 and payload["Description"] == "Desk gear"
    assert payload["ID"] == 9208 and "Subcategories" not in payload and "ModifiedDate" not in payload
    assert json.loads(edited.baseline_json)["ModifiedDate"] == "cat-v1"
    assert edited.preview.ticket_title == "Office Devices" and edited.preview.action == "category_edit"
    with pytest.raises(ValueError, match="parent"):
        adapter.validate(parse_action(dict(kind="category_edit", category_id=9208, parent_id=9208)))


def test_article_actions_need_a_portal_application():
    from dynamix_manager.ticket_writes.adapter import WriteAdapter
    adapter = WriteAdapter("https://tenant.example/TDWebApi", 42, "secret", read=lambda path: ARTICLE_ROUTES[path])
    with pytest.raises(ValueError, match="portal"):
        adapter.validate(parse_action(dict(kind="article_edit", article_id=95821, subject="x")))


def test_article_create_and_edit_apply_and_confirm_the_article():
    adapter, calls, _ = kb_adapter()
    prepared = adapter.validate(parse_action(dict(kind="article_create", subject="Reset MFA", body="x", category_id=9208, owner_uid=UID)))
    created = dict(ARTICLE_REC, ID=170001, Subject="Reset MFA", Status=1, StatusName="Not Submitted", IsPublished=False, RevisionNumber=1)
    adapter.request = lambda *a, **k: (calls.append((a, k)) or SimpleNamespace(status_code=201, json=lambda: created))
    result = adapter.apply_once(prepared)
    assert result.outcome == "applied" and result.status_code == 201
    assert result.detail == {"article_id": 170001, "status": "Not Submitted", "is_published": False, "is_public": False, "revision": 1}
    assert calls[-1][0] == ("POST", f"https://tenant.example/TDWebApi/api/{PORTAL_APP}/knowledgebase")
    assert calls[-1][1]["json"]["Subject"] == "Reset MFA"
    edit = adapter.validate(parse_action(dict(kind="article_edit", article_id=95821, subject="Renamed")))
    adapter.request = lambda *a, **k: (calls.append((a, k)) or SimpleNamespace(status_code=200, json=lambda: dict(ARTICLE_REC, Subject="Renamed")))
    result = adapter.apply_once(edit)
    assert result.outcome == "applied" and result.detail["article_id"] == 95821 and result.detail["revision"] == 4
    assert calls[-1][0] == ("PATCH", f"https://tenant.example/TDWebApi/api/{PORTAL_APP}/knowledgebase/95821")
    adapter.request = lambda *a, **k: SimpleNamespace(status_code=200, json=lambda: dict(ARTICLE_REC, ID=1))
    assert adapter.apply_once(edit).outcome == "unknown"
    adapter.request = lambda *a, **k: SimpleNamespace(status_code=403, json=lambda: {})
    assert adapter.apply_once(edit).outcome == "rejected"


def test_article_links_use_the_right_endpoint_and_idempotent_statuses():
    adapter, calls, _ = kb_adapter()
    to_asset = adapter.validate(parse_action(dict(kind="article_link", article_id=95821, asset_id=1973209)))
    adapter.request = lambda *a, **k: (calls.append((a, k)) or SimpleNamespace(status_code=200, json=lambda: {"Message": "ok"}))
    assert adapter.apply_once(to_asset).outcome == "applied"
    assert calls[-1][0] == ("POST", f"https://tenant.example/TDWebApi/api/{ASSET_APP}/assets/1973209/articles/95821")
    adapter.request = lambda *a, **k: SimpleNamespace(status_code=204)
    assert adapter.apply_once(to_asset).message == "The article was already linked."
    unlink = adapter.validate(parse_action(dict(kind="article_unlink", article_id=95821, related_article_id=84764)))
    adapter.request = lambda *a, **k: (calls.append((a, k)) or SimpleNamespace(status_code=200, json=lambda: {"Message": "ok"}))
    assert adapter.apply_once(unlink).outcome == "applied"
    assert calls[-1][0] == ("DELETE", f"https://tenant.example/TDWebApi/api/{PORTAL_APP}/knowledgebase/95821/related/84764")
    adapter.request = lambda *a, **k: SimpleNamespace(status_code=404)
    result = adapter.apply_once(unlink)
    assert result.outcome == "applied" and result.message == "The link did not exist."


def test_category_create_and_edit_apply():
    adapter, calls, _ = kb_adapter()
    created = adapter.validate(parse_action(dict(kind="category_create", name="Scratch", parent_id=10548)))
    adapter.request = lambda *a, **k: (calls.append((a, k)) or SimpleNamespace(status_code=201, json=lambda: dict(CATEGORY_REC, ID=30001, Name="Scratch", ParentID=10548)))
    result = adapter.apply_once(created)
    assert result.outcome == "applied" and result.detail == {"category_id": 30001, "name": "Scratch", "parent_id": 10548}
    assert calls[-1][0] == ("POST", f"https://tenant.example/TDWebApi/api/{PORTAL_APP}/knowledgebase/categories")
    edited = adapter.validate(parse_action(dict(kind="category_edit", category_id=9208, name="Office devices")))
    adapter.request = lambda *a, **k: (calls.append((a, k)) or SimpleNamespace(status_code=200, json=lambda: dict(CATEGORY_REC, Name="Office devices")))
    result = adapter.apply_once(edited)
    assert result.outcome == "applied" and result.detail["name"] == "Office devices"
    assert calls[-1][0] == ("PUT", f"https://tenant.example/TDWebApi/api/{PORTAL_APP}/knowledgebase/categories/9208")
    assert calls[-1][1]["json"]["ID"] == 9208
