"""Load local fictional CSV feeds; property links are fixture associations.

An entity record's property_id identifies a candidate, not confirmed ownership.
No source is approved, matched, or written to a database by this module.
"""

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, TypeVar

from .models import CountyRecord, EntityRecord, Property


T = TypeVar("T", Property, CountyRecord, EntityRecord)


@dataclass(frozen=True)
class Dataset:
    properties: tuple[Property, ...]
    county_records: tuple[CountyRecord, ...]
    entity_records: tuple[EntityRecord, ...]


def _parse_date(value: str) -> date:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError("source_as_of must use YYYY-MM-DD")
    return parsed


def _read_csv(path: Path, columns: tuple[str, ...], factory: Callable[..., T]) -> tuple[T, ...]:
    records: list[T] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if (reader.fieldnames is None or len(reader.fieldnames) != len(columns)
                or set(reader.fieldnames) != set(columns)):
            raise ValueError(f"{path.name}: expected columns {', '.join(columns)}")
        try:
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("row has a missing or extra CSV field")
                values = dict(row)
                if "source_as_of" in values:
                    values["source_as_of"] = _parse_date(values["source_as_of"])
                record = factory(**values)
                key = record.property_id if isinstance(record, Property) else record.source_record_id
                if key in seen:
                    raise ValueError(f"duplicate record ID {key!r}")
                seen.add(key)
                records.append(record)
        except (ValueError, TypeError, csv.Error) as error:
            raise ValueError(f"{path.name}: line {reader.line_num}: {error}") from error
    return tuple(records)


def load_properties(path: str | Path) -> tuple[Property, ...]:
    return _read_csv(Path(path), ("property_id", "property_address", "tenant"), Property)


def load_county_records(path: str | Path) -> tuple[CountyRecord, ...]:
    return _read_csv(Path(path), ("source_record_id", "property_id", "source_name",
                               "source_as_of", "owner_name", "mailing_address"), CountyRecord)


def load_entity_records(path: str | Path) -> tuple[EntityRecord, ...]:
    return _read_csv(Path(path), ("source_record_id", "property_id", "source_name",
                               "source_as_of", "entity_name", "entity_status",
                               "registered_address", "registered_agent"), EntityRecord)


def load_dataset(data_dir: str | Path) -> Dataset:
    data_dir = Path(data_dir)
    properties = load_properties(data_dir / "properties.csv")
    if not properties:
        raise ValueError("properties.csv must contain at least one property")
    county_records = load_county_records(data_dir / "county_records.csv")
    entity_records = load_entity_records(data_dir / "entity_records.csv")
    property_ids = {record.property_id for record in properties}
    for record in (*county_records, *entity_records):
        if record.property_id not in property_ids:
            raise ValueError(f"{record.source_record_id}: unknown property_id {record.property_id!r}")
    return Dataset(properties, county_records, entity_records)
