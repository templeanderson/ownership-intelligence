"""Streamlit review UI. Matching, persistence, and decision rules remain separate."""

from collections import Counter
from datetime import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import streamlit as st

from net_lease_ownership.__main__ import _validate_output
from net_lease_ownership.ingestion import load_dataset
from net_lease_ownership.matching import reconcile_dataset
from net_lease_ownership.models import ReviewStatus
from net_lease_ownership.policy import load_policy
from net_lease_ownership.repository import Repository, ReviewRecord
from net_lease_ownership.review import StaleReviewError, submit_review
from net_lease_ownership.salesforce import PayloadError, generate_payload


ROOT = Path(__file__).resolve().parents[2]
LOGGER = logging.getLogger(__name__)
LABELS = {"unreviewed": "Unreviewed", "approved": "Approved", "rejected": "Rejected",
          "needs_research": "Needs Research", "ready_for_review": "Ready for review", "conflict": "Conflict"}
ACTIONS = {"Approve": ReviewStatus.APPROVED, "Needs Research": ReviewStatus.NEEDS_RESEARCH,
           "Reject": ReviewStatus.REJECTED}

REVIEW_ISSUES = {
    "missing_county_record": "County record missing.",
    "missing_county_owner": "County owner name missing.",
    "missing_entity_record": "Company record missing.",
    "missing_entity_name": "Company name missing.",
    "missing_county_address": "County address unavailable.",
    "missing_entity_address": "Company address unavailable.",
    "address_disagreement": "Addresses differ.",
    "entity_status_requires_research": "Company status needs checking.",
    "duplicate_source_identity": "Records do not provide independent confirmation.",
    "name_variation": "Names differ.",
    "legal_suffix_disagreement": "Names may identify different legal companies.",
    "name_number_disagreement": "Names may identify different legal companies.",
    "name_identifier_disagreement": "Names may identify different legal companies.",
    "unapproved_name_variation": "Names may identify different legal companies.",
    "no_shared_distinctive_name_token": "Names may identify different legal companies.",
    "name_below_review_threshold": "Names may identify different legal companies.",
    "possible_related_entities": "Related companies may still be separate legal owners.",
    "multiple_plausible_entity_candidates": "More than one company could match. Check each option.",
    "multiple_county_owner_records": "More than one county owner is listed. Check each option.",
}

REVIEW_ERRORS = {
    "Choose a decision explicitly": "Choose Approve, Needs Research, or Reject before saving.",
    "reviewer_name must be a nonblank string": "Enter your name before saving your decision.",
    "Approval requires an explicit valid candidate index": "Choose the company you want to approve before saving.",
    "Approval requires a candidate with a useful entity name": "This option has no usable company name. Mark it Needs Research or Reject instead.",
    "Approval overriding property or selected candidate warnings requires a reviewer note":
        "Add a note explaining why you are approving despite the issues listed above.",
}


def _rows(records) -> list[dict]:
    return [{"Property": record.property_id, "Address": record.evidence["property"]["property_address"],
             "Tenant": record.evidence["property"]["tenant"],
             "Recommendation": LABELS[record.evidence["disposition"]],
             "Review status": LABELS[record.review_status.value],
             "Match score": record.evidence["confidence_score"]} for record in records]


def _analysis(evidence: dict, candidate: dict) -> None:
    codes = set(evidence["discrepancies"]) | set(candidate["discrepancies"])
    if evidence["disposition"] == candidate["disposition"] == "ready_for_review":
        st.success("Ready for your review. Confirm the owner using the records below.")
        return
    # Formatting differences add no useful warning when more specific name issues exist.
    if codes - {"name_variation"}:
        codes.discard("name_variation")
    issues = dict.fromkeys(REVIEW_ISSUES.get(code, "Additional checking needed.")
                           for code in REVIEW_ISSUES if code in codes)
    st.warning(" ".join(issues) or "Check ownership before approving.")


def _source(record: dict | None, *, county: bool) -> None:
    if record is None:
        st.text("No county record available." if county else "No company record available.")
        return
    name = record["owner_name" if county else "entity_name"] or "Name missing"
    address = record["mailing_address" if county else "registered_address"] or "Address missing"
    lines = [name, address]
    if not county:
        lines.append(f"Status: {record['entity_status'] or 'Not supplied'}")
    st.text("\n".join(lines))


def _source_details(record: dict | None, *, county: bool) -> None:
    if record is None:
        return
    st.caption("County record" if county else "Company record")
    if not county:
        st.text(f"Registered agent: {record['registered_agent'] or 'Not supplied'}")
    st.text(f"Source: {record['source_name']} · Reference: {record['source_record_id']} · Record date: {record['source_as_of']}")


