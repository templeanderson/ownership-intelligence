"""Load, inspect, review, and export local evidence with explicit human decisions."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from .__main__ import _validate_output
from .ingestion import load_dataset
from .matching import reconcile_dataset
from .models import ReviewStatus
from .policy import load_policy
from .repository import Repository
from .review import submit_review
from .salesforce import generate_payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("ownership.sqlite3"))
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser("load", help="Reconcile CSVs and persist unreviewed evidence")
    load.add_argument("--data-dir", type=Path, default=Path("data"))
    load.add_argument("--config", type=Path, default=Path("config/matching.toml"))
    commands.add_parser("list", help="Show the current review queue")
    for name in ("show", "history", "decide", "export"):
        command = commands.add_parser(name)
        command.add_argument("property_id")
        if name == "decide":
            command.add_argument("--action", required=True, choices=[
                "approved", "needs_research", "rejected"])
            command.add_argument("--reviewer", required=True)
            command.add_argument("--snapshot-id", type=int, required=True)
            command.add_argument("--revision", type=int, required=True)
            command.add_argument("--candidate", type=int, help="Explicit zero-based candidate index for approval")
            command.add_argument("--note", help="Required when approval overrides property or selected candidate warnings")
    args = parser.parse_args()
    try:
        results = None
        if args.command == "load":
            _validate_output(args.db, args.data_dir, args.config, label="Database path")
            results = reconcile_dataset(load_dataset(args.data_dir), load_policy(args.config))
        elif not args.db.is_file():
            raise ValueError("Database does not exist; load evidence first")
        with Repository(args.db) as repository:
            if args.command == "load":
                repository.save_results(results)
                output = {"loaded": len(results), "message": "Changed evidence requires new review; no automatic approval."}
            elif args.command == "list":
                output = [{"property_id": record.property_id, "snapshot_id": record.snapshot_id,
                           "revision": record.revision, "review_status": record.review_status,
                           "disposition": record.evidence["disposition"]}
                          for record in repository.list_records()]
            elif args.command == "show":
                output = asdict(repository.get_record(args.property_id))
            elif args.command == "history":
                output = [asdict(event) for event in repository.history(args.property_id)]
            elif args.command == "export":
                output = generate_payload(repository, args.property_id)
            else:
                output = asdict(submit_review(repository, args.property_id, ReviewStatus(args.action),
                    reviewer_name=args.reviewer, expected_snapshot_id=args.snapshot_id,
                    expected_revision=args.revision, selected_candidate=args.candidate, note=args.note))
        print(json.dumps(output, indent=2, ensure_ascii=False))
    except (OSError, ValueError, KeyError, sqlite3.Error) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
