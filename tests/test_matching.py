from dataclasses import FrozenInstanceError, replace
from datetime import date
from pathlib import Path

import pytest

from net_lease_ownership.ingestion import Dataset, load_dataset
from net_lease_ownership.matching import compare_records, name_similarity, reconcile_dataset, reconcile_property
from net_lease_ownership.models import CountyRecord, Disposition, EntityRecord, Property, ReviewStatus
from net_lease_ownership.policy import MatchingPolicy, load_policy


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def records():
    property = Property("P1", "101 Fictional Way, Dallas, TX 75001", "Example Clinic")
    county = CountyRecord("C1", "P1", "Fictional County Feed", date(2026, 9, 15),
                          "ABC Medical Holdings LLC", "100 Main Street, Dallas, TX 75001")
    entity = EntityRecord("E1", "P1", "Fictional Entity Feed", date(2026, 9, 16),
                          "ABC MEDICAL HOLDINGS, L.L.C.", "active",
                          "100 MAIN ST., DALLAS, TX 75001", "Example Agent")
    return property, county, entity


def test_exact_match_preserves_evidence_and_is_not_approved(records):
    property, county, entity = records
    result = reconcile_property(property, (county,), (entity,))
    assert result.name_similarity == 1
    assert result.address_match is True
    assert result.confidence_score == 100
    assert result.disposition == Disposition.READY_FOR_REVIEW
    assert result.source_count == result.corroborating_source_count == 2
    assert result.corroborated is True
    assert result.review_status == ReviewStatus.UNREVIEWED
    assert result.leading_candidate.county_record is county
    assert result.leading_candidate.entity_record is entity
    assert result.leading_candidate.entity_record.entity_name == "ABC MEDICAL HOLDINGS, L.L.C."
    assert result.policy_fingerprint == MatchingPolicy().fingerprint
    assert result.matching_version == result.normalization_version == "2"


def test_strong_fuzzy_match_explains_the_variation(records):
    property, county, entity = records
    county = replace(county, owner_name="Sunrise Property Group LLC")
    entity = replace(entity, entity_name="Sunrise Properties Group, LLC")
    result = reconcile_property(property, (county,), (entity,))
    assert 0.90 < result.name_similarity < 1
    assert result.confidence_score == 93.91
    assert result.disposition == Disposition.READY_FOR_REVIEW
    assert "name_variation" in result.discrepancies
    assert any("sunrise property group" in explanation for explanation in result.explanations)


def test_same_name_different_addresses_cannot_be_ready(records):
    property, county, entity = records
    entity = replace(entity, registered_address="999 Different Road, Austin, TX 78701")
    result = reconcile_property(property, (county,), (entity,))
    assert result.name_similarity == 1
    assert result.address_match is False
    assert result.confidence_score == 70
    assert result.disposition == Disposition.NEEDS_RESEARCH
    assert "address_disagreement" in result.discrepancies
    assert not result.corroborated
    assert result.corroborating_source_count == 0


def test_similar_names_with_different_addresses_need_research(records):
    property, county, entity = records
    county = replace(county, owner_name="Sunrise Property Group LLC")
    entity = replace(entity, entity_name="Sunrise Properties Group LLC", registered_address="200 Other Road")
    result = reconcile_property(property, (county,), (entity,))
    assert result.name_similarity > 0.90
    assert result.disposition == Disposition.NEEDS_RESEARCH


def test_materially_conflicting_names_are_not_rescued_by_address(records):
    property, county, entity = records
    county = replace(county, owner_name="DFW Healthcare Properties LLC")
    entity = replace(entity, entity_name="Lone Star Healthcare Holdings LLC")
    result = reconcile_property(property, (county,), (entity,))
    assert result.address_match is True
    assert result.disposition == Disposition.CONFLICT
    assert not result.corroborated


def test_possible_parent_is_a_research_hint_not_an_identity_match(records):
    property, county, entity = records
    county = replace(county, owner_name="Sunrise Dallas Property LLC")
    entity = replace(entity, entity_name="Sunrise Investment Group LLC")
    result = reconcile_property(property, (county,), (entity,))
    assert result.disposition == Disposition.NEEDS_RESEARCH
    assert "possible_related_entities" in result.discrepancies
    assert result.leading_candidate.county_record.owner_name == "Sunrise Dallas Property LLC"
    assert any("no parentage" in explanation for explanation in result.explanations)


