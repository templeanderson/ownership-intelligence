import csv
from pathlib import Path

import pytest

from net_lease_ownership.ingestion import (
    load_county_records, load_dataset, load_entity_records, load_properties,
)


DATA_DIR = Path(__file__).resolve().parents[1] / "data"
PROPERTY_COLUMNS = ["property_id", "property_address", "tenant"]
COUNTY_COLUMNS = ["source_record_id", "property_id", "source_name", "source_as_of",
                  "owner_name", "mailing_address"]
ENTITY_COLUMNS = ["source_record_id", "property_id", "source_name", "source_as_of",
                  "entity_name", "entity_status", "registered_address", "registered_agent"]


def write_csv(path, columns, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(columns)
        writer.writerows(rows)
    return path


def test_sample_dataset_has_valid_unique_ids_and_provenance():
    dataset = load_dataset(DATA_DIR)
    assert len(dataset.properties) == 15
    assert len(dataset.county_records) == 14
    assert len(dataset.entity_records) == 15
    property_ids = {record.property_id for record in dataset.properties}
    assert len(property_ids) == 15
    for feed in (dataset.county_records, dataset.entity_records):
        assert len({record.source_record_id for record in feed}) == len(feed)
        assert all(record.property_id in property_ids for record in feed)
        assert all(record.source_name.startswith("Fictional") for record in feed)
        assert all(record.source_as_of.isoformat().startswith("2026-09-") for record in feed)


def test_samples_include_required_evidence_variations():
    dataset = load_dataset(DATA_DIR)
    counties = {record.property_id: record for record in dataset.county_records}
    entities = {record.property_id: record for record in dataset.entity_records}
    assert counties["P001"].owner_normalized == entities["P001"].entity_normalized
    assert counties["P001"].mailing_address_normalized == entities["P001"].registered_address_normalized
    assert counties["P002"].owner_comparison != entities["P002"].entity_comparison
    assert counties["P002"].mailing_address_normalized == entities["P002"].registered_address_normalized
    assert counties["P003"].owner_comparison != entities["P003"].entity_comparison
    assert "P004" not in entities
    assert counties["P005"].owner_comparison == entities["P005"].entity_comparison
    assert counties["P005"].mailing_address_normalized != entities["P005"].registered_address_normalized
    assert counties["P009"].mailing_address_normalized == entities["P009"].registered_address_normalized
    assert "P010" not in counties
    assert entities["P011"].entity_status == "dissolved"
    assert counties["P012"].owner_comparison != entities["P012"].entity_comparison
    assert len([record for record in dataset.entity_records if record.property_id == "P013"]) == 2
    assert counties["P014"].mailing_address_normalized == entities["P014"].registered_address_normalized == ""
    assert entities["P015"].entity_name == ""


def test_csv_preserves_source_whitespace_and_punctuation():
    record = next(record for record in load_county_records(DATA_DIR / "county_records.csv")
                  if record.property_id == "P008")
    assert record.owner_name == "  Bluebird   Medical   Holdings LLC  "
    assert record.owner_normalized == "bluebird medical holdings llc"
    assert record.mailing_address.startswith(" ")


@pytest.mark.parametrize("columns", [
    ["property_id", "property_address"],
    ["property_id", "property_address", "tenant", "extra"],
    ["property_id", "property_address", "property_address"],
])
def test_wrong_or_duplicate_columns_are_rejected(tmp_path, columns):
    path = write_csv(tmp_path / "properties.csv", columns, [])
    with pytest.raises(ValueError, match="expected columns"):
        load_properties(path)


@pytest.mark.parametrize("row", [["P1", "100 Main St"], ["P1", "100 Main St", "", "extra"]])
def test_malformed_row_width_is_rejected(tmp_path, row):
    path = write_csv(tmp_path / "properties.csv", PROPERTY_COLUMNS, [row])
    with pytest.raises(ValueError, match="line 2:.*missing or extra"):
        load_properties(path)


def test_duplicate_property_id_is_rejected(tmp_path):
    path = write_csv(tmp_path / "properties.csv", PROPERTY_COLUMNS,
                     [["P1", "100 Main St", ""], ["P1", "200 Main St", ""]])
    with pytest.raises(ValueError, match="duplicate record ID"):
        load_properties(path)


def test_duplicate_feed_id_is_rejected(tmp_path):
    row = ["C1", "P1", "Fictional County Feed", "2026-09-15", "ABC LLC", ""]
    path = write_csv(tmp_path / "county_records.csv", COUNTY_COLUMNS, [row, row])
    with pytest.raises(ValueError, match="duplicate record ID"):
        load_county_records(path)


@pytest.mark.parametrize("value", ["2026-02-30", "09/15/2026", "20260915", ""])
def test_invalid_or_noncanonical_source_date_is_rejected(tmp_path, value):
    row = ["C1", "P1", "Fictional County Feed", value, "ABC LLC", ""]
    path = write_csv(tmp_path / "county_records.csv", COUNTY_COLUMNS, [row])
    with pytest.raises(ValueError, match="county_records.csv: line 2"):
        load_county_records(path)


def test_unknown_property_link_is_rejected(tmp_path):
    write_csv(tmp_path / "properties.csv", PROPERTY_COLUMNS, [["P1", "100 Main St", ""]])
    write_csv(tmp_path / "county_records.csv", COUNTY_COLUMNS,
              [["C1", "P2", "Fictional County Feed", "2026-09-15", "ABC LLC", ""]])
    write_csv(tmp_path / "entity_records.csv", ENTITY_COLUMNS, [])
    with pytest.raises(ValueError, match="unknown property_id 'P2'"):
        load_dataset(tmp_path)


def test_empty_property_list_is_rejected(tmp_path):
    write_csv(tmp_path / "properties.csv", PROPERTY_COLUMNS, [])
    with pytest.raises(ValueError, match="at least one property"):
        load_dataset(tmp_path)


def test_empty_feeds_are_valid_missing_evidence(tmp_path):
    write_csv(tmp_path / "properties.csv", PROPERTY_COLUMNS, [["P1", "100 Main St", ""]])
    write_csv(tmp_path / "county_records.csv", COUNTY_COLUMNS, [])
    write_csv(tmp_path / "entity_records.csv", ENTITY_COLUMNS, [])
    dataset = load_dataset(tmp_path)
    assert dataset.county_records == dataset.entity_records == ()


def test_blank_entity_evidence_is_retained(tmp_path):
    path = write_csv(tmp_path / "entity_records.csv", ENTITY_COLUMNS,
                     [["E1", "P1", "Fictional Entity Feed", "2026-09-15", "", "", "", ""]])
    record, = load_entity_records(path)
    assert record.entity_name == record.entity_normalized == ""


def test_broken_csv_quoting_is_rejected(tmp_path):
    path = tmp_path / "properties.csv"
    path.write_text('property_id,property_address,tenant\nP1,"unterminated\n', encoding="utf-8")
    with pytest.raises(ValueError, match="properties.csv: line"):
        load_properties(path)
