"""Proposed Salesforce custom-field mapping. Local JSON only; no write transport."""

from datetime import date, datetime, timezone
import hashlib
import json
import math

from .models import Disposition, ReviewStatus
from .normalization import is_useful_name, normalize_company_name
from .repository import Repository
from .review import validate_approval


class PayloadError(ValueError):
    """The current record is not eligible for a proposed Salesforce payload."""


def _required(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PayloadError(f"{label} must be a nonblank string")
    return value


def _source(source: dict | None, property_id: str) -> dict | None:
    if source is None:
        return None
    if source["property_id"] != property_id:
        raise PayloadError("Source record belongs to another property")
    for field in ("source_name", "source_record_id", "source_as_of"):
        _required(source[field], field)
    if date.fromisoformat(source["source_as_of"]).isoformat() != source["source_as_of"]:
        raise PayloadError("Source date must use YYYY-MM-DD")
    return source


def _metadata(evidence: dict, label: str) -> None:
    if not isinstance(evidence["disposition"], str) or evidence["disposition"] not in {
        disposition.value for disposition in Disposition
    }:
        raise PayloadError(f"{label} disposition is invalid")
    discrepancies = evidence["discrepancies"]
    if not isinstance(discrepancies, list) or any(
        not isinstance(code, str) or not code.strip() for code in discrepancies
    ):
        raise PayloadError(f"{label} discrepancies must be a list of nonblank strings")


def generate_payload(repository: Repository, property_id: str, *, dry_run: bool = True,
                     expected_snapshot_id: int | None = None,
                     expected_revision: int | None = None) -> dict:
    """Generate one upsert proposal from current persisted approval, never a draft.

    UI callers pin snapshot/revision to the displayed record. CLI callers use
    the latest persisted state. Eligibility describes the instant of generation;
    downloaded proposals are not authorization for a later production write.
    """
    if dry_run is not True:
        raise PayloadError("Only dry-run export is supported; Salesforce writes are disabled")
    if (expected_snapshot_id is None) != (expected_revision is None):
        raise PayloadError("Supply both expected snapshot ID and revision")
    if expected_snapshot_id is not None and any(
        type(value) is not int or value < 1 for value in (expected_snapshot_id, expected_revision)
    ):
        raise PayloadError("Expected snapshot ID and revision must be positive integers")
    try:
        record, history = repository.get_review_context(property_id)
        if expected_snapshot_id is not None and (record.snapshot_id, record.revision) != (
            expected_snapshot_id, expected_revision
        ):
            raise PayloadError("Property changed; refresh it before exporting")
        if record.review_status != ReviewStatus.APPROVED:
            raise PayloadError("Only a currently approved property can generate a Salesforce payload")
        if not history:
            raise PayloadError("Approval audit event is missing")
        approval = history[-1]
        if (approval.property_id, approval.snapshot_id, approval.revision,
            approval.new_state, approval.selected_candidate, approval.actor_kind) != (
            record.property_id, record.snapshot_id, record.revision,
            ReviewStatus.APPROVED, record.selected_candidate, "human"
        ):
            raise PayloadError("Current approval does not match its human review audit event")
        _required(approval.reviewer_name, "Reviewer name")
        verified = datetime.fromisoformat(approval.timestamp)
        if verified.tzinfo is None or verified.utcoffset() is None:
            raise PayloadError("Approval timestamp must include a timezone")
        try:
            verified_date = verified.astimezone(timezone.utc).date().isoformat()
        except OverflowError as error:
            raise PayloadError("Approval timestamp cannot be represented in UTC") from error
        evidence = record.evidence
        content = json.dumps(evidence, sort_keys=True, separators=(",", ":"),
                             allow_nan=False, ensure_ascii=False)
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != record.fingerprint:
            raise PayloadError("Evidence does not match its preserved fingerprint")
        _metadata(evidence, "Property")
        for key in ("matching_version", "normalization_version", "policy_version", "policy_fingerprint"):
            _required(evidence[key], key)
        policy_fingerprint = evidence["policy_fingerprint"]
        if len(policy_fingerprint) != 64 or any(character not in "0123456789abcdef" for character in policy_fingerprint):
            raise PayloadError("Policy fingerprint must be a SHA-256 hexadecimal string")
        property_record = evidence["property"]
        if property_record["property_id"] != record.property_id:
            raise PayloadError("Evidence belongs to another property")
        _required(record.property_id, "Property ID")
        address = _required(property_record["property_address"], "Property address")
        validate_approval(record, record.selected_candidate, approval.note)
        candidate = evidence["candidates"][record.selected_candidate]
        _metadata(candidate, "Candidate")
        county = _source(candidate["county_record"], record.property_id)
        entity = _source(candidate["entity_record"], record.property_id)
        name = _required(entity["entity_name"], "Ownership entity")
        if not is_useful_name(normalize_company_name(name, remove_legal_suffix=True)):
            raise PayloadError("Ownership entity name is not usable")
        for field in ("entity_status", "registered_agent", "registered_address"):
            if not isinstance(entity[field], str):
                raise PayloadError(f"{field} must be a string")
        score = candidate["confidence_score"]
        if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 100:
            raise PayloadError("Ownership confidence must be finite and between 0 and 100")
        provenance = {
            "county_record": county, "entity_record": entity,
            "snapshot_id": record.snapshot_id, "revision": record.revision,
            "evidence_fingerprint": record.fingerprint,
            "selected_candidate": record.selected_candidate,
            "reviewer_name": approval.reviewer_name, "reviewed_at": approval.timestamp,
            "reviewer_note": approval.note,
            "property_disposition": evidence["disposition"],
            "candidate_disposition": candidate["disposition"],
            "discrepancies": {"property": evidence["discrepancies"], "candidate": candidate["discrepancies"]},
            "versions": {key: evidence[key] for key in (
                "matching_version", "normalization_version", "policy_version", "policy_fingerprint")},
        }
        fields = {
            "External_Property_ID__c": record.property_id,
            "Property_Address__c": address,
            "Ownership_Entity__c": name,
            "Registered_Agent__c": entity["registered_agent"] if entity["registered_agent"].strip() else None,
            "Entity_Status__c": entity["entity_status"] if entity["entity_status"].strip() else None,
            "Ownership_Confidence__c": score,
            "Last_Verified_Date__c": verified_date,
            "Source_Provenance__c": json.dumps(provenance, sort_keys=True, ensure_ascii=False, allow_nan=False),
            "Review_Status__c": "approved",
        }
        return {"schema_version": 1, "dry_run": True, "operation": "upsert",
                "external_id_field": "External_Property_ID__c", "records": [fields]}
    except PayloadError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, AttributeError) as error:
        raise PayloadError(f"Cannot export malformed or unavailable approved record: {error}") from error
