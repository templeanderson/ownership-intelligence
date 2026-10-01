# Net-Lease Ownership Intelligence POC

A portfolio proof of concept for reconciling commercial-property ownership
evidence before human review and a guarded Salesforce payload export.

**Current milestone: 2 — explainable matching and configurable policy.** Source
models, fictional data, ingestion, normalization, and reconciliation are
implemented. SQLite, human decisions, Streamlit, and Salesforce export remain
future milestones. Every generated match result is `unreviewed`.

## Problem

Ownership records can disagree across county and corporate sources. Formatting
differences can conceal agreement, while similar company names, shared registered
agents, and related entities can create misleading apparent matches. Finding
data does not make it trusted. The eventual workflow must preserve evidence,
explain its recommendation, and require a human decision before export.

## Run the POC

Requires Python 3.11 or later. From this project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest
```

In this workspace, a working `.venv` has already been created with the bundled
Python runtime. To rerun tests without using macOS's developer-tools-dependent
system Python, run `.venv/bin/python -m pytest` from this directory. Verification
uses Python 3.12.14 and pytest 8.4.2; the current full suite has 207 passing tests.

Generate an inspectable matching analysis report from the project directory:

```bash
PYTHONPATH=src .venv/bin/python -m net_lease_ownership
```

This prints 6 `ready_for_review`, 8 `needs_research`, and 1 `conflict`, then writes
`exports/matching_report.json`. All 15 results remain `unreviewed`. The report
contains original and normalized evidence, every candidate comparison, scores,
discrepancy codes, explanations, and policy/algorithm versions. It is not a
Salesforce payload. The generated directory is ignored by Git.

Self-review fixes reject placeholder evidence, restrict fuzzy readiness to
explicit minor word variations, preserve name identifiers, and prevent blank
candidates from hiding conflicts. Report output cannot replace input feeds or
policy files, including through symbolic or hard links, and report replacement
is atomic. Corroboration fields and evidence counts are computed rather than
independently supplied. Regression tests cover each reviewed failure.

The explicit source path avoids an observed macOS hidden-file flag on the
editable-install `.pth` file in this workspace, which causes Python to skip it.
Clearing that flag temporarily fixed imports, but it returned. `PYTHONPATH=src`
and pytest's source-path configuration work without relying on that file. On a
normally installed environment, `python -m net_lease_ownership` also works.

To use different fixtures, policy, or report location:

```bash
PYTHONPATH=src .venv/bin/python -m net_lease_ownership \
  --data-dir data --config config/matching.toml \
  --output exports/matching_report.json
```

Load the sample data in Python after installing the project (or start Python
with `PYTHONPATH=src` in this workspace):

```python
from net_lease_ownership.ingestion import load_dataset

dataset = load_dataset("data")
print(len(dataset.properties))  # 15
county = dataset.county_records[0]
print(county.owner_name)         # ABC Medical Holdings LLC
print(county.owner_normalized)   # abc medical holdings llc
print(county.owner_comparison)   # abc medical holdings
```

The application uses the Python standard library at this stage. `pytest` is a
development dependency. Streamlit will be added in Milestone 4. No credentials,
AI API, environment variables, live feeds, or Salesforce org are needed.

## Project layout

```text
net-lease-ownership-intelligence/
    README.md
    requirements.txt
    pyproject.toml
    .gitignore
    .env.example
    config/
        matching.toml
    docs/
        MATCHING.md
    data/
        properties.csv
        county_records.csv
        entity_records.csv
        SCENARIOS.md
    src/net_lease_ownership/
        __init__.py
        __main__.py
        models.py
        normalization.py
        ingestion.py
        policy.py
        matching.py
    tests/
        test_models.py
        test_normalization.py
        test_ingestion.py
        test_policy.py
        test_matching.py
        test_report.py
```

The named package under `src/` supports predictable imports and keeps business
logic independent of the future UI. Modules are added as their milestone needs
them, rather than creating nonfunctional review or export placeholders.

## Architecture and milestone boundaries

```mermaid
flowchart TD
    P[Property CSV] --> I[Load and validate]
    C[Fictional county CSV] --> I
    E[Fictional entity CSV] --> I
    I --> N[Preserve originals and normalize]
    N --> M[Explainable matching and configurable rules]
    M --> DB[(Milestone 3: SQLite)]
    DB --> R[Milestones 3 and 4: human review]
    R --> A[Explicit approval of reviewed evidence]
    A --> X[Milestone 5: guarded Salesforce JSON dry run]
