from dataclasses import replace
from datetime import datetime
from pathlib import Path
import sqlite3

import pytest

from net_lease_ownership.ingestion import load_dataset
from net_lease_ownership.matching import reconcile_dataset
from net_lease_ownership.models import ReviewStatus
from net_lease_ownership.repository import Repository
from net_lease_ownership.review import StaleReviewError, submit_review


ROOT = Path(__file__).resolve().parents[1]
DECISIONS = [ReviewStatus.APPROVED, ReviewStatus.NEEDS_RESEARCH, ReviewStatus.REJECTED]


@pytest.fixture
def repository(tmp_path):
    with Repository(tmp_path / "ownership.sqlite3") as repository:
        repository.save_results(reconcile_dataset(load_dataset(ROOT / "data")))
        yield repository


def decide(repository, action, property_id="P001", **overrides):
    current = repository.get_record(property_id)
    arguments = dict(reviewer_name="Reviewer A", expected_snapshot_id=current.snapshot_id,
                     expected_revision=current.revision,
                     selected_candidate=0 if action == ReviewStatus.APPROVED else None)
    arguments.update(overrides)
    return submit_review(repository, property_id, action, **arguments)


@pytest.mark.parametrize("initial", [ReviewStatus.UNREVIEWED, *DECISIONS])
@pytest.mark.parametrize("action", DECISIONS)
def test_explicit_transitions_preserve_audit_and_clear_revoked_selection(repository, initial, action):
    if initial != ReviewStatus.UNREVIEWED:
        decide(repository, initial)
    before = repository.get_record("P001")
    history = repository.history("P001")
    updated = decide(repository, action, reviewer_name=" Reviewer B ", note="Reviewed original evidence.")
    assert updated.review_status == action
    assert updated.selected_candidate == (0 if action == ReviewStatus.APPROVED else None)
    assert updated.revision == before.revision + 1
    events = repository.history("P001")
    assert events[:-1] == history
    latest = events[-1]
    assert latest.previous_state == initial
    assert latest.new_state == action
    assert latest.reviewer_name == "Reviewer B"
    assert latest.note == "Reviewed original evidence."
    assert latest.actor_kind == "human"
    assert latest.snapshot_id == before.snapshot_id
    assert latest.selected_candidate == updated.selected_candidate
    assert datetime.fromisoformat(latest.timestamp).utcoffset().total_seconds() == 0


def test_review_and_history_survive_reopening(repository, tmp_path):
    updated = decide(repository, ReviewStatus.APPROVED)
    history = repository.history("P001")
    with Repository(tmp_path / "ownership.sqlite3") as reopened:
        assert reopened.get_record("P001") == updated
        assert reopened.history("P001") == history


@pytest.mark.parametrize("changes", [
    {"reviewer_name": ""}, {"reviewer_name": "  "}, {"reviewer_name": None},
    {"note": 123}, {"expected_snapshot_id": True}, {"expected_revision": 0},
    {"expected_revision": "1"}, {"selected_candidate": None}, {"selected_candidate": -1},
    {"selected_candidate": True}, {"selected_candidate": 99}, {"selected_candidate": "0"},
])
def test_invalid_review_cannot_change_state_or_history(repository, changes):
    before = repository.get_record("P001")
    history = repository.history("P001")
    with pytest.raises(ValueError):
        decide(repository, ReviewStatus.APPROVED, **changes)
    assert repository.get_record("P001") == before
    assert repository.history("P001") == history


@pytest.mark.parametrize("action", [ReviewStatus.UNREVIEWED, "approved", None])
def test_unknown_or_system_only_action_is_rejected(repository, action):
    with pytest.raises(ValueError, match="Choose"):
        decide(repository, action)
    assert repository.get_record("P001").revision == 1


@pytest.mark.parametrize("action", [ReviewStatus.NEEDS_RESEARCH, ReviewStatus.REJECTED])
def test_nonapproval_cannot_retain_candidate_selection(repository, action):
    with pytest.raises(ValueError, match="only valid for approval"):
        decide(repository, action, selected_candidate=0)
    assert repository.get_record("P001").revision == 1


@pytest.mark.parametrize("property_id", ["P004", "P015"])
def test_missing_or_blank_entity_cannot_be_approved(repository, property_id):
    with pytest.raises(ValueError, match="useful entity name"):
        decide(repository, ReviewStatus.APPROVED, property_id)
    assert repository.get_record(property_id).review_status == ReviewStatus.UNREVIEWED


def test_ambiguous_property_approval_uses_explicit_candidate_not_leading_default(repository):
    before = repository.get_record("P013")
    assert before.evidence["disposition"] == "needs_research"
    updated = decide(repository, ReviewStatus.APPROVED, "P013", selected_candidate=1,
                     note="Investigated the second entity candidate.")
    assert updated.selected_candidate == 1
    assert updated.evidence == before.evidence
    assert repository.history("P013")[-1].selected_candidate == 1


