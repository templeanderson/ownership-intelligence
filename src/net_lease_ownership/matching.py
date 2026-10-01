"""Explainable heuristics for fictional research packets, never approval.

Scores prioritize review; explicit evidence rules determine dispositions.
No database writes, UI dependencies, external APIs, or AI calls are used.
"""

from difflib import SequenceMatcher
import re

from .ingestion import Dataset
from .models import CandidateEvidence, CountyRecord, Disposition, EntityRecord, MatchResult, Property
from .normalization import NORMALIZATION_VERSION, is_useful_address, is_useful_name
from .policy import MatchingPolicy


MATCHING_VERSION = "2"

_DIRECTION_IDENTIFIERS = {"north", "south", "east", "west", "northeast", "northwest",
                          "southeast", "southwest", "n", "s", "e", "w", "ne", "nw", "se", "sw"}


def name_similarity(left: str, right: str) -> float | None:
    """Symmetric character similarity of nonempty comparison names.

    SequenceMatcher can depend on argument order. Averaging both directions
    prevents that asymmetry; disabling autojunk avoids length-based shortcuts.
    This measures spelling similarity, not entity identity.
    """
    if not left or not right:
        return None
    return (SequenceMatcher(None, left, right, autojunk=False).ratio()
            + SequenceMatcher(None, right, left, autojunk=False).ratio()) / 2


def _legal_suffix(normalized: str, comparison: str) -> str:
    return normalized.split()[-1] if normalized and normalized != comparison else ""


def _identifier_tokens(name: str) -> tuple[str, ...]:
    # Directional/series identifiers remain significant even if someone adds
    # an unsafe pair such as East/West or I/II to the variation whitelist.
    return tuple(word for word in name.split()
                 if word in _DIRECTION_IDENTIFIERS or re.fullmatch(r"[ivxlcdm]+", word))


def _allowed_name_variation(left: str, right: str, policy: MatchingPolicy) -> bool:
    left_words, right_words = left.split(), right.split()
    if len(left_words) != len(right_words):
        return False
    pairs = set(policy.allowed_name_variations)
    return all(a == b or tuple(sorted((a, b))) in pairs for a, b in zip(left_words, right_words))


