"""Administrative CLI. Run from regulatory-api; never prints credentials."""

import argparse
import json
import logging
import traceback
from pathlib import Path

from pymongo import MongoClient

from ..app import Settings
from ..llm import LangChainClient
from ..models import Authority
from .models import ClientProfile
from .service import IntelligenceService

logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 3 batch intelligence")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init-db")
    analyze = commands.add_parser("analyze-document")
    analyze.add_argument(
        "--source", choices=[s.value for s in Authority], required=True
    )
    analyze.add_argument(
        "--document-hash", help="Omit to analyze all documents for --source"
    )
    clients = commands.add_parser("import-clients")
    clients.add_argument("file", type=Path, help="JSON array of tenant-owned profiles")
    review = commands.add_parser("review")
    review_scope = review.add_mutually_exclusive_group(required=True)
    review_scope.add_argument("--analysis-id")
    review_scope.add_argument(
        "--source",
        choices=[s.value for s in Authority],
        help="Bulk review pending/same-decision analyses for this source",
    )
    review.add_argument("--reviewer", required=True)
    review.add_argument("--decision", choices=["approved", "rejected"], required=True)
    alerts = commands.add_parser("build-alerts")
    alerts.add_argument("--analysis-id", required=True)
    alerts.add_argument("--tenant-id", required=True)
    args = parser.parse_args()
    settings = Settings()
    if not settings.mongodb_uri:
        parser.error("Set MONGODB_URI in environment or .env")
    model_client = None
    if args.command == "analyze-document":
        if not settings.openai_api_key:
            parser.error("Set OPENAI_API_KEY for analysis")
        model_client = LangChainClient(
            settings.openai_api_key.get_secret_value(), settings.chat_model
        )
    try:
        with MongoClient(settings.mongodb_uri, tz_aware=True, timeoutMS=15000) as db:
            service = IntelligenceService(
                db[settings.mongodb_database],
                model_client,
                settings.chat_model,
                sensitive_values=[
                    settings.mongodb_uri,
                    (
                        settings.openai_api_key.get_secret_value()
                        if settings.openai_api_key
                        else ""
                    ),
                    (
                        settings.phase3_api_token.get_secret_value()
                        if settings.phase3_api_token
                        else ""
                    ),
                ],
            )
            if args.command == "init-db":
                service.init_indexes()
                result = {"status": "indexes_created"}
            elif args.command == "analyze-document":
                result = (
                    service.analyze(args.source, args.document_hash)
                    if args.document_hash
                    else service.analyze_source(args.source)
                )
            elif args.command == "import-clients":
                profiles = [
                    ClientProfile.model_validate(p)
                    for p in json.loads(args.file.read_text())
                ]
                for profile in profiles:
                    service.save_client(profile)
                result = {"profiles_saved": len(profiles)}
            elif args.command == "review":
                if args.source:
                    result = service.review_source(
                        args.source, args.reviewer, args.decision
                    )
                else:
                    service.review(args.analysis_id, args.reviewer, args.decision)
                    result = {"status": args.decision}
            else:
                result = {
                    "profiles_evaluated": service.build_alerts(
                        args.tenant_id, args.analysis_id
                    )
                }
            print(json.dumps(result))
            if result.get("failed", 0):
                parser.exit(1, "Batch finished with failures; see the JSON summary.\n")
    except Exception as exc:
        # DB/client exception text can contain secrets or private source text.
        logger.exception("Failed (%s)", type(exc).__name__)
        traceback.print_exc()
        parser.exit(
            1,
            f"Phase 3 failed ({type(exc).__name__}); check configuration and processing_runs.\n",
        )


if __name__ == "__main__":
    main()
