# Matching behavior in Milestone 2

Matching recommends a research disposition. It never establishes legal
ownership, approves a record, infers corporate parentage, or produces a
Salesforce payload. Every `MatchResult` has an immutable `unreviewed` status.
Milestone 3 stores human decisions and evidence-bound approval separately in
SQLite; see [review documentation](REVIEW.md).

## Evidence and similarity

Each property research packet contains county records and entity candidates
associated through explicit fixture IDs. All combinations are compared, with
absent sources represented as null. Original source values, computed normalized
fields, record IDs, source labels, and source-as-of dates remain in each result.

Names are compared using their normalized values with a terminal legal suffix
removed. Name similarity is the average of Python `SequenceMatcher` character
ratios in both argument orders, with `autojunk=False`. Averaging makes the score
symmetric. It measures spelling overlap, not business meaning, identity, or
probability. Empty or unusable comparison names produce `null` similarity, not
a perfect empty-string match. Recognized markers such as Unknown, N/A, and TBD
are not useful names; they remain intact in the preserved source fields.

Address agreement is exact equality of two useful normalized addresses:
`true` for agreement, `false` for disagreement, and `null` for unavailable
evidence. County mailing and entity registered addresses have different roles;
disagreement requires research but does not alone establish an ownership conflict.
Useful addresses must begin with a positive house number and include street
letters, or contain a numbered PO box. This is basic shape validation, not postal
verification. Placeholders, `0`, an unnumbered PO box, and unsupported word-number
addresses cannot corroborate a match. Digits in a city/ZIP section cannot stand
in for the street address.

Normalization version 2 retains the street/city comma, producing, for example,
`100 main st, west lake tx 75001`. A second normalization pass preserves city
words rather than applying street abbreviations to them. Original values remain
unchanged; there is no persisted database to migrate at this milestone.

Legal suffix differences and differing numbers within company names remain
visible even when the suffix-stripped names are highly similar. Numeric order
and repetition are preserved: `10 20`, `20 10`, and `10 10` are distinct sequences.
Directional and Roman-series identifiers such as East/West and I/II must agree,
even if a configuration edit lists them as an allowed variation. Shared
distinctive tokens are whole words common to both comparison names, excluding
the configured generic terms. Those terms remain in the similarity calculation.

## Disposition rules

For each county/entity comparison:

1. `conflict`: useful names have similarity below `name_conflict_threshold`
   and share no distinctive whole-word token. Address agreement does not
   override materially incompatible names.
2. `ready_for_review`: useful names meet `name_match_threshold`, share a
   distinctive token, and have agreeing useful addresses, an accepted entity
   status, and distinct source labels. Names must be identical or differ only
   through explicit word-variation pairs, currently Property/Properties and
   Holding/Holdings. Added, removed, reordered, or unlisted differing words
   require research. Known legal suffixes, ordered name numbers, and protected
   name identifiers must not disagree. The record still needs human review.
3. `needs_research`: all other cases, including missing evidence, unsupported
   status, different addresses, or similarity below the readiness threshold.

Below-threshold names that share a distinctive token and address get a
`possible_related_entities` research hint. This is not a claim about parentage.
The original county owner remains intact; no entity is substituted for it.

At the property level:

- Comparisons with two useful names rank ahead of comparisons missing useful
  name evidence. Within that group, readiness rules rank ahead of research,
  which rank ahead of conflicts. Workflow score ranks comparisons within those
  groups; source-record IDs break ties reproducibly. This prevents score-weight
  changes from promoting an unrelated comparison over a qualifying candidate,
  and prevents a blank/placeholder candidate from hiding a valid conflict.
  All incomplete records remain in the evidence packet.
- Two or more entity candidates with similarity at or above
  `ambiguity_name_threshold` force `needs_research`, even if one candidate has
  a different address or unsupported status. All candidates remain available
  for inspection; ranking never selects an approved owner.
- Multiple useful county records force `needs_research`, even if their values
  agree. This conservative POC does not decide which county snapshot is current.
- An unrelated low-similarity candidate does not override a qualifying
  candidate. Its separate conflict evidence remains visible in the packet.

Threshold decisions use full-precision similarity. Only the displayed workflow
score is rounded to two decimals.

## Workflow score and counts

```text
workflow score = name_weight × name_similarity
               + address_weight × address_agreement

Default: 70 × name_similarity + 30 × address_agreement
```

Unknown name similarity contributes zero. Address agreement contributes 1 only
when `true`; disagreement and unknown evidence contribute zero. Weights must
be finite, nonnegative, and sum to 100. These weights are illustrative policy,
not fitted statistics. The score does not determine disposition by itself.

For example, an exact-name/address candidate with a dissolved status has score
100 but requires research. An exact candidate amid multiple plausible entities
also has score 100 while the property requires research.

`source_count` counts useful source types (0–2), where a usable comparison name
is required. It does not count rows, assume that blanks are useful, or prove
source independence. A blank or placeholder entity-name row leaves the count
at 1 when the county has a useful owner name.

`corroborating_source_count` is 2 only when all readiness checks pass, otherwise
0. There is no one-source corroboration in this two-feed workflow. At the
property level, ambiguity cancels corroboration even when a candidate pair
has agreement. Both levels are retained to explain that difference.

