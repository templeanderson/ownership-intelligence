# SQLite persistence and human review — Milestone 3

Matching recommends a disposition. A human makes a separate review decision.
Loading evidence never creates an approval. [Milestone 4's Streamlit interface](UI.md)
uses the same decision rules. Salesforce payload generation and validation are
Milestone 5. Persistence/review use Python's standard library and SQLite.

## Run and inspect

From the repository directory:

```bash
PYTHONPATH=src .venv/bin/python -m net_lease_ownership.review_cli load
PYTHONPATH=src .venv/bin/python -m net_lease_ownership.review_cli list
PYTHONPATH=src .venv/bin/python -m net_lease_ownership.review_cli show P001
PYTHONPATH=src .venv/bin/python -m net_lease_ownership.review_cli history P001
```

Use `--db /path/to/demo.sqlite3` before the command to choose another database.
Its parent directory must exist. `load` accepts `--data-dir` and `--config`, just
like the matching report. The database path cannot alias an input CSV or policy
file, including through hard or symbolic links. Other commands require an
existing database. Database files and SQLite sidecar files are ignored by Git.

`show` displays the property, original and normalized source records, all
candidate comparisons, matching explanations, policy and algorithm versions,
snapshot ID, fingerprint, current review status, revision, and selected candidate.
The matching snapshot's nested `review_status` stays `unreviewed` forever; the
outer `review_status` is the current human workflow state.

To approve, inspect `show`, then supply its current `snapshot_id` and `revision`,
your reviewer name, and an explicit **zero-based** candidate index. For example,
on a fresh database where P001 has snapshot 1 and revision 1:

```bash
PYTHONPATH=src .venv/bin/python -m net_lease_ownership.review_cli decide P001 \
  --action approved --reviewer "Example Reviewer" \
  --snapshot-id 1 --revision 1 --candidate 0 \
  --note "Inspected both original records and the selected entity."
```

This example changes local state when executed. Substitute the IDs actually
shown and your own name. Candidate zero is not selected automatically, and a
stale revision produces an error rather than overwriting another decision.

Research and rejection do not select an entity:

```bash
PYTHONPATH=src .venv/bin/python -m net_lease_ownership.review_cli decide P003 \
  --action needs_research --reviewer "Example Reviewer" \
  --snapshot-id 3 --revision 1 --note "Investigate conflicting owner names."

PYTHONPATH=src .venv/bin/python -m net_lease_ownership.review_cli decide P005 \
  --action rejected --reviewer "Example Reviewer" \
  --snapshot-id 5 --revision 1 --note "Address disagreement is unresolved."
```

Again, use the current snapshot and revision from `show`. Run `history` afterward
to inspect previous state, new state, reviewer, UTC timestamp, note, and candidate.

## SQLite schema

| Table | Purpose | Key fields |
|---|---|---|
| `snapshots` | Immutable full evidence and analysis | snapshot ID, property ID, SHA-256 fingerprint, canonical JSON, creation timestamp |
| `review_records` | Current evidence pointer and human state | property ID, snapshot ID, revision, review status, selected candidate |
| `review_events` | Append-only decision and reset history | property ID, previous/current snapshot, revision, previous/new state, actor kind, reviewer, UTC timestamp, note, selected candidate |

One JSON snapshot retains originals, normalized values, provenance, candidates,
explanations, and policy metadata together. This is deliberately simpler than
separate tables for each feed, normalized field, and candidate in a 15-property
POC. Review state and audit fields use ordinary SQL columns and constraints.
JSON is not a second mutable source of truth for human decisions.

Foreign keys enforce property/snapshot associations. Status checks limit valid
states. Approved state requires a nonnegative candidate selection; other states
clear it. Database triggers reject updates or deletes of snapshots and events.
Repository connections enable recursive triggers so `INSERT OR REPLACE` also
fires the deletion guards, including replacements through secondary unique keys.
Schema version 1 uses `PRAGMA user_version`, while `PRAGMA application_id`
identifies this application separately. Opening validates all expected tables,
constraints, indexes, and protection triggers against the checked-in DDL;
unrelated version-1 databases and missing/altered objects are rejected. A valid
original Milestone 3 database with application ID zero is adopted only after
this validation, preserving evidence and decisions. No general migration
framework is needed yet.

## State transitions and evidence binding

| Trigger | Result | Audit behavior |
|---|---|---|
| First import | `unreviewed`, revision 1 | System event, no previous state or reviewer |
| Identical reimport | Current decision retained | No new snapshot or event |
| Changed evidence/analysis | `unreviewed`, selection cleared, revision incremented | System reset records previous state and snapshot |
| Explicit approval | `approved`, chosen candidate, revision incremented | Human event records reviewer and decision |
| Explicit research | `needs_research`, selection cleared, revision incremented | Human event records reviewer and decision |
| Explicit rejection | `rejected`, selection cleared, revision incremented | Human event records reviewer and decision |

Any current state may receive any of the three human decisions, including
reconsidering a rejection or revoking approval. Reaffirming the same decision
also creates an event and increments the revision. Humans cannot submit
`unreviewed`; that state is reserved for initial load or evidence invalidation.

Approval requires a useful entity name in the explicitly chosen candidate.
Missing/placeholder entities cannot be approved. A matching conflict, dissolved
status, absent county record, or ambiguity does not prohibit a deliberate human
decision. Final judgment remains with the reviewer; all warnings stay visible
and unchanged. Approval requires a nonblank rationale if either the property
disposition or the **selected candidate's** disposition is not `ready_for_review`.
Thus, a ready property cannot silently authorize an unrelated conflicting
candidate. Notes remain optional for ordinary ready approvals, research, and
rejection. Rationale is recorded, not independently verified. An approved record
may still fail the future Salesforce payload validation.

The fingerprint includes originals, source dates, candidate identities, property
fields, normalization/matching versions, policy metadata, and derived analysis.
Even an original spelling change that normalizes to the same value invalidates
a decision. Reverting to an older snapshot never restores an old approval.
Snapshot IDs identify the exact candidate list, and revision numbers also
detect intervening reviews on the same snapshot.

Imports and review updates use `BEGIN IMMEDIATE` transactions. Review checks
the expected snapshot and revision while holding SQLite's write lock, then
updates current state and appends its audit event atomically. A failed audit
insert rolls back the decision. Failed batch imports roll back snapshots,
resets, and history together. SQL values are parameterized, including reviewer
names and multiline notes.

## Python API

```python
from net_lease_ownership.models import ReviewStatus
from net_lease_ownership.repository import Repository
from net_lease_ownership.review import submit_review

with Repository("ownership.sqlite3") as repository:
    displayed = repository.get_record("P001")
    # After a human has inspected displayed.evidence and selected a candidate:
    reviewed = submit_review(
        repository, displayed.property_id, ReviewStatus.APPROVED,
        reviewer_name="Example Reviewer", selected_candidate=0,
        expected_snapshot_id=displayed.snapshot_id,
        expected_revision=displayed.revision,
        note="Inspected the original source evidence.",
    )
```

`Repository.save_results()` imports matching results. `list_records()`,
`get_record()`, `get_snapshot()`, and `history()` expose inspectable data for the
next milestone's UI. Returned evidence is freshly decoded; modifying a returned
dictionary cannot alter stored evidence. Review and persistence share one
transaction rather than adding an ORM or generic workflow framework. SQL and
transaction management belong to `Repository.record_review()`; `review.py`
provides decision validation and the public convenience function. Direct calls
to the repository method perform the same validation.

## Verification and limits

Run `.venv/bin/python -m pytest`. The full suite has **328 passing tests**,
including 43 regressions added after Milestone 3 self-review. Tests reopen actual temporary
databases, inspect stored history and originals, exercise every human transition,
use two independent connections for stale submissions, and force database
failures to verify rollback. Input-protection tests check unchanged source bytes.
A subprocess test verifies the CLI across process boundaries.

Regression tests attempt `INSERT OR REPLACE` against primary and secondary unique
keys, inspect unchanged snapshots/history, reject unrelated version-1 databases
and damaged schemas, verify safe adoption of the legacy database, and enforce
rationale for both property and selected-candidate overrides. Report generation
rejects SQLite headers regardless of extension or aliases, and reserves database
and sidecar filenames. Tests inspect original database bytes after rejected writes.

This is a local single-user POC, not an authenticated approval system. Reviewer
names are supplied by the caller, not verified identities. A caller invoking the
review API is responsible for obtaining explicit human intent. Triggers preserve
history during normal application use; someone who directly controls the SQLite
file can bypass the application. No role permissions, signed audit trail, or
production backup system is claimed.

Imports update included properties; omitted properties remain in the database.
There is no deletion, retirement, or authoritative full-feed replacement policy
yet. To start an independent experiment, choose a new database path. The
application does not infer parent/child accounts or change preserved evidence
during review. Streamlit screens are implemented separately in Milestone 4.
No Salesforce module is implemented yet.

Manually inspect P001 for a straightforward match, P003 for conflicting names,
and P013 for multiple candidates. Verify the candidate index before approving;
inspect history after changing a decision. For an evidence-reset experiment,
use copied fictional feeds and a separate database, change a source value,
reload, and confirm `unreviewed` while the earlier approval remains in history.
