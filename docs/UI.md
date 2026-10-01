# Streamlit review interface — Milestone 4

The interface uses the existing matching, SQLite, and human-decision modules.
It adds no alternate approval path. [Milestone 5 guarded export](SALESFORCE.md)
generates local JSON proposals; there is no Salesforce connection.

## Start

From the repository directory, after installing `requirements.txt`:

```bash
PYTHONPATH=src .venv/bin/python -m streamlit run src/net_lease_ownership/app.py
```

Open `http://127.0.0.1:8501`. The Streamlit configuration binds the server to
localhost, disables usage statistics, and uses a minimal toolbar. Tested with
Streamlit 1.64.0 and Python 3.12.14; the dependency requires Streamlit 1.64 or
later within major version 1.

The default database is `ownership.sqlite3` in the project directory, independent
of the shell's current directory. The app reads existing records without
automatically reconciling or reviewing them. If no database exists, click
**Load sample property records** in the sidebar. Identical reimports preserve
decisions; changed evidence resets affected records and retains audit history.

For a separate experiment, choose a new database in an existing directory:

```bash
NLOI_DATABASE=exports/ui-demo.sqlite3 PYTHONPATH=src .venv/bin/python \
  -m streamlit run src/net_lease_ownership/app.py
```

Database files are ignored by Git. The loader protects source CSVs and policy
from path collisions. Invalid databases show an error without being replaced.
This UI loads fictional fixtures; custom feeds can be loaded with the existing
review CLI and inspected through the UI. There is no CSV uploader at this stage.

## Screens and decisions

| Screen | Behavior |
|---|---|
| Dashboard | Total properties, ready/research/conflict recommendations, approved count, portfolio table |
| Review Queue | Review-status filter, property selection, original source fields, comparisons, human decisions |
| Approved records | Current approvals with the selected entity, guarded JSON download, and controls for reconsideration |

Recommendations count all properties. Approved count measures human state
separately, so counts overlap. Untouched fixtures show 15 total, 6 ready,
8 research, 1 conflict, and 0 approved.

Each detail displays property address and tenant. Ownership review shows one short
recommendation, the original county owner and mailing address, and the company
name, registered address, and status. Other company options remain available for
comparison. Approved records initially display the approved company, while new
decisions remain unselected. Download captions identify the persisted approved
company even when a different option is being considered. Registered agents and
source labels, IDs, and dates are in collapsed
**Source details** sections. Scores and repeated comparison summaries are omitted.
Warnings use plain language and combine repeated issues. Raw discrepancy codes,
algorithm explanations, normalized values, and policy metadata are not shown.
Full technical evidence remains preserved in SQLite and available through the
review CLI. Review history shows reviewer, previous/new status, approved company,
date/time in US Central time, and notes; internal snapshot and revision identifiers
are not displayed.

1. Choose a property and inspect its evidence and all plausible candidates.
2. To approve, explicitly select an entity candidate. Display numbering starts
   at 1; the service's zero-based index is handled internally.
3. Choose **Approve**, **Needs Research**, or **Reject**, and enter a reviewer name.
   Neither decision nor candidate is preselected.
4. Approval overriding property or selected-candidate warnings requires a
   nonblank rationale. Missing entity names cannot be approved.
5. Click **Save review decision**. Successful decisions commit their audit event,
   refresh status, and clear decision/candidate defaults.

Research and rejection need no candidate selection. Approved records show the
explicitly selected entity, rather than substituting the highest-ranked candidate.
A subsequent research/rejection decision clears approval and its selection.

The review holds the evidence snapshot and revision the reviewer opened. Widget
changes and form submissions do not silently refresh these tokens. If another
session changes evidence or makes a decision, the UI warns that the record is
stale and the service rejects submission. **Refresh property information** explicitly
refreshes evidence and resets defaults when the revision changed. Switching
properties prevents reuse of another property's candidate or decision.

SQLite connections open and close per script run; they are not globally cached.
Only displayed evidence is held in session state. Decisions remain in SQLite,
so another browser session sees committed state. Session state is not the approval
database. Reviewer names remain caller-supplied, not authenticated identities.

## Manual inspection and verification

- P001: inspect originals and verify approval has no default.
- P003: inspect conflict explanations and approval without a rationale.
- P013: compare both candidates and verify the selected entity in Approved records.
- P004/P015: inspect absent/blank entity evidence and blocked approval.
- Use a separate demo database for decisions; inspect history after approval/revocation.
- Keep a detail open while changing its evidence through the CLI in another terminal;
  submission must require reloading and inspecting the current record.

The full suite has **414 passing tests**, including 23 Streamlit AppTest cases
against actual `app.py`. Tests drive widgets and forms, then reopen temporary
databases to verify decisions and audit events. Coverage includes counts, source
rendering, defaults, all decisions, missing inputs, rationale, selected-candidate
preservation, stale evidence/decisions, property switching, explicit initial load,
identical reload, invalid databases, filters, revocation, and guarded Salesforce
proposal downloads. Tests inspect actual registered JSON download bytes and
verify that changing a draft candidate leaves the exported company unchanged.
Malformed timestamp coverage checks that export fails clearly and history remains
viewable. The running app was also inspected in the local browser.

AppTest does not prove every browser layout or keyboard behavior. This remains
a local POC, with no production hosting, corporate hierarchy inference, live feeds,
automatic approval, or validation against an actual Salesforce org.
