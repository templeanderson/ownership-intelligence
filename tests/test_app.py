from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest
from streamlit.testing.v1 import AppTest

from net_lease_ownership.ingestion import load_dataset
from net_lease_ownership.matching import reconcile_dataset
from net_lease_ownership.models import ReviewStatus
from net_lease_ownership.repository import Repository
from net_lease_ownership.review import submit_review


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "src/net_lease_ownership/app.py"


@pytest.fixture
def database(tmp_path, monkeypatch):
    path = tmp_path / "ownership.sqlite3"
    monkeypatch.setenv("NLOI_DATABASE", str(path))
    with Repository(path) as repository:
        repository.save_results(reconcile_dataset(load_dataset(ROOT / "data")))
    return path


def run_app():
    app = AppTest.from_file(str(APP), default_timeout=15).run()
    assert not app.exception
    return app


def by_label(elements, label):
    return next(element for element in elements if element.label == label)


def open_record(app, property_id="P001"):
    app.radio(key="page").set_value("Review Queue").run()
    by_label(app.selectbox, "Property to review").set_value(property_id).run()
    assert not app.exception


def fill(app, action="Approve", candidate=0, reviewer="Example Reviewer", note=""):
    if candidate is not None:
        by_label(app.selectbox, "Company to approve").set_value(candidate).run()
    by_label(app.selectbox, "Decision").set_value(action)
    by_label(app.text_input, "Reviewer name").set_value(reviewer)
    by_label(app.text_area, "Reviewer note").set_value(note)


def save(app):
    by_label(app.button, "Save review decision").click().run()
    assert not app.exception


def test_dashboard_counts_and_no_automatic_decisions(database):
    app = run_app()
    assert {metric.label: metric.value for metric in app.metric} == {
        "Total properties": "15", "Ready for review": "6", "Needs research": "8", "Conflicts": "1", "Approved": "0"}
    assert len(app.dataframe[0].value) == 15
    with Repository(database) as repository:
        assert all(record.review_status == ReviewStatus.UNREVIEWED for record in repository.list_records())
        assert all(len(repository.history(record.property_id)) == 1 for record in repository.list_records())


def test_detail_shows_source_fields_and_analysis_without_preselected_approval(database):
    app = run_app()
    open_record(app)
    assert by_label(app.selectbox, "Decision").value is None
    assert by_label(app.selectbox, "Company to approve").value is None
    text = "\n".join(element.value for element in app.text)
    assert "ABC MEDICAL HOLDINGS, L.L.C." in text
    assert "ABC Medical Holdings LLC" in text
    assert "100 Sample Office Road, Dallas, TX 75001" in text
    assert "Example Agent One" in text
    assert not app.metric
    assert any(element.label == "Source details" and not element.proto.expanded for element in app.expander)
    assert any(element.value == "Review history" for element in app.subheader)
    assert not app.json


@pytest.mark.parametrize("action,status", [("Approve", ReviewStatus.APPROVED),
    ("Needs Research", ReviewStatus.NEEDS_RESEARCH), ("Reject", ReviewStatus.REJECTED)])
def test_ui_decisions_persist_and_refresh_form(database, action, status):
    app = run_app()
    open_record(app)
    fill(app, action=action)
    save(app)
    assert not app.error
    assert by_label(app.selectbox, "Decision").value is None
    assert by_label(app.selectbox, "Company to approve").value is None
    with Repository(database) as repository:
        record = repository.get_record("P001")
        assert record.review_status == status
        event = repository.history("P001")[-1]
        assert event.reviewer_name == "Example Reviewer"
        assert event.new_state == status
        assert len(repository.history("P001")) == 2
    # A fresh UI session sees the committed state.
    fresh = run_app()
    fresh.radio(key="page").set_value("Approved records").run()
    if status == ReviewStatus.APPROVED:
        assert fresh.dataframe[0].value.iloc[0]["Approved company"] == "ABC MEDICAL HOLDINGS, L.L.C."
    else:
        assert any("No records" in message.value for message in fresh.info)


@pytest.mark.parametrize("missing", ["decision", "candidate", "reviewer"])
def test_incomplete_form_cannot_approve(database, missing):
    app = run_app()
    open_record(app)
    fill(app, candidate=None if missing == "candidate" else 0,
         reviewer="" if missing == "reviewer" else "Reviewer")
    if missing == "decision":
        by_label(app.selectbox, "Decision").set_value(None)
    save(app)
    assert app.error
    with Repository(database) as repository:
        assert repository.get_record("P001").review_status == ReviewStatus.UNREVIEWED
        assert len(repository.history("P001")) == 1


def test_warning_override_needs_note_and_keeps_selected_candidate(database):
    app = run_app()
    open_record(app, "P013")
    fill(app, candidate=1)
    save(app)
    assert "Add a note explaining why" in app.error[0].value
    with Repository(database) as repository:
        assert repository.get_record("P013").review_status == ReviewStatus.UNREVIEWED
    by_label(app.text_area, "Reviewer note").set_value("Investigated the second candidate.")
    save(app)
    with Repository(database) as repository:
        approved = repository.get_record("P013")
        assert approved.review_status == ReviewStatus.APPROVED
        assert approved.selected_candidate == 1
        assert repository.history("P013")[-1].note == "Investigated the second candidate."
        expected_name = approved.evidence["candidates"][1]["entity_record"]["entity_name"]
    app.radio(key="page").set_value("Approved records").run()
    assert app.dataframe[0].value.iloc[0]["Approved company"] == expected_name


