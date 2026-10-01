# Fictional sample scenarios

All properties, tenants, entities, agents, street addresses, and records are
invented. City names provide geographic context only. Dates are fixed fixture
values, not claims about live records. There are 15 properties, 14 county records,
and 15 entity candidates. Each feed has its own source-record identifiers.

`property_id` in a feed means the record is associated with that property's
research packet. In the entity feed it does **not** assert confirmed ownership.
The labels below describe fixture intent, not approval decisions. Milestone 2
implements and tests these scenarios; actual default-policy results are listed
in [matching documentation](../docs/MATCHING.md). All evidence must still receive
human review.

| Property | Scenario | Intended matching behavior in Milestone 2 |
|---|---|---|
| P001 | ABC Medical Holdings LLC versus L.L.C.; same address | Strong evidence, ready for human review |
| P002 | Property versus Properties; same address | Explain minor variation and evaluate likely match |
| P003 | DFW Healthcare versus Lone Star Healthcare; same address | Flag conflicting ownership names; address alone is insufficient |
| P004 | County record only | Needs research; no corporate corroboration |
| P005 | Same entity name, different addresses | Needs research; do not confidently match on name alone |
| P006 | Incorporated versus Inc. | Normalize suffix spelling, preserve original names |
| P007 | Ampersand versus “and”; dotted LLC | Normalize superficial punctuation differences |
| P008 | Casing and repeated/leading/trailing spaces | Normalize comparisons while preserving source text |
| P009 | North/N., Road/Rd., Ste./Suite, same suite number | Normalize equivalent address formats |
| P010 | Entity candidate only | Needs research; county owner unavailable |
| P011 | Names and addresses agree, corporate status dissolved | Status discrepancy requires research |
| P012 | Property-specific LLC versus possible parent at same address | Flag possible relationship; never infer parentage or substitute the parent |
| P013 | Two similarly named candidates, same agent/address | Flag ambiguity; do not pick the first candidate silently |
| P014 | Both addresses absent | Missing values are not address corroboration |
| P015 | Entity feed row exists but contains no entity name/address | A row's existence is not useful source corroboration |

Neither these fixtures nor normalization establish entity identity, legal
ownership, a corporate hierarchy, or approval.
