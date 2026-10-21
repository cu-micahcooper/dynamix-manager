from dynamix_manager.cli import build_parser
from types import SimpleNamespace

import pytest


def test_build_parser_supports_refresh_surveys_command():
    parser = build_parser()
    parsed = parser.parse_args(["refresh-surveys"])
    assert parsed.command == "refresh-surveys"


def test_build_parser_supports_generate_report_command():
    parser = build_parser()
    parsed = parser.parse_args(["generate-report"])
    assert parsed.command == "generate-report"


def test_build_parser_supports_generate_cfo_email_header_burst_text_option():
    parser = build_parser()
    parsed = parser.parse_args([
        "generate-cfo-email",
        "--header-burst-text",
        "Board of Trustee Edition",
    ])
    assert parsed.command == "generate-cfo-email"
    assert parsed.header_burst_text == "Board of Trustee Edition"


def test_build_parser_supports_generate_cfo_email_datatype_sparklines_option():
    parser = build_parser()
    parsed = parser.parse_args([
        "generate-cfo-email",
        "--datatype-sparklines",
    ])
    assert parsed.command == "generate-cfo-email"
    assert parsed.datatype_sparklines is True


@pytest.mark.parametrize("draft_id", [None, "draft-123"])
def test_cfo_cli_fails_when_draft_is_missing(monkeypatch, capsys, draft_id):
    from dynamix_manager import cli

    monkeypatch.setattr("sys.argv", ["dynamix-manager", "generate-cfo-email"])
    monkeypatch.setattr(cli, "load_runtime_config", lambda: SimpleNamespace(
        base_url="https://example.test", app_id="1", username="test", password="test"))
    monkeypatch.setattr(cli.pipeline, "generate_cfo_email", lambda **kwargs: {
        "gmail_draft_id": draft_id, "email_path": "reports/cfo_email.html"})
    if draft_id:
        cli.main()
    else:
        with pytest.raises(SystemExit) as exc:
            cli.main()
        assert exc.value.code == 1
        assert "Gmail draft was not created" in capsys.readouterr().err