@pytest.mark.parametrize("which,expected_count", [("county", 1), ("entity", 1), ("both", 0)])
def test_missing_sources_are_research(records, which, expected_count):
    property, county, entity = records
    counties = () if which in ("county", "both") else (county,)
    entities = () if which in ("entity", "both") else (entity,)
    result = reconcile_property(property, counties, entities)
    assert result.disposition == Disposition.NEEDS_RESEARCH
    assert result.source_count == expected_count
    assert result.name_similarity is None
    assert result.address_match is None
    assert result.confidence_score == 0


@pytest.mark.parametrize("value", ["", " ", "LLC", "!!!"])
def test_empty_or_unusable_entity_name_is_not_a_useful_source(records, value):
    property, county, entity = records
    result = reconcile_property(property, (county,), (replace(entity, entity_name=value),))
    assert result.source_count == 1
    assert result.name_similarity is None
    assert result.disposition == Disposition.NEEDS_RESEARCH
    assert "missing_entity_name" in result.discrepancies


def test_both_blank_names_and_addresses_do_not_corroborate(records):
    property, county, entity = records
    result = reconcile_property(property, (replace(county, owner_name="", mailing_address=""),),
                                (replace(entity, entity_name="", registered_address=""),))
    assert result.source_count == 0
    assert result.address_match is None
    assert result.name_similarity is None
    assert result.confidence_score == 0
    assert not result.corroborated


@pytest.mark.parametrize("county_address,entity_address", [("", ""), ("", "100 Main St"), ("100 Main St", "")])
def test_missing_addresses_are_unknown_not_equal(records, county_address, entity_address):
    property, county, entity = records
    result = reconcile_property(property, (replace(county, mailing_address=county_address),),
                                (replace(entity, registered_address=entity_address),))
    assert result.address_match is None
    assert result.disposition == Disposition.NEEDS_RESEARCH
    assert result.confidence_score == 70


def test_different_suite_numbers_block_readiness(records):
    property, county, entity = records
    result = reconcile_property(property, (replace(county, mailing_address="100 Main St Suite 200"),),
                                (replace(entity, registered_address="100 Main Street Suite 201"),))
    assert result.address_match is False
    assert result.disposition == Disposition.NEEDS_RESEARCH


@pytest.mark.parametrize("status", ["dissolved", "inactive", "unknown", "", "pending"])
def test_unaccepted_entity_status_blocks_even_score_100(records, status):
    property, county, entity = records
    result = reconcile_property(property, (county,), (replace(entity, entity_status=status),))
    assert result.confidence_score == 100
    assert result.disposition == Disposition.NEEDS_RESEARCH
    assert "entity_status_requires_research" in result.discrepancies


def test_status_comparison_handles_case_and_whitespace(records):
    property, county, entity = records
    result = reconcile_property(property, (county,), (replace(entity, entity_status=" ACTIVE "),))
    assert result.disposition == Disposition.READY_FOR_REVIEW


def test_duplicate_source_labels_do_not_count_as_independent_agreement(records):
    property, county, entity = records
    entity = replace(entity, source_name=" fictional county feed ")
    result = reconcile_property(property, (county,), (entity,))
    assert result.source_count == 2  # Useful source types, not an independence assertion.
    assert result.corroborating_source_count == 0
    assert "duplicate_source_identity" in result.discrepancies
    assert result.disposition == Disposition.NEEDS_RESEARCH


def test_differing_legal_suffixes_are_not_hidden(records):
    property, county, entity = records
    result = reconcile_property(property, (county,), (replace(entity, entity_name="ABC Medical Holdings Inc."),))
    assert result.name_similarity == 1
    assert "legal_suffix_disagreement" in result.discrepancies
    assert result.disposition == Disposition.NEEDS_RESEARCH


def test_differing_company_numbers_block_a_high_similarity(records):
    property, county, entity = records
    result = reconcile_property(property, (replace(county, owner_name="Sunrise Property 101 LLC"),),
                                (replace(entity, entity_name="Sunrise Property 102 LLC"),))
    assert result.name_similarity > 0.90
    assert "name_number_disagreement" in result.discrepancies
    assert result.disposition == Disposition.NEEDS_RESEARCH


def test_generic_terms_alone_are_insufficient(records):
    property, county, entity = records
    result = reconcile_property(property, (replace(county, owner_name="Medical Properties LLC"),),
                                (replace(entity, entity_name="Medical Properties LLC"),))
    assert result.confidence_score == 100
    assert result.disposition == Disposition.NEEDS_RESEARCH
    assert "no_shared_distinctive_name_token" in result.discrepancies


