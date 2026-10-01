# Milestone 6 validation and GitHub readiness

The six-milestone POC is implemented. Final verification runs locally before
any additional commit, push, deployment, or Salesforce action.

## Local verification

Run from the repository directory:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pip check
git diff --check
```

The full suite has **414 passing tests** on Python 3.12.14, pytest 8.4.2, and
Streamlit 1.64.0. It includes 23 Streamlit AppTest cases and one full CLI workflow
across separate processes. No test uses a live feed, Salesforce org, or external AI.

| Area | Evidence |
|---|---|
| Normalization/ingestion | Original values retained; formatting, placeholders, missing fields, CSV shape, dates, and associations checked |
| Matching | Exact/minor variations, identifiers, legal suffixes, conflicting addresses/names, missing sources, ambiguity, and policy validation |
| Persistence/review | Real SQLite state transitions, immutable snapshots, append-only history, rollback, changed-evidence invalidation, and stale revisions |
| Browser workflow | Forms driven in AppTest; defaults, selected candidate, decisions, filters, stale displays, approved comparisons, and actual download bytes |
| Salesforce boundary | Every unapproved state blocked; selected-company fields/score, audit context, malformed metadata, whitespace, UTC dates, and disabled writes |
| End-to-end CLI | Matching report → database import → blocked unapproved export → second-company approval → proposed JSON → rejection → blocked export and preserved audit |

Tests validate workflow behavior, not legal ownership accuracy. AppTest does not
prove every browser layout, download dialog, or keyboard interaction. Earlier
milestones included local browser inspection; the final regressions inspect
Streamlit's actual registered download bytes without contacting Salesforce.

The workspace's editable-install `.pth` file has previously acquired a macOS
hidden flag, causing imports to be skipped. CLI/app commands explicitly use
`PYTHONPATH=src`; pytest uses its configured `pythonpath`. This avoids relying on
that workspace-specific installation behavior. Normal installed environments
can use `python -m net_lease_ownership` without the source-path prefix.

Dependency compatibility is checked with `pip check`. Versions are bounded in
`pyproject.toml` and `requirements.txt`, not fully locked; a future installation
may resolve newer compatible versions. Python 3.11 support is declared and
configured in CI, but the local run verifies Python 3.12 only. macOS/Linux are
covered by documented commands; Windows is not locally verified.

## GitHub Actions

`.github/workflows/tests.yml` runs on pushes to main and pull requests. It installs
requirements, checks dependency compatibility, and runs the full suite on
Python 3.11 and 3.12. It uses read-only repository permissions, disables retained
checkout credentials, and has no deployment, release, credential, or live-write step.
The action usage follows the official [checkout](https://github.com/actions/checkout)
and [setup-python](https://github.com/actions/setup-python) documentation.

The workflow is configured locally. It has not run on GitHub because Milestone 6
has not been committed or pushed. No passing CI badge or remote result is claimed.
Major action tags and bounded pip dependencies can change; CI will test their
resolved behavior when this revision is published.

## Repository inspection

Source code, documentation, fictional CSVs, policy, tests, and CI configuration
are intended for Git. Virtual environments, Python caches, generated reports,
SQLite databases/sidecars, `.env` files, and Streamlit secrets are ignored.
`.env.example` contains only comments explaining optional local configuration.
No real credentials or production data are needed for the project.

Before publishing, inspect `git status` and the staged diff to confirm only the
intended files are included. There is no project license grant in this repository;
licensing remains a product-owner decision. GitHub publication and any subsequent
production integration require separate user instructions.

## Manual acceptance

Use the isolated [demo walkthrough](DEMO.md). Inspect P001's approval/history,
P003/P005's conflict or research behavior, P012's related-entity warning, and
P013's explicit second-company approval, download, and revocation. These scenarios
are illustrative evidence packets, not a statistical evaluation dataset.

All six milestones stop here. No further action proceeds automatically.