def _detail(repository: Repository, property_id: str, database: Path) -> None:
    context = (str(database), property_id)
    if st.button("Refresh property information", key="refresh_record"):
        st.session_state.pop("displayed_record", None)
    displayed = st.session_state.get("displayed_record")
    if displayed is None or st.session_state.get("displayed_context") != context:
        displayed = repository.get_record(property_id)
        st.session_state["displayed_record"] = displayed
        st.session_state["displayed_context"] = context
    current = repository.get_record(property_id)
    stale = (current.snapshot_id, current.revision) != (displayed.snapshot_id, displayed.revision)
    if stale:
        st.warning("This property's information or review status has changed. Refresh it and check the latest records before saving a decision.")
    evidence = displayed.evidence
    st.subheader(f"Property {property_id}")
    st.text(evidence["property"]["property_address"])
    st.write("Tenant:", evidence["property"]["tenant"] or "Not supplied")
    st.write("Review status:", LABELS[displayed.review_status.value])
    if displayed.selected_candidate is not None:
        entity = evidence["candidates"][displayed.selected_candidate]["entity_record"]
        st.write("Approved company:", entity["entity_name"])
    st.subheader("Ownership check")

    token = hashlib.sha256(f"{context}:{displayed.snapshot_id}:{displayed.revision}".encode()).hexdigest()[:16]
    candidate_key = f"candidate_{token}"
    choice = st.selectbox("Company to approve", range(len(evidence["candidates"])), index=None,
        key=candidate_key, placeholder="Choose a company after checking the records",
        format_func=lambda i: f"{i + 1}: " + (
            evidence["candidates"][i]["entity_record"]["entity_name"] or "Company name missing"
            if evidence["candidates"][i]["entity_record"] else "No company record"))
    preview_index = choice if choice is not None else (
        displayed.selected_candidate if displayed.selected_candidate is not None else 0)
    _analysis(evidence, evidence["candidates"][preview_index])
    for index, candidate in enumerate(evidence["candidates"]):
        label = f"Company option {index + 1}"
        if displayed.selected_candidate == index:
            label += " — approved"
        elif choice == index:
            label += " — selected"
        with st.expander(label, expanded=(index == preview_index)):
            county, entity = st.columns(2)
            with county:
                st.write("**County owner**")
                _source(candidate["county_record"], county=True)
            with entity:
                st.write("**Company record**")
                _source(candidate["entity_record"], county=False)
            with st.expander("Source details"):
                _source_details(candidate["county_record"], county=True)
                _source_details(candidate["entity_record"], county=False)

    st.subheader("Your decision")
    st.caption("Approving with warnings requires a note explaining why.")
    with st.form(f"review_{token}"):
        action = st.selectbox("Decision", list(ACTIONS), index=None, key=f"decision_{token}",
                              placeholder="Choose a decision")
        reviewer = st.text_input("Reviewer name", key=f"reviewer_{token}")
        note = st.text_area("Reviewer note", key=f"note_{token}")
        submitted = st.form_submit_button("Save review decision")
    if submitted:
        try:
            if action is None:
                raise ValueError("Choose a decision explicitly")
            submit_review(repository, property_id, ACTIONS[action], reviewer_name=reviewer,
                expected_snapshot_id=displayed.snapshot_id, expected_revision=displayed.revision,
                selected_candidate=choice if ACTIONS[action] == ReviewStatus.APPROVED else None,
                note=note or None)
        except StaleReviewError:
            st.error("Your decision was not saved because this property changed. Refresh the property information, check the latest records, and try again.")
        except ValueError as error:
            st.error(REVIEW_ERRORS.get(str(error), "Your decision could not be saved. Check your entries and try again."))
        except (KeyError, sqlite3.Error):
            st.error("Your decision could not be saved. Refresh the property information and try again.")
        else:
            st.session_state.pop("displayed_record", None)
            st.session_state["review_message"] = f"{action} decision saved for {property_id}."
            st.rerun()
    if displayed.review_status == ReviewStatus.APPROVED:
        st.subheader("Salesforce export")
        approved_name = evidence["candidates"][displayed.selected_candidate]["entity_record"]["entity_name"]
        st.caption(f"Download a proposal for {approved_name}. No data is sent to Salesforce.")
        try:
            payload = generate_payload(repository, property_id,
                expected_snapshot_id=displayed.snapshot_id, expected_revision=displayed.revision)
        except PayloadError:
            st.error("Export unavailable. Refresh this property and check its approval and records.")
        else:
            st.download_button("Download proposed JSON", json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False),
                               file_name=f"salesforce-{property_id}.json", mime="application/json",
                               key=f"export_{token}")
    st.subheader("Review history")
    history = []
    for event in repository.history(property_id):
        company = "—"
        if event.selected_candidate is not None:
            snapshot = repository.get_snapshot(event.snapshot_id)
            company = snapshot["candidates"][event.selected_candidate]["entity_record"]["entity_name"]
        note = event.note or ""
        if event.actor_kind == "system":
            note = "Property records loaded." if event.previous_state is None else "Property records changed. A new review is needed."
        try:
            event_time = datetime.fromisoformat(event.timestamp).astimezone(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d %I:%M %p %Z")
        except (TypeError, ValueError, OverflowError):
            event_time = "Invalid date"
        history.append({
            "Date and time": event_time,
            "Reviewed by": event.reviewer_name or "System",
            "Previous status": LABELS[event.previous_state.value] if event.previous_state else "—",
            "New status": LABELS[event.new_state.value], "Approved company": company, "Note": note,
        })
    st.dataframe(history, hide_index=True, width="stretch")


def main() -> None:
    st.set_page_config(page_title="Ownership Intelligence", layout="wide")
    st.title("Net-Lease Ownership Intelligence")
    st.caption("Sample ownership records · Review decisions · Review history")
    database = Path(os.environ.get("NLOI_DATABASE", str(ROOT / "ownership.sqlite3"))).expanduser().resolve()
    page = st.sidebar.radio("View", ["Dashboard", "Review Queue", "Approved records"], key="page")
    st.sidebar.caption("Sample property records for local review")
    if st.sidebar.button("Load sample property records", key="load_samples"):
        try:
            _validate_output(database, ROOT / "data", ROOT / "config/matching.toml", label="Database path")
            results = reconcile_dataset(load_dataset(ROOT / "data"), load_policy(ROOT / "config/matching.toml"))
            with Repository(database) as repository:
                repository.save_results(results)
            st.session_state.pop("displayed_record", None)
            st.success("Sample property records loaded. Updated records need a new review; existing decisions are kept when the records have not changed.")
        except (OSError, ValueError, sqlite3.Error):
            LOGGER.exception("Could not load sample review records")
            st.error("The sample records could not be loaded. Check the source files and try again.")
    if "review_message" in st.session_state:
        st.success(st.session_state.pop("review_message"))
    if not database.is_file():
        st.info("Load the sample property records to begin. Approval always requires your decision.")
        return
    try:
        with Repository(database) as repository:
            records = repository.list_records()
            if page == "Dashboard":
                st.subheader("Portfolio dashboard")
                counts = Counter(record.evidence["disposition"] for record in records)
                metrics = [("Total properties", len(records)), ("Ready for review", counts["ready_for_review"]),
                           ("Needs research", counts["needs_research"]), ("Conflicts", counts["conflict"]),
                           ("Approved", sum(r.review_status == ReviewStatus.APPROVED for r in records))]
                for column, (label, value) in zip(st.columns(5), metrics):
                    column.metric(label, value)
                st.caption("Match results and review decisions are counted separately. A property can appear in both.")
                st.dataframe(_rows(records), hide_index=True, width="stretch")
                return
            if page == "Approved records":
                st.subheader("Approved records")
                st.caption("Select an approved property to download its proposed Salesforce JSON.")
                visible = [r for r in records if r.review_status == ReviewStatus.APPROVED]
                rows = _rows(visible)
                for row, record in zip(rows, visible):
                    row["Approved company"] = record.evidence["candidates"][record.selected_candidate]["entity_record"]["entity_name"]
            else:
                st.subheader("Review queue")
                status = st.selectbox("Review status filter", ["All", *(status.value for status in ReviewStatus)], key="status_filter",
                                      format_func=lambda value: LABELS.get(value, value))
                visible = [r for r in records if status == "All" or r.review_status.value == status]
                rows = _rows(visible)
            if not visible:
                st.info("No records in this view.")
                return
            st.dataframe(rows, hide_index=True, width="stretch")
            property_id = st.selectbox("Property to review", [r.property_id for r in visible], key=f"property_{page}")
            _detail(repository, property_id, database)
    except (OSError, ValueError, KeyError, sqlite3.Error):
        LOGGER.exception("Could not open local review data")
        st.error("We could not open your saved property records. Check the saved review file and try again.")


if __name__ == "__main__":
    main()