def test_multiple_plausible_candidates_preserve_all_evidence_and_block_readiness(records):
    property, county, entity = records
    other = replace(entity, source_record_id="E2", entity_name="ABC Medical Holding LLC")
    result = reconcile_property(property, (county,), (entity, other))
    assert len(result.candidates) == 2
    assert result.leading_candidate.entity_record is entity
    assert result.leading_candidate.corroborated  # Pair-level agreement survives.
    assert not result.corroborated  # Property-level ambiguity blocks readiness.
    assert result.corroborating_source_count == 0
    assert result.source_count == 2
    assert result.disposition == Disposition.NEEDS_RESEARCH
    assert "multiple_plausible_entity_candidates" in result.discrepancies
    assert reconcile_property(property, (county,), (other, entity)) == result


def test_similar_candidate_at_another_address_still_requires_research(records):
    property, county, entity = records
    other = replace(entity, source_record_id="E2", registered_address="999 Other Road")
    result = reconcile_property(property, (county,), (entity, other))
    assert result.disposition == Disposition.NEEDS_RESEARCH
    assert "multiple_plausible_entity_candidates" in result.discrepancies


def test_unrelated_candidate_does_not_override_a_strong_candidate(records):
    property, county, entity = records
    other = replace(entity, source_record_id="E2", entity_name="Zephyr Retail Assets LLC")
    result = reconcile_property(property, (county,), (other, entity))
    assert len(result.candidates) == 2
    assert result.disposition == Disposition.READY_FOR_REVIEW
    assert any(candidate.disposition == Disposition.CONFLICT for candidate in result.candidates)


def test_multiple_county_rows_do_not_inflate_sources(records):
    property, county, entity = records
    other = replace(county, source_record_id="C2")
    result = reconcile_property(property, (county, other), (entity,))
    assert result.source_count == 2
    assert result.disposition == Disposition.NEEDS_RESEARCH
    assert "multiple_county_owner_records" in result.discrepancies


def test_configured_threshold_changes_recommendation(records):
    property, county, entity = records
    county = replace(county, owner_name="Sunrise Property Group LLC")
    entity = replace(entity, entity_name="Sunrise Properties Group LLC")
    strict = MatchingPolicy(name_match_threshold=0.95)
    result = reconcile_property(property, (county,), (entity,), strict)
    assert result.disposition == Disposition.NEEDS_RESEARCH
    assert result.policy_fingerprint == strict.fingerprint


def test_weights_change_score_but_cannot_override_address_requirement(records):
    property, county, entity = records
    entity = replace(entity, registered_address="999 Other Road")
    policy = MatchingPolicy(name_weight=100, address_weight=0)
    result = reconcile_property(property, (county,), (entity,), policy)
    assert result.confidence_score == 100
    assert result.disposition == Disposition.NEEDS_RESEARCH


def test_address_only_weight_cannot_rank_an_unrelated_candidate_over_a_valid_one(records):
    property, county, entity = records
    other = replace(entity, source_record_id="E0", entity_name="Zephyr Retail Assets LLC")
    policy = MatchingPolicy(name_weight=0, address_weight=100)
    result = reconcile_property(property, (county,), (other, entity), policy)
    assert all(candidate.confidence_score == 100 for candidate in result.candidates)
    assert result.leading_candidate.entity_record is entity
    assert result.disposition == Disposition.READY_FOR_REVIEW


def test_name_threshold_boundaries_use_full_precision(records):
    property, county, entity = records
    county = replace(county, owner_name="Sunrise Property Group LLC")
    entity = replace(entity, entity_name="Sunrise Properties Group LLC")
    similarity = name_similarity(county.owner_comparison, entity.entity_comparison)
    equal = MatchingPolicy(name_match_threshold=similarity)
    above = MatchingPolicy(name_match_threshold=similarity + 0.000001)
    assert compare_records(property, county, entity, equal).disposition == Disposition.READY_FOR_REVIEW
    assert compare_records(property, county, entity, above).disposition == Disposition.NEEDS_RESEARCH


def test_configured_status_changes_the_accepted_status(records):
    property, county, entity = records
    policy = MatchingPolicy(accepted_entity_statuses=("active", "good standing"))
    result = reconcile_property(property, (county,), (replace(entity, entity_status="good standing"),), policy)
    assert result.disposition == Disposition.READY_FOR_REVIEW


@pytest.mark.parametrize("left,right", [("tide", "diet"), ("sunrise property", "sunrise properties")])
def test_similarity_does_not_depend_on_argument_order(left, right):
    assert name_similarity(left, right) == name_similarity(right, left)


def test_missing_names_have_unknown_similarity():
    assert name_similarity("", "") is None
    assert name_similarity("abc", "") is None


def test_cross_property_record_is_rejected(records):
    property, county, entity = records
    with pytest.raises(ValueError, match="property association"):
        compare_records(property, county, replace(entity, property_id="P2"))


