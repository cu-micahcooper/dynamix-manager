from dynamix_manager.surveys import infer_survey_columns, normalize_survey_rows


def test_infer_survey_columns_finds_satisfaction_and_comment_keys():
    row = {
        "ResponseID": 1,
        "TicketID": 42,
        "48398": "Very Satisfied",
        "48399": "Helpful support",
    }

    columns = infer_survey_columns([row])

    assert columns["satisfaction_key"] == "48398"
    assert columns["comment_key"] == "48399"
    assert columns["effort_key"] is None


def test_infer_survey_columns_finds_effort_key():
    rows = [
        {"ResponseID": 1, "TicketID": 42, "168242": "Easy", "168243": "Great service"},
    ]
    columns = infer_survey_columns(rows)
    assert columns["effort_key"] == "168242"
    assert columns["comment_key"] == "168243"
    assert columns["satisfaction_key"] is None


def test_infer_survey_columns_scans_multiple_rows_to_find_effort():
    rows = [
        {"ResponseID": 1, "TicketID": 42, "168242": None, "48399": "Thank you"},  # old row
        {"ResponseID": 2, "TicketID": 43, "168242": "Very Easy", "48399": None},  # new row
    ]
    columns = infer_survey_columns(rows)
    assert columns["effort_key"] == "168242"


def test_infer_survey_columns_keeps_multiple_comment_fields_and_rating_field():
    rows = [
        {
            "ResponseID": 1,
            "TicketID": 42,
            "168242": None,
            "48399": "Older free-text comment",
        },
        {
            "ResponseID": 2,
            "TicketID": 43,
            "168242": "Easy",
            "168243": "Newer free-text comment",
            "48403": "Great!",
            "48399": "Second free-text answer",
        },
    ]

    columns = infer_survey_columns(rows)

    assert columns["effort_key"] == "168242"
    assert columns["satisfaction_key"] == "48403"
    assert set(columns["comment_keys"]) == {"168243", "48399"}


def test_infer_survey_columns_skips_metadata_fields():
    row = {
        "ResponseID": 1,
        "TicketID": 42,
        "ItemTitle": "Password Reset",
        "SurveyCompletedFullName": "Jane Doe",
        "AccountName": "IT Department",
        "168242": "Easy",
        "168243": "Nice work",
    }
    columns = infer_survey_columns([row])
    assert columns["comment_key"] == "168243"
    assert columns["comment_key"] not in {"ItemTitle", "SurveyCompletedFullName", "AccountName"}


def test_normalize_survey_rows_preserves_ticket_link_and_dates():
    rows = [
        {
            "ResponseID": 1,
            "TicketID": 42,
            "SurveyRequestedDate": "2026-03-01T11:00:00Z",
            "SurveyCompletedDate": "2026-03-01T12:00:00Z",
            "SurveyCompletedFullName": "Jane Doe",
            "48398": "Very Satisfied",
            "48399": "Helpful support",
        }
    ]

    df = normalize_survey_rows(rows)

    assert df.loc[0, "response_id"] == 1
    assert df.loc[0, "ticket_id"] == 42
    assert df.loc[0, "survey_requested_at"] == "2026-03-01T11:00:00Z"
    assert df.loc[0, "survey_completed_at"] == "2026-03-01T12:00:00Z"
    assert df.loc[0, "commenter_name"] == "Jane Doe"
    assert df.loc[0, "satisfaction_label"] == "Very Satisfied"
    assert df.loc[0, "comment_text"] == "Helpful support"


def test_normalize_survey_rows_includes_customer_effort_label():
    rows = [
        {
            "ResponseID": 2,
            "TicketID": 99,
            "SurveyRequestedDate": "2026-04-01T08:00:00Z",
            "SurveyCompletedDate": "2026-04-01T09:00:00Z",
            "168242": "Easy",
            "168243": "Quick resolution, thank you",
        }
    ]

    df = normalize_survey_rows(rows)

    assert df.loc[0, "customer_effort_label"] == "Easy"
    assert df.loc[0, "commenter_name"] is None
    assert df.loc[0, "comment_text"] == "Quick resolution, thank you"
    assert df.loc[0, "satisfaction_label"] is None


def test_normalize_survey_rows_effort_none_for_old_rows():
    rows = [
        {
            "ResponseID": 1,
            "TicketID": 42,
            "SurveyRequestedDate": "2026-03-01T11:00:00Z",
            "SurveyCompletedDate": "2026-03-01T12:00:00Z",
            "168242": None,
            "48399": "Thank you!",
        }
    ]

    df = normalize_survey_rows(rows)

    assert df.loc[0, "customer_effort_label"] is None
    assert df.loc[0, "comment_text"] == "Thank you!"


def test_normalize_survey_rows_combines_multiple_comment_columns():
    rows = [
        {
            "ResponseID": 2,
            "TicketID": 99,
            "SurveyRequestedDate": "2026-04-14T08:00:00Z",
            "SurveyCompletedDate": "2026-04-14T09:00:00Z",
            "168242": "Easy",
            "168243": "Friendly service by people who know what they are doing.",
            "48403": "Great!",
            "48399": "Very good!",
        }
    ]

    df = normalize_survey_rows(rows)

    assert df.loc[0, "customer_effort_label"] == "Easy"
    assert df.loc[0, "satisfaction_label"] == "Great!"
    assert df.loc[0, "comment_text"] == (
        "Friendly service by people who know what they are doing.\n\nVery good!"
    )
