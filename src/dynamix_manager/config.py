import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def required_env_vars() -> tuple[str, ...]:
    """Return the TeamDynamix-specific environment variables required to run."""

    return (
        "TDX_BASE_URL",
        "TDX_APP_ID",
        "TDX_USERNAME",
        "TDX_PASSWORD",
    )


def analytics_db_path() -> Path:
    """Return the default local DuckDB storage path."""

    return Path("data") / "analytics.duckdb"


def survey_report_id() -> int:
    """Return the static TeamDynamix survey report ID used for ingestion."""

    return 100482


@dataclass(frozen=True)
class RuntimeConfig:
    base_url: str
    app_id: str
    username: str
    password: str
    db_path: Path
    report_output_path: Path
    notebook_output_path: Path
    gmail_user: str | None = None
    gmail_app_password: str | None = None
    gmail_token_path: str | None = None
    gmail_draft_to: str | None = None
    youtrack_base: str | None = None
    youtrack_token: str | None = None
    youtrack_board_id: str | None = None


def load_runtime_config() -> RuntimeConfig:
    """Load runtime configuration from the environment and `.env` file."""

    load_dotenv()
    missing = [name for name in required_env_vars() if not os.environ.get(name)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {missing}")

    return RuntimeConfig(
        base_url=os.environ["TDX_BASE_URL"],
        app_id=os.environ["TDX_APP_ID"],
        username=os.environ["TDX_USERNAME"],
        password=os.environ["TDX_PASSWORD"],
        db_path=analytics_db_path(),
        report_output_path=Path("reports") / "survey_health.html",
        notebook_output_path=Path("notebooks") / "survey_health.ipynb",
        gmail_user=os.environ.get("GMAIL_USER"),
        gmail_app_password=os.environ.get("GMAIL_APP_PASSWORD"),
        gmail_token_path=os.environ.get("GMAIL_TOKEN_PATH"),
        gmail_draft_to=os.environ.get("GMAIL_DRAFT_TO"),
        youtrack_base=os.environ.get("YOUTRACK_BASE"),
        youtrack_token=os.environ.get("YOUTRACK_TOKEN"),
        youtrack_board_id=os.environ.get("YOUTRACK_BOARD_ID"),
    )
