"""Explicit human decisions, checked against the evidence and revision displayed."""

from .models import ReviewStatus
from .normalization import is_useful_name
from .repository import Repository, ReviewRecord


class StaleReviewError(ValueError):
    """The evidence or review state changed since the reviewer opened the record."""


def submit_review(repository: Repository, property_id: str, action: ReviewStatus, *,
                  reviewer_name: str, expected_snapshot_id: int, expected_revision: int,
                  note: str | None = None, selected_candidate: int | None = None) -> ReviewRecord:
    """Approve, research, or reject. The caller must make an explicit human choice.

    Any current state can receive any human decision. Approval selects an entity
    from the displayed snapshot; matching recommendations never authorize it.
    """
    return repository.record_review(property_id, action, reviewer_name=reviewer_name,
        expected_snapshot_id=expected_snapshot_id, expected_revision=expected_revision,
        note=note, selected_candidate=selected_candidate)


def validate_review_request(action, reviewer_name, expected_snapshot_id, expected_revision,
                            note, selected_candidate) -> None:
    if not isinstance(action, ReviewStatus) or action == ReviewStatus.UNREVIEWED:
        raise ValueError("Choose approved, needs_research, or rejected explicitly")
    if not isinstance(reviewer_name, str) or not reviewer_name.strip():
        raise ValueError("reviewer_name must be a nonblank string")
    if note is not None and not isinstance(note, str):
        raise ValueError("note must be a string or null")
    if any(type(value) is not int or value < 1 for value in (expected_snapshot_id, expected_revision)):
        raise ValueError("Expected snapshot ID and revision must be positive integers")
    if action != ReviewStatus.APPROVED and selected_candidate is not None:
        raise ValueError("Candidate selection is only valid for approval")


def validate_approval(current: ReviewRecord, selected_candidate: int, note: str | None) -> None:
    if (type(selected_candidate) is not int or selected_candidate < 0
            or selected_candidate >= len(current.evidence["candidates"])):
        raise ValueError("Approval requires an explicit valid candidate index")
    candidate = current.evidence["candidates"][selected_candidate]
    entity = candidate["entity_record"]
    if entity is None or not is_useful_name(entity["entity_comparison"]):
        raise ValueError("Approval requires a candidate with a useful entity name")
    if (current.evidence["disposition"] != "ready_for_review"
            or candidate["disposition"] != "ready_for_review") and (note is None or not note.strip()):
        raise ValueError("Approval overriding property or selected candidate warnings requires a reviewer note")
