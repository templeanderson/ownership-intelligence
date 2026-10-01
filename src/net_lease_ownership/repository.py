"""Small SQLite store for immutable evidence, current state, and audit history."""

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from functools import lru_cache

from .models import MatchResult, ReviewStatus


SCHEMA_VERSION = 1
APPLICATION_ID = 0x4E4C4F49  # NLOI; SQLite's application identity, separate from schema version.
_SCHEMA = """
BEGIN IMMEDIATE;
CREATE TABLE snapshots (
    snapshot_id INTEGER PRIMARY KEY,
    property_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(property_id, fingerprint),
    UNIQUE(snapshot_id, property_id)
);
CREATE TABLE review_records (
    property_id TEXT PRIMARY KEY,
    snapshot_id INTEGER NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 1),
    review_status TEXT NOT NULL DEFAULT 'unreviewed'
        CHECK(review_status IN ('unreviewed', 'approved', 'needs_research', 'rejected')),
    selected_candidate INTEGER,
    CHECK((review_status = 'approved' AND selected_candidate IS NOT NULL AND selected_candidate >= 0)
        OR (review_status != 'approved' AND selected_candidate IS NULL)),
    FOREIGN KEY(snapshot_id, property_id) REFERENCES snapshots(snapshot_id, property_id)
);
CREATE TABLE review_events (
    event_id INTEGER PRIMARY KEY,
    property_id TEXT NOT NULL,
    snapshot_id INTEGER NOT NULL,
    previous_snapshot_id INTEGER,
    revision INTEGER NOT NULL,
    previous_state TEXT CHECK(previous_state IN ('unreviewed', 'approved', 'needs_research', 'rejected')),
    new_state TEXT NOT NULL CHECK(new_state IN ('unreviewed', 'approved', 'needs_research', 'rejected')),
    actor_kind TEXT NOT NULL CHECK(actor_kind IN ('human', 'system')),
    reviewer_name TEXT,
    timestamp TEXT NOT NULL,
    note TEXT,
    selected_candidate INTEGER,
    CHECK((actor_kind = 'human' AND reviewer_name IS NOT NULL
           AND length(trim(reviewer_name)) > 0 AND new_state != 'unreviewed')
       OR (actor_kind = 'system' AND reviewer_name IS NULL AND new_state = 'unreviewed')),
    CHECK((new_state = 'approved' AND selected_candidate IS NOT NULL AND selected_candidate >= 0)
       OR (new_state != 'approved' AND selected_candidate IS NULL)),
    UNIQUE(property_id, revision),
    FOREIGN KEY(property_id) REFERENCES review_records(property_id),
    FOREIGN KEY(snapshot_id, property_id) REFERENCES snapshots(snapshot_id, property_id),
    FOREIGN KEY(previous_snapshot_id, property_id) REFERENCES snapshots(snapshot_id, property_id)
);
CREATE TRIGGER snapshots_no_update BEFORE UPDATE ON snapshots
BEGIN SELECT RAISE(ABORT, 'Evidence snapshots are immutable'); END;
CREATE TRIGGER snapshots_no_delete BEFORE DELETE ON snapshots
BEGIN SELECT RAISE(ABORT, 'Evidence snapshots are immutable'); END;
CREATE TRIGGER events_no_update BEFORE UPDATE ON review_events
BEGIN SELECT RAISE(ABORT, 'Audit history is append-only'); END;
CREATE TRIGGER events_no_delete BEFORE DELETE ON review_events
BEGIN SELECT RAISE(ABORT, 'Audit history is append-only'); END;
PRAGMA user_version = 1;
COMMIT;
"""


@lru_cache(maxsize=1)
def _expected_schema() -> dict:
    # Derive the expected tables/constraints/triggers from the single DDL source.
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(_SCHEMA)
        return _schema_objects(connection)
    finally:
        connection.close()