```

1. **Milestone 1:** scaffold, source models, samples, normalization, tests.
2. **Milestone 2:** reconciliation rules, configurable policy, explainable scores.
3. **Milestone 3:** SQLite evidence/results and append-only human decisions.
4. **Milestone 4:** Streamlit dashboard, review detail, and approved-record view.
5. **Milestone 5:** validated export of explicitly approved evidence only.
6. **Milestone 6:** documentation, cleanup, full tests, and GitHub readiness.

Each milestone ends with tests and product-owner review before the next begins.

For Milestone 2 review, inspect [matching rules and sample outcomes](docs/MATCHING.md),
`config/matching.toml`, and the generated analysis report. Confirm that the
explanations and conservative handling of conflicts, possible related entities,
and multiple candidates fit the intended ownership-research workflow.

Milestone 1 was committed and pushed to
[ownership-intelligence](https://github.com/templeanderson/ownership-intelligence).
Milestone 2 changes are local for product-owner review; they have not been
committed or pushed.

## Models and source provenance

`Property`, `CountyRecord`, and `EntityRecord` are immutable dataclasses. Feed
records retain source name, source-record ID, source-as-of date, and original
values. Derived normalized values are computed at construction and cannot be
supplied independently. County mailing addresses and entity registered addresses
remain separate fields because they describe different roles.

Company names have a suffix-preserving normalized value and a comparison value
that removes a trailing legal suffix. A separate normalized-record hierarchy is
unnecessary for this small POC. Match results record normalization and matching
versions, policy version, and a fingerprint of the actual policy values.

Disposition and review-status enums represent separate concepts:
`ready_for_review` is not approval. Source records carry no review status. The
matching layer always produces `unreviewed` results. The later review layer must
require a human decision and
bind approval to the specific evidence reviewed. Changed evidence requires new
review. Persisted human decisions and guarded export remain planned for later
milestones.

## Sample data and validation

The fixtures contain 15 properties, 14 county rows, and 15 entity candidates.
All property/entity/agent/tenant names and street addresses are fictional. See
[the scenario guide](data/SCENARIOS.md) for the intended cases, including missing
sources, conflicting names, shared addresses, possible related entities, and
multiple candidates. Scenario expectations are documentation, not match outputs.

Feed `property_id` values provide explicit research-packet associations. They do
not establish ownership and avoid pretending to implement live candidate discovery.
Multiple entity candidates for one property are allowed.

Loaders reject incorrect/duplicate headers, malformed rows, duplicate identifiers,
invalid dates, blank required fields, and unknown property associations. A source
with missing owner/entity or address evidence is retained so matching can
explain the missing evidence. A header-only feed is valid; an empty property list
is not. Original strings are retained, including whitespace and punctuation.

## Deterministic normalization

- Unicode compatibility normalization, case folding, and whitespace cleanup.
- Dotted legal abbreviations such as `L.L.C.` become `llc`.
- Terminal `Incorporated`, `Corporation`, and `Limited` become `inc`, `corp`, and `ltd`.
- Ampersands become `and`; apostrophes/periods are removed; other punctuation
  becomes a word separator.
- Optional removal of one trailing legal suffix keeps meaningful name tokens.
- Common street and directional abbreviations are normalized before the first
  comma, which is retained to keep street and city roles stable on repeated
  normalization. `Ste.` and `#` become `suite`; apartment identifiers stay distinct.
- House numbers, unit numbers, city/state text, and postal-code digits remain.
- Blank values stay blank. Placeholders remain visible but do not count as useful
  evidence. Address corroboration requires a basic numbered street or PO box.

Normalization does not expand `Med` to `Medical`, singularize `Properties`,
geocode addresses, establish identity, or infer parent/child relationships.
Abbreviation maps remain versioned normalization constants. Matching thresholds,
score weights, accepted statuses, allowed minor name-variation pairs, and generic
name tokens are configurable in
`config/matching.toml`, using Python's built-in TOML reader rather than adding a
YAML dependency. See [matching documentation](docs/MATCHING.md) for the formula
and the evidence rules that take precedence over the score.

## Limitations and safety boundaries

Address normalization is an illustrative US-format heuristic, not postal
validation. Inputs should use comma-separated street/city/state segments.
Directional street names, unusual punctuation, legal-name conventions, and
international addresses may need different rules. Address equality is not proof
of ownership; address disagreement is not necessarily an ownership conflict.
Word-number addresses such as `One Main Street` are outside the current evidence
format and require research. Unlisted spelling variations also require research,
even with a high similarity score. Normalization, matching, and the default
policy now use version 2; original CSV evidence is unchanged.

There is no corporate hierarchy model. A possible parent remains a research
hint and must never silently replace a property-specific ownership entity.

No live records are scraped, no production systems are connected, and no LLM is
used. The matching score is an operational review priority, not a statistically
calibrated probability. Distinct source labels do not prove independent upstream
data, source dates do not establish current ownership, and even a score of 100
may require research. The future export layer
must require persisted human approval and validate fields, with dry-run output
only in V1.

## How Codex was used

The product owner supplied the business problem, specification, milestone
boundaries, and approved design. Codex served as the primary coding agent for
Milestones 1 and 2: scaffolding the project, implementing source models and
loaders, creating fictional fixtures, writing normalization and matching,
adding tests, diagnosing a local package-import issue, refining candidate
ranking, and reviewing implementation decisions. Codex does not independently
own the project.

## Future work

After the six milestones: live county/corporate adapters, duplicate detection,
optional AI assistance for ambiguous candidates, richer audit evidence, scheduled
ingestion, and a separately authorized real Salesforce integration. None of
these additions is required for the current milestone.