def test_duplicate_record_ids_are_rejected(records):
    property, county, entity = records
    with pytest.raises(ValueError, match="Duplicate source-record"):
        reconcile_property(property, (county,), (entity, entity))


def test_direct_dataset_cannot_silently_drop_unknown_property_evidence(records):
    property, county, entity = records
    dataset = Dataset((property,), (county,), (replace(entity, property_id="P2"),))
    with pytest.raises(ValueError, match="unknown property_id"):
        reconcile_dataset(dataset)


def test_all_fifteen_sample_outcomes_and_review_status():
    dataset = load_dataset(ROOT / "data")
    results = reconcile_dataset(dataset, load_policy(ROOT / "config/matching.toml"))
    expected_ready = {"P001", "P002", "P006", "P007", "P008", "P009"}
    for result in results:
        expected = (Disposition.READY_FOR_REVIEW if result.property_id in expected_ready
                    else Disposition.CONFLICT if result.property_id == "P003"
                    else Disposition.NEEDS_RESEARCH)
        assert result.disposition == expected, result.property_id
        assert result.review_status == ReviewStatus.UNREVIEWED
        assert result.explanations
        assert 0 <= result.confidence_score <= 100
    assert len(results) == 15


def test_result_disallows_ordinary_attribute_assignment(records):
    property, county, entity = records
    result = reconcile_property(property, (county,), (entity,))
    with pytest.raises(FrozenInstanceError):
        result.review_status = ReviewStatus.APPROVED


@pytest.mark.parametrize("marker", ["Unknown", "N/A", "TBD", "Unknown LLC", "Not Available"])
def test_placeholder_names_are_not_useful_sources(records, marker):
    property, county, entity = records
    result = reconcile_property(property, (replace(county, owner_name=marker),),
                                (replace(entity, entity_name=marker),))
    assert result.source_count == 0
    assert result.name_similarity is None
    assert result.disposition == Disposition.NEEDS_RESEARCH
    assert not result.corroborated
    assert result.leading_candidate.county_record.owner_name == marker
    assert result.leading_candidate.entity_record.entity_name == marker


@pytest.mark.parametrize("marker", ["N/A", "unknown", "0", "PO Box", "TBD", "Unknown, Dallas, TX 75001"])
def test_placeholder_and_unsupported_addresses_cannot_corroborate(records, marker):
    property, county, entity = records
    result = reconcile_property(property, (replace(county, mailing_address=marker),),
                                (replace(entity, registered_address=marker),))
    assert result.name_similarity == 1
    assert result.address_match is None
    assert result.confidence_score == 70
    assert result.disposition == Disposition.NEEDS_RESEARCH
    assert result.leading_candidate.county_record.mailing_address == marker
    assert "missing_county_address" in result.discrepancies
    assert "missing_entity_address" in result.discrepancies


@pytest.mark.parametrize("left,right", [
    ("Sunrise Dallas Medical Holdings East LLC", "Sunrise Dallas Medical Holdings West LLC"),
    ("Sunrise Dallas Medical Holdings I LLC", "Sunrise Dallas Medical Holdings II LLC"),
    ("Sunrise Dallas Medical Holdings LLC", "Sunrise Dallas Medical Holdings A LLC"),
    ("Sunrise Dallas Medical Holdings LLC", "Sunrise Dallas Medical Holdingz LLC"),
])
def test_high_character_similarity_does_not_approve_unlisted_name_edits(records, left, right):
    property, county, entity = records
    result = reconcile_property(property, (replace(county, owner_name=left),),
                                (replace(entity, entity_name=right),))
    assert result.name_similarity > 0.90
    assert result.address_match is True
    assert result.disposition == Disposition.NEEDS_RESEARCH
    assert "unapproved_name_variation" in result.discrepancies


@pytest.mark.parametrize("left,right", [
    ("Sunrise Dallas Medical Property Holdings 10 20 LLC", "Sunrise Dallas Medical Property Holdings 20 10 LLC"),
    ("Sunrise Dallas Medical Property Holdings 10 LLC", "Sunrise Dallas Medical Property Holdings 10 10 LLC"),
])
def test_numeric_sequence_order_and_repetition_distinguish_entities(records, left, right):
    property, county, entity = records
    result = reconcile_property(property, (replace(county, owner_name=left),),
                                (replace(entity, entity_name=right),))
    assert result.name_similarity > 0.90
    assert result.disposition == Disposition.NEEDS_RESEARCH
    assert "name_number_disagreement" in result.discrepancies


