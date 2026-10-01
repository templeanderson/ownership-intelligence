from dataclasses import FrozenInstanceError, asdict
from datetime import date, datetime

import pytest

from net_lease_ownership.models import CountyRecord, EntityRecord, Property, ReviewStatus


def county(**changes):
    values = dict(source_record_id="C1", property_id="P1", source_name="Fictional County Feed",
                  source_as_of=date(2026, 9, 15), owner_name="  ABC Medical, L.L.C.  ",
                  mailing_address="100 Main Street, Dallas, TX 75001")
    return CountyRecord(**(values | changes))


def test_county_preserves_originals_and_derives_comparisons():
    record = county()
    assert record.owner_name == "  ABC Medical, L.L.C.  "
    assert record.owner_normalized == "abc medical llc"
    assert record.owner_comparison == "abc medical"
    assert record.mailing_address == "100 Main Street, Dallas, TX 75001"
    assert record.mailing_address_normalized == "100 main st dallas tx 75001"
    assert record.source_as_of == date(2026, 9, 15)


def test_entity_preserves_roles_and_originals():
    record = EntityRecord("E1", "P1", "Fictional Entity Feed", date(2026, 9, 16),
                          "ABC Medical L.L.C.", "active", "100 Main St.", "Example Agent")
    assert record.entity_name == "ABC Medical L.L.C."
    assert record.entity_normalized == "abc medical llc"
    assert record.entity_comparison == "abc medical"
    assert record.registered_address_normalized == "100 main st"
    assert record.registered_agent == "Example Agent"


def test_sources_are_immutable():
    with pytest.raises(FrozenInstanceError):
        county().owner_name = "Replacement Owner"


def test_missing_evidence_stays_missing():
    record = county(owner_name="", mailing_address="")
    assert record.owner_normalized == record.owner_comparison == ""
    assert record.mailing_address_normalized == ""


@pytest.mark.parametrize("changes", [
    {"source_record_id": ""}, {"property_id": " "}, {"source_name": ""},
    {"owner_name": None}, {"mailing_address": None},
    {"source_as_of": "2026-09-15"}, {"source_as_of": datetime(2026, 9, 15)},
])
def test_invalid_source_values_are_rejected(changes):
    with pytest.raises(ValueError):
        county(**changes)


@pytest.mark.parametrize("property_id,address", [("", "100 Main St"), ("P1", " ")])
def test_property_requires_identity_and_address(property_id, address):
    with pytest.raises(ValueError):
        Property(property_id, address)


def test_source_loading_does_not_assign_review_status():
    assert "review_status" not in asdict(county())
    assert ReviewStatus.UNREVIEWED.value == "unreviewed"