def compare_records(property: Property, county: CountyRecord | None, entity: EntityRecord | None,
                    policy: MatchingPolicy | None = None) -> CandidateEvidence:
    policy = policy or MatchingPolicy()
    for record in (county, entity):
        if record is not None and record.property_id != property.property_id:
            raise ValueError(f"{record.source_record_id}: property association does not match {property.property_id}")

    codes: list[str] = []
    explanations: list[str] = []

    def flag(code: str, explanation: str) -> None:
        codes.append(code)
        explanations.append(explanation)

    county_name = county.owner_comparison if county and is_useful_name(county.owner_comparison) else ""
    entity_name = entity.entity_comparison if entity and is_useful_name(entity.entity_comparison) else ""
    county_address = (county.mailing_address_normalized
                      if county and is_useful_address(county.mailing_address_normalized) else "")
    entity_address = (entity.registered_address_normalized
                      if entity and is_useful_address(entity.registered_address_normalized) else "")
    similarity = name_similarity(county_name, entity_name)
    address_match = county_address == entity_address if county_address and entity_address else None

    if county is None:
        flag("missing_county_record", "No county record is available to establish the recorded owner.")
    elif not county_name:
        flag("missing_county_owner", "The county row contains no useful owner comparison name.")
    if entity is None:
        flag("missing_entity_record", "No corporate/entity candidate is available to corroborate the county owner.")
    elif not entity_name:
        flag("missing_entity_name", "The entity row contains no useful entity comparison name.")
    if not county_address:
        flag("missing_county_address", "County mailing address is missing, a placeholder, or unsupported; it cannot corroborate ownership.")
    if not entity_address:
        flag("missing_entity_address", "Entity registered address is missing, a placeholder, or unsupported; it cannot corroborate ownership.")
    if address_match is False:
        flag("address_disagreement", "County mailing and entity registered addresses differ; these roles may legitimately differ.")
    elif address_match is True:
        explanations.append("County mailing and entity registered addresses agree after normalization; this does not prove ownership.")

    accepted_status = bool(entity and entity.entity_status.strip().casefold() in policy.accepted_entity_statuses)
    if entity and not accepted_status:
        flag("entity_status_requires_research",
             f"Entity status {entity.entity_status!r} is outside accepted statuses {policy.accepted_entity_statuses}.")
    distinct_sources = bool(county and entity
                            and county.source_name.strip().casefold() != entity.source_name.strip().casefold())
    if county and entity and not distinct_sources:
        flag("duplicate_source_identity", "Both records have the same source label; independent corroboration is not established.")

    shared_distinctive_tokens: set[str] = set()
    suffix_disagreement = False
    number_disagreement = False
    identifier_disagreement = False
    allowed_variation = False
    if similarity is not None:
        explanations.append(f"Symmetric character name similarity is {similarity:.4f}; ready-for-review threshold is {policy.name_match_threshold:.4f}.")
        shared_distinctive_tokens = (set(county_name.split()) & set(entity_name.split())) - set(policy.generic_name_tokens)
        if similarity == 1:
            explanations.append("Comparison names agree exactly after normalization and removal of terminal legal suffixes.")
        else:
            flag("name_variation", f"Comparison names differ: {county_name!r} versus {entity_name!r}.")
        county_suffix = _legal_suffix(county.owner_normalized, county_name)
        entity_suffix = _legal_suffix(entity.entity_normalized, entity_name)
        suffix_disagreement = bool(county_suffix and entity_suffix and county_suffix != entity_suffix)
        if suffix_disagreement:
            flag("legal_suffix_disagreement", f"Legal suffixes differ ({county_suffix!r} versus {entity_suffix!r}); stripping them must not conceal this difference.")
        number_disagreement = re.findall(r"\d+", county_name) != re.findall(r"\d+", entity_name)
        if number_disagreement:
            flag("name_number_disagreement", "The ordered sequence of numbers within entity names differs, including repetition; high spelling similarity is insufficient.")
        identifier_disagreement = _identifier_tokens(county_name) != _identifier_tokens(entity_name)
        if identifier_disagreement:
            flag("name_identifier_disagreement", "Directional or Roman-series identifiers differ; these may distinguish separate legal entities.")
        allowed_variation = _allowed_name_variation(county_name, entity_name, policy)
        if not allowed_variation:
            flag("unapproved_name_variation", "Name edits fall outside the explicitly permitted word-variation pairs; high character similarity cannot override this.")
        elif similarity != 1:
            explanations.append("Every differing word is an explicitly permitted minor variation; all other readiness checks still apply.")
        if not shared_distinctive_tokens:
            flag("no_shared_distinctive_name_token", "Names share no distinctive whole-word token after excluding configured generic terms.")
        if similarity < policy.name_match_threshold:
            flag("name_below_review_threshold", "Name similarity does not meet the ready-for-review threshold.")
            if address_match is True and shared_distinctive_tokens:
                flag("possible_related_entities", "A shared distinctive name token and address may indicate related entities; no parentage or legal identity is established.")

    corroborated = bool(similarity is not None and similarity >= policy.name_match_threshold
                        and address_match is True and accepted_status and distinct_sources
                        and shared_distinctive_tokens and allowed_variation
                        and not suffix_disagreement and not number_disagreement and not identifier_disagreement)
    if similarity is not None and similarity < policy.name_conflict_threshold and not shared_distinctive_tokens:
        disposition = Disposition.CONFLICT
        explanations.append("Materially different owner/candidate names fall below the conflict threshold without a shared distinctive token.")
    elif corroborated:
        disposition = Disposition.READY_FOR_REVIEW
        explanations.append("Name, address, status, and distinct-source-label checks support human review; approval is still required.")
    else:
        disposition = Disposition.NEEDS_RESEARCH
        explanations.append("Evidence is incomplete, ambiguous, or discrepant; more research is required.")

    score = policy.name_weight * (similarity if similarity is not None else 0) + policy.address_weight * int(address_match is True)
    explanations.append(f"Workflow score = {policy.name_weight:g} × name similarity + {policy.address_weight:g} × address agreement; unknown components contribute zero. Score is not a probability.")
    return CandidateEvidence(county_record=county, entity_record=entity, name_similarity=similarity,
                             confidence_score=round(score, 2), disposition=disposition,
                             discrepancies=tuple(codes), explanations=tuple(explanations))


