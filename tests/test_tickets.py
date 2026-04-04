from dynamix_manager.tickets import build_ticket_search_filters, normalize_ticket_rows


def test_build_ticket_search_filters_uses_modified_date_for_incremental_sync():
    payload = build_ticket_search_filters(modified_from="2026-03-01T00:00:00Z")

    assert payload["ModifiedDateFrom"] == "2026-03-01T00:00:00Z"
    assert payload["StatusIDs"] == []


def test_normalize_ticket_rows_extracts_fields_needed_for_survey_join():
    rows = [
        {
            "ID": 42,
            "Title": "Laptop issue",
            "StatusName": "Closed",
            "StatusClass": 3,
            "TypeName": "Incident",
            "PriorityName": "High",
            "ServiceName": "Desktop Support",
            "ResponsibleGroupName": "Client Services",
            "RespondingFullName": "Analyst One",
            "RespondedUid": "analyst-1",
            "RequestorName": "Pat Client",
            "RequestorUid": "client-1",
            "CreatedDate": "2026-03-01T08:00:00Z",
            "ModifiedDate": "2026-03-01T10:00:00Z",
            "RespondedDate": "2026-03-01T08:30:00Z",
            "CompletedDate": "2026-03-01T09:00:00Z",
            "CompletedFullName": "Analyst One",
            "SlaName": "Standard",
            "SlaBeginDate": "2026-03-01T08:00:00Z",
            "RespondByDate": "2026-03-01T09:00:00Z",
            "ResolveByDate": "2026-03-02T08:00:00Z",
            "IsSlaViolated": False,
            "IsSlaRespondByViolated": False,
            "IsSlaResolveByViolated": False,
        }
    ]

    df = normalize_ticket_rows(rows)

    assert df.loc[0, "ticket_id"] == 42
    assert df.loc[0, "ticket_title"] == "Laptop issue"
    assert df.loc[0, "status_name"] == "Closed"
    assert df.loc[0, "status_class"] == 3
    assert df.loc[0, "service_name"] == "Desktop Support"
    assert df.loc[0, "team_name"] == "Client Services"
    assert df.loc[0, "assignee_name"] == "Analyst One"
    assert df.loc[0, "assignee_uid"] == "analyst-1"
    assert df.loc[0, "requestor_name"] == "Pat Client"
    assert df.loc[0, "created_at"] == "2026-03-01T08:00:00Z"
    assert df.loc[0, "resolved_at"] == "2026-03-01T09:00:00Z"
    assert df.loc[0, "sla_name"] == "Standard"
    assert not bool(df.loc[0, "is_sla_violated"])
