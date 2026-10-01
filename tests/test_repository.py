from dataclasses import replace
from datetime import datetime
from pathlib import Path
import sqlite3

import pytest

from net_lease_ownership.ingestion import load_dataset
from net_lease_ownership.matching import reconcile_dataset
from net_lease_ownership.models import ReviewStatus
from net_lease_ownership.repository import APPLICATION_ID, Repository
from net_lease_ownership.review import submit_review


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def results():
    return reconcile_dataset(load_dataset(ROOT / "data"))


def approve(repository, property_id="P001"):
    record = repository.get_record(property_id)
    return submit_review(repository, property_id, ReviewStatus.APPROVED,
                         reviewer_name="Example Reviewer", selected_candidate=0,
                         expected_snapshot_id=record.snapshot_id, expected_revision=record.revision)


def test_round_trip_preserves_originals_analysis_and_unreviewed_state(tmp_path, results):
    path = tmp_path / "ownership.sqlite3"
    with Repository(path) as repository:
        repository.save_results(results)
    with Repository(path) as repository:
        records = repository.list_records()
        assert len(records) == 15
        assert all(record.review_status == ReviewStatus.UNREVIEWED for record in records)
        first = repository.get_record("P001")
        entity = first.evidence["candidates"][0]["entity_record"]
        assert entity["entity_name"] == "ABC MEDICAL HOLDINGS, L.L.C."
        assert entity["entity_normalized"] == "abc medical holdings llc"
        assert entity["source_as_of"] == "2026-09-16"
        assert first.evidence["policy_fingerprint"] == results[0].policy_fingerprint
        assert first.evidence["matching_version"] == "2"
        assert len(repository.get_record("P013").evidence["candidates"]) == 2
        event, = repository.history("P001")
        assert event.previous_state is None
        assert event.new_state == ReviewStatus.UNREVIEWED
        assert event.actor_kind == "system"
        assert event.reviewer_name is None
        assert datetime.fromisoformat(event.timestamp).utcoffset().total_seconds() == 0


def test_identical_reload_preserves_approval_revision_and_history(tmp_path, results):
    with Repository(tmp_path / "store.sqlite3") as repository:
        repository.save_results(results)
        approved = approve(repository)
        before = repository.history("P001")
        repository.save_results(results)
        assert repository.get_record("P001") == approved
        assert repository.history("P001") == before
        assert repository._connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 15


@pytest.mark.parametrize("change", ["original", "date", "policy", "algorithm", "property", "candidate"])
def test_changed_evidence_invalidates_approval_and_preserves_old_snapshot(tmp_path, results, change):
    first = results[0]
    candidate = first.leading_candidate
    if change == "original":
        candidate = replace(candidate, entity_record=replace(candidate.entity_record,
                            entity_name="ABC Medical Holdings LLC"))  # Same normalized name, different evidence.
    elif change == "date":
        candidate = replace(candidate, entity_record=replace(candidate.entity_record,
                            source_as_of=candidate.entity_record.source_as_of.replace(day=17)))
    elif change == "candidate":
        candidate = replace(candidate, entity_record=replace(candidate.entity_record, source_record_id="NEW"))
    changed = replace(first, candidates=(candidate,))
    if change == "policy":
        changed = replace(changed, policy_fingerprint="changed-settings")
    elif change == "algorithm":
        changed = replace(changed, matching_version="3")
    elif change == "property":
        changed = replace(changed, property=replace(first.property, tenant="New fictional tenant"))
    with Repository(tmp_path / "store.sqlite3") as repository:
        repository.save_results([first])
        approved = approve(repository)
        repository.save_results([changed])
        current = repository.get_record("P001")
        assert current.review_status == ReviewStatus.UNREVIEWED
        assert current.selected_candidate is None
        assert current.revision == approved.revision + 1
        assert current.snapshot_id != approved.snapshot_id
        assert repository.get_snapshot(approved.snapshot_id) == approved.evidence
        reset = repository.history("P001")[-1]
        assert reset.previous_state == ReviewStatus.APPROVED
        assert reset.new_state == ReviewStatus.UNREVIEWED
        assert reset.previous_snapshot_id == approved.snapshot_id
        assert reset.snapshot_id == current.snapshot_id
        assert reset.actor_kind == "system"


def test_reverting_to_old_evidence_never_resurrects_approval(tmp_path, results):
    first = results[0]
    with Repository(tmp_path / "store.sqlite3") as repository:
        repository.save_results([first])
        approved = approve(repository)
        repository.save_results([replace(first, matching_version="next")])
        repository.save_results([first])
        current = repository.get_record("P001")
        assert current.snapshot_id == approved.snapshot_id
        assert current.review_status == ReviewStatus.UNREVIEWED
        assert current.revision == 4
        assert len(repository.history("P001")) == 4


def test_returned_evidence_cannot_mutate_persisted_snapshot(tmp_path, results):
    with Repository(tmp_path / "store.sqlite3") as repository:
        repository.save_results(results)
        record = repository.get_record("P001")
        record.evidence["candidates"].clear()
        assert len(repository.get_record("P001").evidence["candidates"]) == 1


def test_duplicate_batch_is_rejected_without_partial_import(tmp_path, results):
    with Repository(tmp_path / "store.sqlite3") as repository:
        with pytest.raises(ValueError, match="Duplicate property"):
            repository.save_results([results[0], results[1], results[0]])
        assert repository.list_records() == ()
        assert repository._connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0


