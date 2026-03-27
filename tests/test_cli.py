from dynamix_manager.cli import build_parser


def test_build_parser_supports_refresh_surveys_command():
    parser = build_parser()
    parsed = parser.parse_args(["refresh-surveys"])
    assert parsed.command == "refresh-surveys"
