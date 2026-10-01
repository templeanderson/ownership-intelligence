"""Small immutable source models, with original and computed comparison fields.

Matching dispositions and review statuses describe separate future workflows.
Source models carry neither approval nor a claim that evidence is trusted.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from .normalization import normalize_address, normalize_company_name


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