Source counts and address agreement are derived from preserved evidence.
Corroboration flags/counts are derived from disposition, and a property's score
is derived from its leading comparison. These values cannot be supplied
independently to the dataclass constructors. Basic score/enum checks and a
nonempty candidate requirement reject malformed result objects. The models are
data containers, not a substitute for persisted human decisions or future
Salesforce export validation.

Distinct source labels are required for readiness, but do not prove independent
upstream sourcing. Real adapters would need source-lineage evidence. There is
no temporal ownership reconciliation or automatic staleness check in V1.

## Configuration and reproducibility

`config/matching.toml` contains:

| Setting | Default | Meaning |
|---|---|---|
| `name_match_threshold` | 0.90 | Minimum similarity for readiness, subject to every other check |
| `name_conflict_threshold` | 0.65 | Below this, materially different names without distinctive overlap conflict |
| `ambiguity_name_threshold` | 0.75 | Candidates at/above this count toward ambiguity |
| `name_weight` / `address_weight` | 70 / 30 | Workflow score contributions |
| `accepted_entity_statuses` | `active` | Status whitelist for readiness |
| `allowed_name_variations` | Property/Properties, Holding/Holdings | Explicit word pairs permitting minor fuzzy variation |
| `generic_name_tokens` | See file | Generic words excluded from distinctive-token evidence |
| `version` | `2` | Human-readable policy label |

The loader rejects unknown fields, invalid types, nonfinite numbers, inconsistent
threshold ordering, invalid weights, invalid variation pairs, and an empty
status whitelist. An empty variation whitelist allows only exact comparison
names. Omitted
fields use the defaults from `MatchingPolicy`. Results also include a SHA-256
fingerprint of actual settings, so a policy edit is detectable even if its
version label was not updated. Matching and normalization versions accompany
the policy metadata. Re-running the same evidence and policy is deterministic.

Normalization maps remain code constants; changes should be tested and bump
the normalization version. Missing-evidence safeguards, legal-suffix/number
checks, protected name-identifier checks, source-label checks, ambiguity blocking,
and unreviewed output are fixed
controls. Configuration cannot enable automatic approval or Salesforce writes.

## Actual fictional sample results

Generated by the default policy, and asserted in the test suite:

| Property | Disposition | Workflow score | Explanation |
|---|---|---:|---|
| P001 | ready_for_review | 100.00 | LLC punctuation and address agree |
| P002 | ready_for_review | 93.91 | Property/Properties variation; strong spelling evidence and same address |
| P003 | conflict | 68.89 | Incompatible names; common Healthcare term and address do not rescue them |
| P004 | needs_research | 0.00 | No entity candidate |
| P005 | needs_research | 70.00 | Same name but different addresses |
| P006 | ready_for_review | 100.00 | Incorporated/Inc. equivalents |
| P007 | ready_for_review | 100.00 | Ampersand/and equivalents |
| P008 | ready_for_review | 100.00 | Casing and whitespace equivalents |
| P009 | ready_for_review | 100.00 | Equivalent address abbreviations, same suite |
| P010 | needs_research | 0.00 | No county record |
| P011 | needs_research | 100.00 | Dissolved entity status |
| P012 | needs_research | 68.72 | Possible related-entity hint; no inferred parent relationship |
| P013 | needs_research | 100.00 | Two plausible candidates at the shared agent address |
| P014 | needs_research | 70.00 | Both addresses missing, not corroborating |
| P015 | needs_research | 0.00 | Entity row contains no useful name/address and unknown status |

Six are ready for review, eight need research, and one is a conflict. **Zero are
approved.** The local JSON report preserves all candidate details for inspection.

## Report and regression safeguards

The CLI checks output paths before loading or writing. An output path that
matches any input CSV or the selected policy file is rejected, including through
resolved symbolic links or hard-link aliases. Report content is written to a
temporary file in the output directory and replaces the report only after a
complete write. A failed replacement preserves the previous report and removes
the temporary file. Write errors produce a clear CLI error.

Milestone 3 also protects the review database: report output rejects existing
SQLite content regardless of extension or aliases, plus database and SQLite
sidecar filenames. Protection is checked before analysis and again before writing.

The 207-test suite covers the original samples and the self-review failures:
placeholder names/addresses, significant name edits, reordered/repeated numeric
identifiers, protected series/directional tokens, incomplete-candidate conflict
suppression, directional-city idempotence, malformed result objects, and report
path collisions. Input-protection tests compare original bytes after rejected
writes, rather than relying on a success/error message.

Matching tests do not prove legal ownership accuracy. Milestone 3 adds separate
tests for human decisions, audit preservation, and approval invalidation after
evidence changes. [Milestone 5](SALESFORCE.md) adds guarded Salesforce proposals
from current human approval; matching never authorizes export.

## Limits and optional AI extension

Heuristics can still produce false positives: entities may share a distinctive
word and address, registered-agent offices may represent unrelated companies,
and close spellings can denote different legal identities. Abbreviations such
as Med/Medical, spelling changes, and reordering can also produce false negatives.
No thresholds or confidence values have been validated against real ownership
outcomes. Real ingestion would also require candidate discovery and deduplication.

No AI is called or required. The pure `compare_records` function is the future
extension point for an optional ambiguous-case assistant. Such an assistant
would receive original evidence and deterministic explanations and return a
separate advisory suggestion with provenance. It must not mutate source values,
override review safeguards, approve records, or generate Salesforce payloads.
No unused AI abstraction or provider dependency is introduced in this milestone.