@pytest.mark.parametrize("left,right", [("East", "West"), ("I", "II")])
def test_identifier_guard_cannot_be_disabled_by_variation_whitelist(records, left, right):
    property, county, entity = records
    policy = MatchingPolicy(allowed_name_variations=((left, right),))
    county = replace(county, owner_name=f"Sunrise Dallas Medical Holdings {left} LLC")
    entity = replace(entity, entity_name=f"Sunrise Dallas Medical Holdings {right} LLC")
    result = reconcile_property(property, (county,), (entity,), policy)
    assert result.disposition == Disposition.NEEDS_RESEARCH
    assert "name_identifier_disagreement" in result.discrepancies


def test_empty_variation_whitelist_still_allows_exact_names(records):
    property, county, entity = records
    policy = MatchingPolicy(allowed_name_variations=())
    result = reconcile_property(property, (county,), (entity,), policy)
    assert result.disposition == Disposition.READY_FOR_REVIEW
    county = replace(county, owner_name="Sunrise Property Group LLC")
    entity = replace(entity, entity_name="Sunrise Properties Group LLC")
    result = reconcile_property(property, (county,), (entity,), policy)
    assert result.disposition == Disposition.NEEDS_RESEARCH


@pytest.mark.parametrize("name", ["", "Unknown", "N/A"])
def test_incomplete_entity_candidate_cannot_hide_a_conflict(records, name):
    property, county, entity = records
    county = replace(county, owner_name="DFW Healthcare Properties LLC")
    entity = replace(entity, entity_name="Lone Star Healthcare Holdings LLC")
    incomplete = replace(entity, source_record_id="E0", entity_name=name,
                         registered_address="", entity_status="unknown")
    baseline = reconcile_property(property, (county,), (entity,))
    result = reconcile_property(property, (county,), (incomplete, entity))
    assert result.disposition == baseline.disposition == Disposition.CONFLICT
    assert result.confidence_score == baseline.confidence_score == 68.89
    assert result.discrepancies == baseline.discrepancies
    assert result.leading_candidate.entity_record is entity
    assert len(result.candidates) == 2
    assert result.candidates[1].entity_record is incomplete
    assert result.source_count == 2
    assert reconcile_property(property, (county,), (entity, incomplete)) == result


def test_placeholder_county_row_does_not_create_false_ambiguity(records):
    property, county, entity = records
    extra = replace(county, source_record_id="C2", owner_name="Unknown")
    result = reconcile_property(property, (county, extra), (entity,))
    assert result.disposition == Disposition.READY_FOR_REVIEW
    assert "multiple_county_owner_records" not in result.discrepancies
    assert len(result.candidates) == 2


def test_directional_city_names_do_not_drift_when_reloaded(records):
    property, county, entity = records
    county = replace(county, mailing_address="100 Main Street, West Lake, TX 75001")
    entity = replace(entity, registered_address=county.mailing_address_normalized)
    result = reconcile_property(property, (county,), (entity,))
    assert result.address_match is True
    assert result.disposition == Disposition.READY_FOR_REVIEW


def test_derived_recommendation_fields_cannot_be_supplied_independently(records):
    property, county, entity = records
    result = reconcile_property(property, (county,), (entity,))
    for field, value in [("corroborated", False), ("corroborating_source_count", 0),
                         ("source_count", 0), ("confidence_score", 0), ("review_status", ReviewStatus.APPROVED)]:
        with pytest.raises(ValueError, match="init=False"):
            replace(result, **{field: value})
    for field, value in [("corroborated", False), ("corroborating_source_count", 0),
                         ("source_count", 0), ("address_match", False)]:
        with pytest.raises(ValueError, match="init=False"):
            replace(result.leading_candidate, **{field: value})


@pytest.mark.parametrize("score", [-1, 101, float("nan"), float("inf"), True])
def test_malformed_candidate_scores_are_rejected(records, score):
    property, county, entity = records
    candidate = compare_records(property, county, entity)
    with pytest.raises(ValueError, match="confidence_score"):
        replace(candidate, confidence_score=score)


def test_empty_match_result_is_rejected(records):
    property, county, entity = records
    result = reconcile_property(property, (county,), (entity,))
    with pytest.raises(ValueError, match="at least one"):
        replace(result, candidates=())


def test_ready_property_cannot_have_an_uncorroborated_leading_candidate(records):
    property, county, entity = records
    entity = replace(entity, registered_address="999 Other Road")
    result = reconcile_property(property, (county,), (entity,))
    with pytest.raises(ValueError, match="corroborated leading candidate"):
        replace(result, disposition=Disposition.READY_FOR_REVIEW)