@pytest.mark.parametrize("property_id", ["P003", "P010", "P011"])
def test_recommendation_never_substitutes_for_human_decision(repository, property_id):
    # Conflict, single source, and dissolved status remain visible after manual approval.
    before = repository.get_record(property_id)
    updated = decide(repository, ReviewStatus.APPROVED, property_id,
                     note="Investigated and resolved the displayed warnings.")
    assert updated.review_status == ReviewStatus.APPROVED
    assert updated.evidence == before.evidence
    assert updated.evidence["disposition"] != "ready_for_review"


def test_replayed_submission_is_stale_even_with_same_snapshot(repository):
    before = repository.get_record("P001")
    decide(repository, ReviewStatus.APPROVED)
    with pytest.raises(StaleReviewError, match="reload"):
        decide(repository, ReviewStatus.REJECTED, expected_revision=before.revision)
    assert repository.get_record("P001").review_status == ReviewStatus.APPROVED
    assert len(repository.history("P001")) == 2


def test_two_sessions_cannot_overwrite_newer_review(repository, tmp_path):
    with Repository(tmp_path / "ownership.sqlite3") as other:
        stale = other.get_record("P001")
        decide(repository, ReviewStatus.REJECTED)
        with pytest.raises(StaleReviewError):
            decide(other, ReviewStatus.APPROVED, expected_snapshot_id=stale.snapshot_id,
                   expected_revision=stale.revision)
        assert other.get_record("P001").review_status == ReviewStatus.REJECTED
        assert len(other.history("P001")) == 2


def test_stale_evidence_cannot_be_approved(repository):
    stale = repository.get_record("P001")
    results = reconcile_dataset(load_dataset(ROOT / "data"))
    repository.save_results([replace(results[0], matching_version="next")])
    with pytest.raises(StaleReviewError):
        decide(repository, ReviewStatus.APPROVED, expected_snapshot_id=stale.snapshot_id,
               expected_revision=stale.revision)
    assert repository.get_record("P001").review_status == ReviewStatus.UNREVIEWED


def test_failed_audit_insert_rolls_back_approval(repository):
    before = repository.get_record("P001")
    repository._connection.execute("""CREATE TRIGGER fail_review BEFORE INSERT ON review_events
        WHEN NEW.actor_kind = 'human' BEGIN SELECT RAISE(ABORT, 'simulated failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="simulated failure"):
        decide(repository, ReviewStatus.APPROVED)
    assert repository.get_record("P001") == before
    assert len(repository.history("P001")) == 1


def test_reviewer_notes_are_bound_sql_values(repository):
    name = "Reviewer '); DROP TABLE snapshots; --"
    note = "Investigator's evidence\nSecond line"
    decide(repository, ReviewStatus.NEEDS_RESEARCH, reviewer_name=name, note=note)
    event = repository.history("P001")[-1]
    assert event.reviewer_name == name
    assert event.note == note
    assert len(repository.list_records()) == 15


@pytest.mark.parametrize("note", [None, "", "   ", "\n\t"])
@pytest.mark.parametrize("property_id", ["P003", "P010", "P011", "P013"])
def test_override_without_rationale_preserves_state_and_history(repository, note, property_id):
    before = repository.get_record(property_id)
    history = repository.history(property_id)
    with pytest.raises(ValueError, match="requires a reviewer note"):
        decide(repository, ReviewStatus.APPROVED, property_id, note=note)
    assert repository.get_record(property_id) == before
    assert repository.history(property_id) == history


def test_selected_conflict_requires_rationale_even_when_property_is_ready(repository):
    dataset = load_dataset(ROOT / "data")
    from net_lease_ownership.matching import reconcile_property
    unrelated = replace(dataset.entity_records[0], source_record_id="UNRELATED",
                        entity_name="Zephyr Retail Assets LLC")
    result = reconcile_property(dataset.properties[0], (dataset.county_records[0],),
                                (dataset.entity_records[0], unrelated))
    repository.save_results([result])
    before = repository.get_record("P001")
    assert before.evidence["disposition"] == "ready_for_review"
    index = next(i for i, candidate in enumerate(before.evidence["candidates"])
                 if candidate["entity_record"]["source_record_id"] == "UNRELATED")
    assert before.evidence["candidates"][index]["disposition"] == "conflict"
    with pytest.raises(ValueError, match="requires a reviewer note"):
        decide(repository, ReviewStatus.APPROVED, selected_candidate=index)
    assert repository.get_record("P001") == before
    approved = decide(repository, ReviewStatus.APPROVED, selected_candidate=index,
                      note="Additional investigation supports this candidate; recording an override.")
    assert approved.selected_candidate == index
    assert repository.history("P001")[-1].note.startswith("Additional investigation")


def test_repository_decision_method_cannot_bypass_review_validation(repository):
    before = repository.get_record("P003")
    with pytest.raises(ValueError, match="requires a reviewer note"):
        repository.record_review("P003", ReviewStatus.APPROVED, reviewer_name="Reviewer",
            expected_snapshot_id=before.snapshot_id, expected_revision=before.revision, selected_candidate=0)
    assert repository.get_record("P003") == before
