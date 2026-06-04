from dynamix_manager.cli import build_parser


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