@pytest.mark.parametrize("property_id", ["P004", "P015"])
def test_missing_entity_is_visible_and_cannot_be_approved(database, property_id):
    app = run_app()
    open_record(app, property_id)
    fill(app, note="Investigation complete")
    save(app)
    assert "no usable company name" in app.error[0].value
    with Repository(database) as repository:
        assert repository.get_record(property_id).review_status == ReviewStatus.UNREVIEWED


@pytest.mark.parametrize("change", ["evidence", "decision"])
def test_stale_ui_never_approves_new_evidence_or_overwrites_review(database, change):
    app = run_app()
    open_record(app)
    fill(app)
    shown = app.session_state["displayed_record"]
    with Repository(database) as other:
        if change == "evidence":
            results = reconcile_dataset(load_dataset(ROOT / "data"))
            other.save_results([replace(results[0], matching_version="next")])
        else:
            submit_review(other, "P001", ReviewStatus.REJECTED, reviewer_name="Other reviewer",
                expected_snapshot_id=shown.snapshot_id, expected_revision=shown.revision)
        before = other.get_record("P001")
        history = other.history("P001")
    save(app)
    assert "Refresh the property information" in app.error[0].value
    assert app.session_state["displayed_record"].revision == shown.revision
    with Repository(database) as other:
        assert other.get_record("P001") == before
        assert other.history("P001") == history
    app.button(key="refresh_record").click().run()
    assert app.session_state["displayed_record"].revision == before.revision
    assert by_label(app.selectbox, "Decision").value is None


def test_property_switch_does_not_reuse_another_propertys_draft(database):
    app = run_app()
    open_record(app)
    fill(app)
    by_label(app.selectbox, "Property to review").set_value("P002").run()
    assert by_label(app.selectbox, "Decision").value is None
    assert by_label(app.selectbox, "Company to approve").value is None
    assert by_label(app.text_input, "Reviewer name").value == ""
    save(app)
    with Repository(database) as repository:
        assert repository.get_record("P002").review_status == ReviewStatus.UNREVIEWED


def test_missing_database_only_loads_after_explicit_click(tmp_path, monkeypatch):
    path = tmp_path / "new.sqlite3"
    monkeypatch.setenv("NLOI_DATABASE", str(path))
    app = run_app()
    assert not path.exists()
    app.button(key="load_samples").click().run()
    assert not app.exception
    with Repository(path) as repository:
        assert len(repository.list_records()) == 15
        assert all(record.review_status == ReviewStatus.UNREVIEWED for record in repository.list_records())


def test_reload_samples_preserves_decisions_for_identical_evidence(database):
    app = run_app()
    open_record(app)
    fill(app)
    save(app)
    app.button(key="load_samples").click().run()
    with Repository(database) as repository:
        assert repository.get_record("P001").review_status == ReviewStatus.APPROVED
        assert len(repository.history("P001")) == 2


def test_bad_database_shows_error_without_destroying_file(tmp_path, monkeypatch):
    path = tmp_path / "foreign.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated(value TEXT)")
        connection.execute("PRAGMA user_version=1")
    before = path.read_bytes()
    monkeypatch.setenv("NLOI_DATABASE", str(path))
    app = run_app()
    assert "could not open" in app.error[0].value
    assert path.read_bytes() == before


def test_no_salesforce_payload_or_export_controls(database):
    app = run_app()
    app.radio(key="page").set_value("Approved records").run()
    assert not app.exception
    assert any("Salesforce export is not available yet" in message.value for message in app.info)
    assert not app.get("download_button")
    assert all("export" not in button.label.casefold() for button in app.button)


def test_review_queue_filters_current_status(database):
    app = run_app()
    open_record(app)
    fill(app)
    save(app)
    app.selectbox(key="status_filter").set_value("approved").run()
    assert list(app.dataframe[0].value["Property"]) == ["P001"]
    app.selectbox(key="status_filter").set_value("unreviewed").run()
    assert len(app.dataframe[0].value) == 14
    assert "P001" not in list(app.dataframe[0].value["Property"])
    app.selectbox(key="status_filter").set_value("rejected").run()
    assert any("No records" in message.value for message in app.info)


def test_approved_view_allows_revocation_and_updates_count(database):
    app = run_app()
    open_record(app)
    fill(app)
    save(app)
    app.radio(key="page").set_value("Approved records").run()
    fill(app, action="Reject", candidate=None, note="Further evidence contradicts this selection.")
    save(app)
    assert any("No records" in message.value for message in app.info)
    with Repository(database) as repository:
        assert repository.get_record("P001").review_status == ReviewStatus.REJECTED
        assert repository.get_record("P001").selected_candidate is None
        assert [event.new_state for event in repository.history("P001")] == [
            ReviewStatus.UNREVIEWED, ReviewStatus.APPROVED, ReviewStatus.REJECTED]
    app.radio(key="page").set_value("Dashboard").run()
    assert by_label(app.metric, "Approved").value == "0"
