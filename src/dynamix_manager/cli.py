from __future__ import annotations

import argparse
import json

from dynamix_manager import pipeline
from dynamix_manager.config import load_runtime_config, survey_report_id
from dynamix_manager.tdx_client import TeamDynamixClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dynamix-manager",
        description="TeamDynamix analytics pipeline CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

    # refresh-surveys
    subparsers.add_parser(
        "refresh-surveys",
        help="Fetch latest survey report and refresh linked-ticket model",
    )

    # backfill-tickets
    backfill = subparsers.add_parser(
        "backfill-tickets",
        help="Backfill ticket context for all surveyed tickets in batches",
    )
    backfill.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of tickets to fetch per batch (default: 50)",
    )
    backfill.add_argument(
        "--max-batches",
        type=int,
        default=5,
        help="Maximum number of batches to run (default: 5)",
    )

    # quality-check
    quality = subparsers.add_parser(
        "quality-check",
        help="Run ticket quality analysis for open tickets",
    )
    quality.add_argument(
        "ticket_app_id",
        type=int,
        help="TeamDynamix ticketing application ID",
    )
    quality.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of tickets to process",
    )

    # cache-days-off
    subparsers.add_parser(
        "cache-days-off",
        help="Fetch and cache planned days off / holidays",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    config = load_runtime_config()
    client = TeamDynamixClient(
        base_url=config.base_url,
        app_id=config.app_id,
        username=config.username,
        password=config.password,
    )

    if args.command == "refresh-surveys":
        result = pipeline.refresh_survey_slice(
            config=config,
            client=client,
            report_id=survey_report_id(),
        )
        print(json.dumps(result, indent=2))

    elif args.command == "backfill-tickets":
        result = pipeline.backfill_ticket_links(
            config=config,
            client=client,
            report_id=survey_report_id(),
            batch_size=args.batch_size,
            max_batches=args.max_batches,
        )
        print(json.dumps(result, indent=2))

    elif args.command == "quality-check":
        result = pipeline.cache_ticket_quality_slice(
            config=config,
            client=client,
            ticket_app_id=args.ticket_app_id,
            limit=args.limit,
        )
        print(json.dumps(result, indent=2))

    elif args.command == "cache-days-off":
        result = pipeline.cache_days_off(config=config, client=client)
        print(json.dumps(result, indent=2))

    else:
        parser.print_help()
        raise SystemExit(1)