def _schema_objects(connection) -> dict:
    return {(row[0], row[1]): row[2] for row in connection.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _serialize(result: MatchResult) -> tuple[str, str]:
    if not isinstance(result, MatchResult) or result.review_status != ReviewStatus.UNREVIEWED:
        raise ValueError("Only unreviewed MatchResult evidence can be imported")

    def encode(value):
        if isinstance(value, date):
            return value.isoformat()
        raise TypeError(f"Cannot serialize {type(value).__name__}")

    content = json.dumps(asdict(result), sort_keys=True, separators=(",", ":"),
                         default=encode, allow_nan=False, ensure_ascii=False)
    return content, hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ReviewRecord:
    property_id: str
    snapshot_id: int
    revision: int
    review_status: ReviewStatus
    selected_candidate: int | None
    fingerprint: str
    evidence: dict


@dataclass(frozen=True)
class ReviewEvent:
    event_id: int
    property_id: str
    snapshot_id: int
    previous_snapshot_id: int | None
    revision: int
    previous_state: ReviewStatus | None
    new_state: ReviewStatus
    actor_kind: str
    reviewer_name: str | None
    timestamp: str
    note: str | None
    selected_candidate: int | None


class Repository:
    def __init__(self, path: str | Path):
        # Caller chooses the local file. No network, ORM, or background jobs.
        self._connection = sqlite3.connect(str(path), timeout=5)
        self._connection.row_factory = sqlite3.Row
        try:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA recursive_triggers = ON")
            application_id = self._connection.execute("PRAGMA application_id").fetchone()[0]
            if application_id not in (0, APPLICATION_ID):
                raise ValueError("Database belongs to a different application")
            version = self._connection.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                if _schema_objects(self._connection):
                    raise ValueError("Refusing to initialize a database with an unknown schema")
                self._connection.executescript(_SCHEMA)
            elif version != SCHEMA_VERSION:
                raise ValueError(f"Unsupported database schema version: {version}")
            if _schema_objects(self._connection) != _expected_schema():
                raise ValueError("Database schema does not match required tables, constraints, and protection triggers")
            # Adopt an existing Milestone 3 database only after complete schema validation.
            if application_id == 0:
                self._connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        except Exception:
            self._connection.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self) -> None:
        self._connection.close()

    @contextmanager
    def _transaction(self):
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

    def get_record(self, property_id: str) -> ReviewRecord:
        row = self._connection.execute("""
            SELECT r.*, s.fingerprint, s.evidence_json FROM review_records r
            JOIN snapshots s ON s.snapshot_id = r.snapshot_id WHERE r.property_id = ?
        """, (property_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown property_id: {property_id}")
        return ReviewRecord(row["property_id"], row["snapshot_id"], row["revision"],
                            ReviewStatus(row["review_status"]), row["selected_candidate"],
                            row["fingerprint"], json.loads(row["evidence_json"]))

    def list_records(self) -> tuple[ReviewRecord, ...]:
        ids = self._connection.execute("SELECT property_id FROM review_records ORDER BY property_id").fetchall()
        return tuple(self.get_record(row[0]) for row in ids)

    def get_snapshot(self, snapshot_id: int) -> dict:
        row = self._connection.execute("SELECT evidence_json FROM snapshots WHERE snapshot_id = ?",
                                       (snapshot_id,)).fetchone()
        if row is None:
            raise KeyError(f"Unknown snapshot_id: {snapshot_id}")
        return json.loads(row[0])

    def history(self, property_id: str) -> tuple[ReviewEvent, ...]:
        self.get_record(property_id)  # Unknown property is different from empty history.
        rows = self._connection.execute("SELECT * FROM review_events WHERE property_id = ? ORDER BY event_id",
                                        (property_id,)).fetchall()
        return tuple(ReviewEvent(**(dict(row) | {
            "previous_state": ReviewStatus(row["previous_state"]) if row["previous_state"] else None,
            "new_state": ReviewStatus(row["new_state"])})) for row in rows)

    def _append_event(self, record: ReviewRecord, *, previous_state: ReviewStatus | None,
                      previous_snapshot_id: int | None, actor_kind: str,
                      reviewer_name: str | None, note: str | None) -> None:
        self._connection.execute("""
            INSERT INTO review_events(property_id, snapshot_id, previous_snapshot_id, revision,
                previous_state, new_state, actor_kind, reviewer_name, timestamp, note, selected_candidate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (record.property_id, record.snapshot_id, previous_snapshot_id, record.revision,
              previous_state.value if previous_state else None, record.review_status.value,
              actor_kind, reviewer_name, _timestamp(), note, record.selected_candidate))

    def record_review(self, property_id: str, action: ReviewStatus, *, reviewer_name: str,
                      expected_snapshot_id: int, expected_revision: int,
                      note: str | None = None, selected_candidate: int | None = None) -> ReviewRecord:
        """Validate and persist a decision with its audit event in one transaction."""
        from .review import StaleReviewError, validate_review_request, validate_approval

        validate_review_request(action, reviewer_name, expected_snapshot_id, expected_revision,
                                note, selected_candidate)
        with self._transaction():
            current = self.get_record(property_id)
            if current.snapshot_id != expected_snapshot_id or current.revision != expected_revision:
                raise StaleReviewError("Record changed; reload its evidence and review state before deciding")
            if action == ReviewStatus.APPROVED:
                validate_approval(current, selected_candidate, note)
            self._connection.execute("""
                UPDATE review_records SET review_status = ?, selected_candidate = ?, revision = revision + 1
                WHERE property_id = ?
            """, (action.value, selected_candidate, property_id))
            updated = self.get_record(property_id)
            self._append_event(updated, previous_state=current.review_status,
                previous_snapshot_id=current.snapshot_id, actor_kind="human",
                reviewer_name=reviewer_name.strip(), note=note)
        return updated

    def save_results(self, results) -> None:
        """Import an entire batch atomically. Changed evidence invalidates all decisions."""
        prepared = []
        ids = set()
        for result in results:
            content, fingerprint = _serialize(result)
            if result.property_id in ids:
                raise ValueError(f"Duplicate property_id: {result.property_id}")
            ids.add(result.property_id)
            prepared.append((result.property_id, content, fingerprint))
        with self._transaction():
            for property_id, content, fingerprint in prepared:
                existing = self._connection.execute(
                    "SELECT * FROM review_records WHERE property_id = ?", (property_id,)).fetchone()
                self._connection.execute("""
                    INSERT INTO snapshots(property_id, fingerprint, evidence_json, created_at)
                    VALUES (?, ?, ?, ?) ON CONFLICT(property_id, fingerprint) DO NOTHING
                """, (property_id, fingerprint, content, _timestamp()))
                snapshot_id = self._connection.execute(
                    "SELECT snapshot_id FROM snapshots WHERE property_id = ? AND fingerprint = ?",
                    (property_id, fingerprint)).fetchone()[0]
                if existing and existing["snapshot_id"] == snapshot_id:
                    continue
                revision = existing["revision"] + 1 if existing else 1
                self._connection.execute("""
                    INSERT INTO review_records(property_id, snapshot_id, revision)
                    VALUES (?, ?, ?) ON CONFLICT(property_id) DO UPDATE SET
                    snapshot_id = excluded.snapshot_id, revision = excluded.revision,
                    review_status = 'unreviewed', selected_candidate = NULL
                """, (property_id, snapshot_id, revision))
                self._append_event(self.get_record(property_id),
                    previous_state=ReviewStatus(existing["review_status"]) if existing else None,
                    previous_snapshot_id=existing["snapshot_id"] if existing else None,
                    actor_kind="system", reviewer_name=None,
                    note="Evidence changed; new human review required." if existing else "Initial evidence loaded.")
