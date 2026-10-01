"""Immutable source evidence and matching recommendations.

Matching dispositions and human review statuses describe separate workflows.
Source models carry neither approval nor a claim that evidence is trusted.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
import math

from .normalization import is_useful_address, is_useful_name, normalize_address, normalize_company_name


class Disposition(str, Enum):
    READY_FOR_REVIEW = "ready_for_review"
    NEEDS_RESEARCH = "needs_research"
    CONFLICT = "conflict"


class ReviewStatus(str, Enum):
    UNREVIEWED = "unreviewed"
    APPROVED = "approved"
    NEEDS_RESEARCH = "needs_research"
    REJECTED = "rejected"


def _required(**values: str) -> None:
    for name, value in values.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonblank string")


def _optional(**values: str) -> None:
    for name, value in values.items():
        if not isinstance(value, str):
            raise ValueError(f"{name} must be a string; use '' for missing data")


def _source_date(value: date) -> None:
    if type(value) is not date:
        raise ValueError("source_as_of must be a date")


def _validate_recommendation(source_count: int, score: float, disposition: Disposition) -> None:
    if type(source_count) is not int or not 0 <= source_count <= 2:
        raise ValueError("source_count must be an integer between 0 and 2")
    if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 100:
        raise ValueError("confidence_score must be finite and between 0 and 100")
    if not isinstance(disposition, Disposition):
        raise ValueError("disposition must be a Disposition")


def _set_corroboration(record) -> None:
    corroborated = record.disposition == Disposition.READY_FOR_REVIEW
    object.__setattr__(record, "corroborated", corroborated)
    object.__setattr__(record, "corroborating_source_count", 2 if corroborated else 0)


@dataclass(frozen=True)
class Property:
    property_id: str
    property_address: str
    tenant: str = ""

    def __post_init__(self) -> None:
        _required(property_id=self.property_id, property_address=self.property_address)
        _optional(tenant=self.tenant)


@dataclass(frozen=True)
class CountyRecord:
    source_record_id: str
    property_id: str
    source_name: str
    source_as_of: date
    owner_name: str
    mailing_address: str = ""
    owner_normalized: str = field(init=False)
    owner_comparison: str = field(init=False)
    mailing_address_normalized: str = field(init=False)

    def __post_init__(self) -> None:
        _required(source_record_id=self.source_record_id, property_id=self.property_id,
                  source_name=self.source_name)
        _source_date(self.source_as_of)
        _optional(owner_name=self.owner_name, mailing_address=self.mailing_address)
        object.__setattr__(self, "owner_normalized", normalize_company_name(self.owner_name))
        object.__setattr__(self, "owner_comparison",
                           normalize_company_name(self.owner_name, remove_legal_suffix=True))
        object.__setattr__(self, "mailing_address_normalized", normalize_address(self.mailing_address))


@dataclass(frozen=True)
class EntityRecord:
    source_record_id: str
    property_id: str
    source_name: str
    source_as_of: date
    entity_name: str
    entity_status: str = ""
    registered_address: str = ""
    registered_agent: str = ""
    entity_normalized: str = field(init=False)
    entity_comparison: str = field(init=False)
    registered_address_normalized: str = field(init=False)

    def __post_init__(self) -> None:
        _required(source_record_id=self.source_record_id, property_id=self.property_id,
                  source_name=self.source_name)
        _source_date(self.source_as_of)
        _optional(entity_name=self.entity_name, entity_status=self.entity_status,
                  registered_address=self.registered_address, registered_agent=self.registered_agent)
        object.__setattr__(self, "entity_normalized", normalize_company_name(self.entity_name))
        object.__setattr__(self, "entity_comparison",
                           normalize_company_name(self.entity_name, remove_legal_suffix=True))
        object.__setattr__(self, "registered_address_normalized", normalize_address(self.registered_address))


@dataclass(frozen=True)
class CandidateEvidence:
    """One county/entity comparison; absent records remain explicit nulls."""

    county_record: CountyRecord | None
    entity_record: EntityRecord | None
    name_similarity: float | None
    address_match: bool | None = field(init=False)
    source_count: int = field(init=False)
    corroborating_source_count: int = field(init=False)
    corroborated: bool = field(init=False)
    confidence_score: float
    disposition: Disposition
    discrepancies: tuple[str, ...]
    explanations: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.county_record is not None and not isinstance(self.county_record, CountyRecord):
            raise ValueError("county_record must be a CountyRecord or null")
        if self.entity_record is not None and not isinstance(self.entity_record, EntityRecord):
            raise ValueError("entity_record must be an EntityRecord or null")
        county_name = self.county_record.owner_comparison if self.county_record else ""
        entity_name = self.entity_record.entity_comparison if self.entity_record else ""
        object.__setattr__(self, "source_count", int(is_useful_name(county_name)) + int(is_useful_name(entity_name)))
        county_address = self.county_record.mailing_address_normalized if self.county_record else ""
        entity_address = self.entity_record.registered_address_normalized if self.entity_record else ""
        agreement = (county_address == entity_address
                     if is_useful_address(county_address) and is_useful_address(entity_address) else None)
        object.__setattr__(self, "address_match", agreement)
        _validate_recommendation(self.source_count, self.confidence_score, self.disposition)
        if self.name_similarity is not None and (
            type(self.name_similarity) not in (int, float) or not math.isfinite(self.name_similarity)
            or not 0 <= self.name_similarity <= 1
        ):
            raise ValueError("name_similarity must be null or finite and between 0 and 1")
        if (self.name_similarity is None) != (self.source_count != 2):
            raise ValueError("Similarity availability must agree with useful name evidence")
        if self.disposition == Disposition.READY_FOR_REVIEW and (
            self.source_count != 2 or self.name_similarity is None or self.address_match is not True
        ):
            raise ValueError("Ready candidate requires two useful sources, name evidence, and address agreement")
        _set_corroboration(self)


@dataclass(frozen=True)
class MatchResult:
    """Property-level recommendation retaining every candidate's evidence.

    Candidates are ranked for display only. No ownership entity is approved or
    selected for export, even when the recommendation is ready_for_review.
    """

    property: Property
    candidates: tuple[CandidateEvidence, ...]
    source_count: int = field(init=False)
    corroborating_source_count: int = field(init=False)
    corroborated: bool = field(init=False)
    confidence_score: float = field(init=False)
    disposition: Disposition
    discrepancies: tuple[str, ...]
    explanations: tuple[str, ...]
    policy_version: str
    policy_fingerprint: str
    normalization_version: str
    matching_version: str
    review_status: ReviewStatus = field(default=ReviewStatus.UNREVIEWED, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.candidates, (tuple, list)) or not self.candidates:
            raise ValueError("MatchResult requires at least one candidate comparison")
        if any(not isinstance(candidate, CandidateEvidence) for candidate in self.candidates):
            raise ValueError("candidates must contain CandidateEvidence")
        object.__setattr__(self, "candidates", tuple(self.candidates))
        for candidate in self.candidates:
            for record in (candidate.county_record, candidate.entity_record):
                if record is not None and record.property_id != self.property.property_id:
                    raise ValueError("Candidate evidence must belong to the result's property")
        county_present = any(candidate.county_record and is_useful_name(candidate.county_record.owner_comparison)
                             for candidate in self.candidates)
        entity_present = any(candidate.entity_record and is_useful_name(candidate.entity_record.entity_comparison)
                             for candidate in self.candidates)
        object.__setattr__(self, "source_count", int(county_present) + int(entity_present))
        object.__setattr__(self, "confidence_score", self.leading_candidate.confidence_score)
        _validate_recommendation(self.source_count, self.confidence_score, self.disposition)
        if self.disposition == Disposition.READY_FOR_REVIEW and (
            self.source_count != 2 or not self.leading_candidate.corroborated
        ):
            raise ValueError("Ready property requires a corroborated leading candidate and two useful sources")
        _set_corroboration(self)

    @property
    def property_id(self) -> str:
        return self.property.property_id

    @property
    def property_address(self) -> str:
        return self.property.property_address

    @property
    def leading_candidate(self) -> CandidateEvidence:
        """Best rule-qualified comparison; a display aid, not an ownership decision."""
        return self.candidates[0]

    @property
    def name_similarity(self) -> float | None:
        return self.leading_candidate.name_similarity

    @property
    def address_match(self) -> bool | None:
        return self.leading_candidate.address_match
