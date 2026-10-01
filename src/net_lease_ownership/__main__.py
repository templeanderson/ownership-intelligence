"""Generate a local matching analysis report, not a Salesforce payload."""

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
import tempfile

from .ingestion import load_dataset
from .matching import MATCHING_VERSION, reconcile_dataset
from .policy import load_policy


def _validate_output(output: Path, data_dir: Path, config: Path) -> None:
    protected = [config, *(data_dir / name for name in (
        "properties.csv", "county_records.csv", "entity_records.csv"))]
    resolved = output.resolve()
    for source in protected:
        if resolved == source.resolve() or (output.exists() and source.exists() and output.samefile(source)):
            raise ValueError(f"Report output must not overwrite input or configuration: {source}")


def _write_report(output: Path, content: str) -> None:
    """Replace a report only after a complete temporary write succeeds."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent,
                                         prefix=".matching-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
        temporary.replace(output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--config", type=Path, default=Path("config/matching.toml"))
    parser.add_argument("--output", type=Path, default=Path("exports/matching_report.json"))
    args = parser.parse_args()
    try:
        _validate_output(args.output, args.data_dir, args.config)
        results = reconcile_dataset(load_dataset(args.data_dir), load_policy(args.config))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    report = {
        "report_type": "matching_analysis_only",
        "matching_version": MATCHING_VERSION,
        "summary": dict(Counter(result.disposition.value for result in results)),
        "results": [asdict(result) | {"property_id": result.property_id,
                    "property_address": result.property_address,
                    "name_similarity": result.name_similarity,
                    "address_match": result.address_match} for result in results],
    }

    def json_default(value):
        if isinstance(value, date):
            return value.isoformat()
        raise TypeError(f"Cannot serialize {type(value).__name__}")

    try:
        _write_report(args.output, json.dumps(report, indent=2, default=json_default) + "\n")
    except OSError as error:
        parser.error(f"Cannot write matching report: {error}")
    print(f"Analyzed {len(results)} properties: {report['summary']}")
    print(f"Report: {args.output}. Every result is unreviewed; no Salesforce payload was generated.")


if __name__ == "__main__":
    main()
