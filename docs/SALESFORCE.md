# Guarded Salesforce proposal — Milestone 5

This module generates local JSON only. It contains no Salesforce client,
credentials, network calls, or production write path. `dry_run=True` is the
default and only supported mode. Passing any other value raises `PayloadError`.

## Generate a proposal

In Streamlit, open **Approved records**, select a property, and click
**Download proposed JSON** below its decision form. The download appears only
when the displayed approval is current and valid. Refresh a stale property
before exporting. A review in the queue can also download its current approval.

The CLI prints one proposed upsert as JSON:

```bash
PYTHONPATH=src .venv/bin/python -m net_lease_ownership.review_cli \
  --db ownership.sqlite3 export P001
```

Use the existing review workflow to approve a property first. Export does not
approve anything, change review state, or append an audit event. Unreviewed,
rejected, and needs-research properties produce a clear error and no payload.
The CLI has no output-file option, so the application cannot overwrite source
files or the review database while exporting.

## Proposal format and field mapping

The envelope has `schema_version: 1`, `dry_run: true`, `operation: "upsert"`,
`external_id_field: "External_Property_ID__c"`, and a one-element `records` list.
It is a proposed integration format, not a Salesforce REST request. No Salesforce
object name is assumed. The following custom fields must be mapped and checked
against a target org before a future adapter can write anything.

| Proposed field | Source |
|---|---|
| External_Property_ID__c | Current property's external ID |
| Property_Address__c | Original property address |
| Ownership_Entity__c | Original name of the explicitly approved company |
| Registered_Agent__c | Approved company's registered agent, or JSON null if empty/whitespace-only |
| Entity_Status__c | Approved company's status, or JSON null if empty/whitespace-only |
| Ownership_Confidence__c | Selected comparison's workflow score, never a calibrated probability |
| Last_Verified_Date__c | UTC calendar date of the latest human approval, not the export date |
| Source_Provenance__c | JSON string containing the selected original source records and approval context |
| Review_Status__c | `approved` |

Provenance includes source names, record IDs, source dates, original and normalized
values, reviewer, review timestamp, note, selected candidate, evidence fingerprint,
snapshot/revision, discrepancies, and matching/normalization/policy versions.
It does not silently substitute the highest-ranked company for the approved one.
Field types, lengths, external-ID uniqueness, object permissions, null semantics,
and lookup relationships are not validated against an actual Salesforce org.

## Guard rules

- Read current evidence and audit history in a single SQLite read transaction.
- Require current `approved` state and a matching latest human approval event,
  including the snapshot, revision, explicit company selection, reviewer, and
  timestamp with timezone.
- Require a matching evidence fingerprint, property ID/address, useful original
  company name, valid candidate index, source identity/dates, and finite score
  between 0 and 100. Validate property/selected-candidate dispositions, discrepancy
  lists, nonblank version strings, and the policy fingerprint format. Reject
  malformed records with `PayloadError`, including timestamps whose UTC
  conversion overflows.
- Reapply approval validation, including a note for warnings. A human may approve
  a researched exception; the export retains its warnings and rationale rather
  than silently requiring a ready-for-review recommendation.
- Validate both snapshot and revision against the displayed UI before generating
  its download. Rejection, research, and evidence changes invalidate future exports.

An export is a proposal based on approval at generation time. A later rejection
cannot recall an already downloaded file. A future live-write adapter must
recheck current approval immediately before writing; this milestone grants no
standing authorization to use a saved proposal in production. Local audit
checks detect inconsistency, not malicious modification by someone with database
access. Reviewer names remain caller-supplied.

## Verification and manual inspection

The full suite has 414 passing tests. Salesforce tests exercise every unapproved
state, selected second-candidate export, provenance, malformed approvals, disabled
write mode, revocation, evidence changes, stale display tokens, and consistent
reads while a second connection changes review state. Regressions verify
historical approval dates across UTC day boundaries, malformed metadata,
whitespace-only optional fields with preserved originals, and timestamp overflow.
CLI and Streamlit tests
exercise the export entry points, inspect actual JSON download bytes, and
verify approved-company display, draft isolation, and disappearance of stale downloads.

Manually inspect P013 after approving its second company with a rationale. Check
the exported company, score, sources, selected index, and reviewer note. Mark that
property Needs Research or Reject and confirm the download disappears. Use a
separate demo database if you want to preserve your current review decisions.

Milestone 6 documents and verifies this boundary. The six-milestone scope is
complete; no real Salesforce integration is included.
