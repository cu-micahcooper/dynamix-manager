from dynamix_manager.cli import build_parser


def test_build_parser_supports_refresh_surveys_command():
    parser = build_parser()
    parsed = parser.parse_args(["refresh-surveys"])
    assert parsed.command == "refresh-surveys"


def test_build_parser_supports_generate_report_command():
    parser = build_parser()
    parsed = parser.parse_args(["generate-report"])
    assert parsed.command == "generate-report"
