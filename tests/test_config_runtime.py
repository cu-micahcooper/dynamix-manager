from dynamix_manager.config import load_runtime_config


def test_load_runtime_config_reads_required_env(monkeypatch):
    monkeypatch.setenv("TDX_BASE_URL", "https://example.test")
    monkeypatch.setenv("TDX_APP_ID", "1234")
    monkeypatch.setenv("TDX_USERNAME", "user")
    monkeypatch.setenv("TDX_PASSWORD", "pass")

    config = load_runtime_config()

    assert config.base_url == "https://example.test"
    assert config.app_id == "1234"
    assert config.db_path.name == "analytics.duckdb"
    assert config.report_output_path.name == "survey_health.html"
    assert config.notebook_output_path.name == "survey_health.ipynb"
