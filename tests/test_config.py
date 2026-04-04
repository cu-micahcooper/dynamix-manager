from dynamix_manager.config import (
    analytics_db_path,
    required_env_vars,
    survey_report_id,
)


def test_required_env_vars_returns_ordered_teamdynamix_keys():
    expected = (
        "TDX_BASE_URL",
        "TDX_APP_ID",
        "TDX_USERNAME",
        "TDX_PASSWORD",
    )
    assert required_env_vars() == expected


def test_default_survey_storage_settings():
    assert analytics_db_path().name == "analytics.duckdb"
    assert survey_report_id() == 100482