def reconcile_property(property: Property, county_records: tuple[CountyRecord, ...],
                       entity_records: tuple[EntityRecord, ...],
                       policy: MatchingPolicy | None = None) -> MatchResult:
    """Retain all comparisons and block readiness when candidates are ambiguous."""
    policy = policy or MatchingPolicy()
    for records in (county_records, entity_records):
        if len({record.source_record_id for record in records}) != len(records):
            raise ValueError("Duplicate source-record IDs in a property research packet")
    candidates = [compare_records(property, county, entity, policy)
                  for county in (county_records or (None,))
                  for entity in (entity_records or (None,))]
    priority = {Disposition.READY_FOR_REVIEW: 0, Disposition.NEEDS_RESEARCH: 1, Disposition.CONFLICT: 2}
    candidates.sort(key=lambda item: (item.name_similarity is None, priority[item.disposition], -item.confidence_score,
                                     item.county_record.source_record_id if item.county_record else "",
                                     item.entity_record.source_record_id if item.entity_record else ""))
    leading = candidates[0]
    codes = list(leading.discrepancies)
    explanations = list(leading.explanations)
    plausible_entity_ids = {
        item.entity_record.source_record_id for item in candidates
        if item.entity_record and item.name_similarity is not None
        and item.name_similarity >= policy.ambiguity_name_threshold
    }
    multiple_county_records = len([record for record in county_records if is_useful_name(record.owner_comparison)]) > 1
    ambiguous_entities = len(plausible_entity_ids) > 1
    disposition = leading.disposition
    if ambiguous_entities:
        codes.append("multiple_plausible_entity_candidates")
        explanations.append(f"{len(plausible_entity_ids)} entity candidates meet the ambiguity threshold {policy.ambiguity_name_threshold:g}; ranking does not select the legal owner.")
        disposition = Disposition.NEEDS_RESEARCH
    if multiple_county_records:
        codes.append("multiple_county_owner_records")
        explanations.append("Multiple useful county records require research, even when their names agree; records are not extra independent source types.")
        disposition = Disposition.NEEDS_RESEARCH
    if ambiguous_entities or multiple_county_records:
        explanations.append("Candidate-level agreement cannot override ambiguity in the property research packet.")
    return MatchResult(property=property, candidates=tuple(candidates), disposition=disposition,
                       discrepancies=tuple(codes), explanations=tuple(explanations),
                       policy_version=policy.version, policy_fingerprint=policy.fingerprint,
                       normalization_version=NORMALIZATION_VERSION, matching_version=MATCHING_VERSION)


def reconcile_dataset(dataset: Dataset, policy: MatchingPolicy | None = None) -> tuple[MatchResult, ...]:
    policy = policy or MatchingPolicy()
    property_ids = {property.property_id for property in dataset.properties}
    if len(property_ids) != len(dataset.properties):
        raise ValueError("Duplicate property IDs in dataset")
    for records in (dataset.county_records, dataset.entity_records):
        if len({record.source_record_id for record in records}) != len(records):
            raise ValueError("Duplicate source-record IDs in dataset")
        for record in records:
            if record.property_id not in property_ids:
                raise ValueError(f"{record.source_record_id}: unknown property_id {record.property_id!r}")
    return tuple(reconcile_property(property,
                 tuple(record for record in dataset.county_records if record.property_id == property.property_id),
                 tuple(record for record in dataset.entity_records if record.property_id == property.property_id), policy)
                 for property in dataset.properties)