def test_database_failure_rolls_back_entire_import(tmp_path, results):
    with Repository(tmp_path / "store.sqlite3") as repository:
        repository._connection.execute("""CREATE TRIGGER fail_import BEFORE INSERT ON review_events
            WHEN NEW.property_id = 'P002' BEGIN SELECT RAISE(ABORT, 'simulated failure'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="simulated failure"):
            repository.save_results(results)
        assert repository.list_records() == ()
        assert repository._connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0
        assert repository._connection.execute("SELECT count(*) FROM review_events").fetchone()[0] == 0


def test_failed_evidence_refresh_preserves_existing_approval(tmp_path, results):
    with Repository(tmp_path / "store.sqlite3") as repository:
        repository.save_results(results)
        approved = approve(repository)
        history = repository.history("P001")
        repository._connection.execute("""CREATE TRIGGER fail_refresh BEFORE INSERT ON review_events
            WHEN NEW.property_id = 'P002' BEGIN SELECT RAISE(ABORT, 'simulated failure'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            repository.save_results([replace(result, matching_version="next") for result in results])
        assert repository.get_record("P001") == approved
        assert repository.history("P001") == history
        assert repository._connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 15


@pytest.mark.parametrize("status", [ReviewStatus.NEEDS_RESEARCH, ReviewStatus.REJECTED])
def test_changed_evidence_resets_nonapproved_decisions_too(tmp_path, results, status):
    with Repository(tmp_path / "store.sqlite3") as repository:
        repository.save_results(results)
        before = repository.get_record("P001")
        submit_review(repository, "P001", status, reviewer_name="Reviewer",
                      expected_snapshot_id=before.snapshot_id, expected_revision=before.revision)
        repository.save_results([replace(results[0], matching_version="next")])
        assert repository.get_record("P001").review_status == ReviewStatus.UNREVIEWED
        assert repository.history("P001")[-1].previous_state == status


@pytest.mark.parametrize("table", ["snapshots", "review_events"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_evidence_and_audit_rows_are_append_only(tmp_path, results, table, operation):
    with Repository(tmp_path / "store.sqlite3") as repository:
        repository.save_results(results)
        query = f"DELETE FROM {table}" if operation == "DELETE" else f"UPDATE {table} SET property_id = property_id"
        with pytest.raises(sqlite3.IntegrityError, match="immutable|append-only"):
            repository._connection.execute(query)
        repository._connection.rollback()
        assert len(repository.list_records()) == 15
        assert len(repository.history("P001")) == 1


def test_unknown_property_and_snapshot_have_clear_errors(tmp_path):
    with Repository(tmp_path / "store.sqlite3") as repository:
        for lookup in (repository.get_record, repository.history):
            with pytest.raises(KeyError, match="Unknown property"):
                lookup("missing")
        with pytest.raises(KeyError, match="Unknown snapshot"):
            repository.get_snapshot(99)


def test_unknown_schema_is_not_modified(tmp_path):
    path = tmp_path / "foreign.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
    with pytest.raises(ValueError, match="unknown schema"):
        Repository(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [("unrelated",)]


def test_newer_schema_is_rejected(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 2")
    with pytest.raises(ValueError, match="Unsupported database schema"):
        Repository(path)


@pytest.mark.parametrize("table", ["snapshots", "review_events"])
@pytest.mark.parametrize("conflict_key", ["primary", "unique"])
def test_replace_cannot_overwrite_evidence_or_history(tmp_path, results, table, conflict_key):
    with Repository(tmp_path / "store.sqlite3") as repository:
        repository.save_results(results)
        before = repository.get_record("P001")
        history = repository.history("P001")
        row = dict(repository._connection.execute(f"SELECT * FROM {table} LIMIT 1").fetchone())
        if table == "snapshots":
            row["evidence_json"] = "{}"
            id_column = "snapshot_id"
        else:
            row["note"] = "Replacement must fail"
            id_column = "event_id"
        if conflict_key == "unique":
            row[id_column] = 999  # Also block replacement via a secondary UNIQUE constraint.
        fields = ",".join(row)
        values = ",".join("?" for _ in row)
        with pytest.raises(sqlite3.IntegrityError):
            repository._connection.execute(f"INSERT OR REPLACE INTO {table} ({fields}) VALUES ({values})",
                                            tuple(row.values()))
        repository._connection.rollback()
        assert repository.get_record("P001") == before
        assert repository.history("P001") == history
        assert repository.get_snapshot(before.snapshot_id) == before.evidence


def test_unrelated_version_one_database_is_rejected_unchanged(tmp_path):
    path = tmp_path / "unrelated.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
        connection.execute("PRAGMA user_version = 1")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="schema does not match"):
        Repository(path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", ["trigger", "table", "constraint", "identity"])
def test_incompatible_database_is_rejected_before_use(tmp_path, damage):
    path = tmp_path / "store.sqlite3"
    with Repository(path):
        pass
    with sqlite3.connect(path) as connection:
        if damage == "trigger":
            connection.execute("DROP TRIGGER events_no_delete")
        elif damage == "table":
            connection.execute("DROP TABLE review_events")
        elif damage == "constraint":
            connection.execute("DROP TABLE review_records")
            connection.execute("CREATE TABLE review_records(property_id TEXT)")
        else:
            connection.execute("PRAGMA application_id = 123")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="schema does not match|different application"):
        Repository(path)
    assert path.read_bytes() == before


def test_valid_legacy_database_adopts_identity_and_preserves_approval(tmp_path, results):
    path = tmp_path / "store.sqlite3"
    with Repository(path) as repository:
        repository.save_results(results)
        approved = approve(repository)
        history = repository.history("P001")
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA application_id = 0")  # Original Milestone 3 metadata.
    with Repository(path) as repository:
        assert repository._connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
        assert repository.get_record("P001") == approved
        assert repository.history("P001") == history
