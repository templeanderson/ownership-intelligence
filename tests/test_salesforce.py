from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from net_lease_ownership.ingestion import load_dataset
from net_lease_ownership.matching import reconcile_dataset
from net_lease_ownership.models import ReviewStatus
from net_lease_ownership.repository import Repository
from net_lease_ownership.review import submit_review
from net_lease_ownership.salesforce import PayloadError, generate_payload


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repository(tmp_path):
    with Repository(tmp_path / 'reviews.sqlite3') as repo:
        repo.save_results(reconcile_dataset(load_dataset(ROOT / 'data')))
        yield repo


def approve(repo, property_id='P001', candidate=0, note=None):
    record = repo.get_record(property_id)
    return submit_review(repo, property_id, ReviewStatus.APPROVED, reviewer_name='Test Reviewer',
        expected_snapshot_id=record.snapshot_id, expected_revision=record.revision,
        selected_candidate=candidate, note=note)


def test_approved_payload_fields_and_preserved_provenance(repository):
    record = approve(repository)
    before = repository.history('P001')
    proposal = generate_payload(repository, 'P001')
    assert proposal.keys() == {'schema_version', 'dry_run', 'operation', 'external_id_field', 'records'}
    assert proposal['dry_run'] is True
    assert proposal['operation'] == 'upsert'
    assert proposal['external_id_field'] == 'External_Property_ID__c'
    assert len(proposal['records']) == 1
    fields = proposal['records'][0]
    assert fields == {
        'External_Property_ID__c': 'P001',
        'Property_Address__c': '101 Fictional Care Way, Dallas, TX 75001',
        'Ownership_Entity__c': 'ABC MEDICAL HOLDINGS, L.L.C.',
        'Registered_Agent__c': 'Example Agent One', 'Entity_Status__c': 'active',
        'Ownership_Confidence__c': 100.0,
        'Last_Verified_Date__c': datetime.fromisoformat(before[-1].timestamp).astimezone(timezone.utc).date().isoformat(),
        'Source_Provenance__c': fields['Source_Provenance__c'], 'Review_Status__c': 'approved',
    }
    provenance = json.loads(fields['Source_Provenance__c'])
    assert provenance['county_record'] == record.evidence['candidates'][0]['county_record']
    assert provenance['entity_record'] == record.evidence['candidates'][0]['entity_record']
    assert provenance['reviewer_name'] == 'Test Reviewer'
    assert provenance['reviewed_at'] == before[-1].timestamp
    assert provenance['evidence_fingerprint'] == record.fingerprint
    assert provenance['revision'] == record.revision
    assert json.loads(json.dumps(proposal, allow_nan=False)) == proposal
    assert repository.history('P001') == before
    assert repository.get_record('P001') == record


@pytest.mark.parametrize('status', [ReviewStatus.UNREVIEWED, ReviewStatus.REJECTED, ReviewStatus.NEEDS_RESEARCH])
def test_unapproved_cannot_export(repository, status):
    if status != ReviewStatus.UNREVIEWED:
        current = repository.get_record('P001')
        submit_review(repository, 'P001', status, reviewer_name='Reviewer',
            expected_snapshot_id=current.snapshot_id, expected_revision=current.revision)
    with pytest.raises(PayloadError, match='Only a currently approved'):
        generate_payload(repository, 'P001')


def test_exports_selected_second_candidate_and_its_score(repository):
    record = approve(repository, 'P013', 1, 'Confirmed the second company independently.')
    fields = generate_payload(repository, 'P013')['records'][0]
    chosen = record.evidence['candidates'][1]
    assert fields['Ownership_Entity__c'] == chosen['entity_record']['entity_name']
    assert fields['Ownership_Entity__c'] != record.evidence['candidates'][0]['entity_record']['entity_name']
    assert fields['Ownership_Confidence__c'] == chosen['confidence_score']
    provenance = json.loads(fields['Source_Provenance__c'])
    assert provenance['selected_candidate'] == 1
    assert provenance['reviewer_note'] == 'Confirmed the second company independently.'
    assert provenance['property_disposition'] == 'needs_research'


@pytest.mark.parametrize('change', ['reject', 'research', 'evidence'])
def test_revocation_and_new_evidence_invalidate_export(repository, change):
    previous = approve(repository)
    assert generate_payload(repository, 'P001')['dry_run']
    if change == 'evidence':
        results = reconcile_dataset(load_dataset(ROOT / 'data'))
        repository.save_results([replace(results[0], matching_version='changed')])
    else:
        submit_review(repository, 'P001', ReviewStatus.REJECTED if change == 'reject' else ReviewStatus.NEEDS_RESEARCH,
            reviewer_name='Second Reviewer', expected_snapshot_id=previous.snapshot_id,
            expected_revision=previous.revision)
    with pytest.raises(PayloadError, match='Only a currently approved'):
        generate_payload(repository, 'P001')


def test_stale_display_tokens_block_export_even_after_new_approval(repository):
    old = approve(repository)
    approve(repository)
    with pytest.raises(PayloadError, match='changed'):
        generate_payload(repository, 'P001', expected_snapshot_id=old.snapshot_id, expected_revision=old.revision)
    assert generate_payload(repository, 'P001')['dry_run']


@pytest.mark.parametrize('dry_run', [False, None, 1, 'true'])
def test_write_mode_is_unavailable(repository, dry_run):
    approve(repository)
    with pytest.raises(PayloadError, match='writes are disabled'):
        generate_payload(repository, 'P001', dry_run=dry_run)


@pytest.mark.parametrize('snapshot,revision', [(1, None), (None, 2), (True, 2), (1, 0)])
def test_invalid_display_tokens(repository, snapshot, revision):
    approve(repository)
    with pytest.raises(PayloadError):
        generate_payload(repository, 'P001', expected_snapshot_id=snapshot, expected_revision=revision)


# Corrupted contexts simulate malformed persistence at the integration boundary.
# Updating the fingerprint isolates field validation from the integrity guard.
@pytest.mark.parametrize('mutation', [
    'empty_address', 'wrong_property', 'missing_entity', 'blank_name', 'suffix_only_name',
    'negative_index', 'bool_index', 'out_of_range', 'nan_score', 'infinite_score',
    'bool_score', 'large_score', 'bad_status', 'bad_agent', 'bad_source_date', 'wrong_source_property',
])
def test_malformed_approved_record_rejected(repository, monkeypatch, mutation):
    current = approve(repository)
    record, history = repository.get_review_context('P001')
    evidence = json.loads(json.dumps(record.evidence))
    candidate = evidence['candidates'][0]
    entity = candidate['entity_record']
    index = record.selected_candidate
    if mutation == 'empty_address': evidence['property']['property_address'] = ' '
    elif mutation == 'wrong_property': evidence['property']['property_id'] = 'P002'
    elif mutation == 'missing_entity': candidate['entity_record'] = None
    elif mutation == 'blank_name': entity['entity_name'] = ' '
    elif mutation == 'suffix_only_name': entity['entity_name'] = 'LLC'
    elif mutation == 'negative_index': index = -1
    elif mutation == 'bool_index': index = True
    elif mutation == 'out_of_range': index = 999
    elif mutation == 'nan_score': candidate['confidence_score'] = float('nan')
    elif mutation == 'infinite_score': candidate['confidence_score'] = float('inf')
    elif mutation == 'bool_score': candidate['confidence_score'] = True
    elif mutation == 'large_score': candidate['confidence_score'] = 101
    elif mutation == 'bad_status': entity['entity_status'] = 42
    elif mutation == 'bad_agent': entity['registered_agent'] = None
    elif mutation == 'bad_source_date': entity['source_as_of'] = 'not a date'
    elif mutation == 'wrong_source_property': entity['property_id'] = 'P002'
    content = json.dumps(evidence, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    malformed = replace(record, evidence=evidence, selected_candidate=index,
        fingerprint=hashlib.sha256(content.encode()).hexdigest())
    event = replace(history[-1], selected_candidate=index)
    monkeypatch.setattr(repository, 'get_review_context', lambda _: (malformed, (*history[:-1], event)))
    with pytest.raises(PayloadError):
        generate_payload(repository, 'P001')
    assert repository.get_record('P001') == current


@pytest.mark.parametrize('mutation', ['no_event', 'system', 'wrong_revision', 'wrong_selection', 'no_reviewer', 'naive_time', 'fingerprint'])
def test_approval_must_have_matching_human_audit(repository, monkeypatch, mutation):
    record = approve(repository)
    history = repository.history('P001')
    event = history[-1]
    if mutation == 'no_event': history = ()
    elif mutation == 'system': event = replace(event, actor_kind='system')
    elif mutation == 'wrong_revision': event = replace(event, revision=1)
    elif mutation == 'wrong_selection': event = replace(event, selected_candidate=1)
    elif mutation == 'no_reviewer': event = replace(event, reviewer_name=' ')
    elif mutation == 'naive_time': event = replace(event, timestamp='2026-01-01T12:00:00')
    elif mutation == 'fingerprint': record = replace(record, fingerprint='wrong')
    if history: history = (*history[:-1], event)
    monkeypatch.setattr(repository, 'get_review_context', lambda _: (record, history))
    with pytest.raises(PayloadError): generate_payload(repository, 'P001')


def test_missing_property_is_clear_error(repository):
    with pytest.raises(PayloadError, match='Unknown property_id'):
        generate_payload(repository, 'missing')


def test_export_reads_review_and_audit_in_one_database_snapshot(repository, monkeypatch):
    approved = approve(repository)
    # WAL allows a second connection to commit between the two reads.
    repository._connection.execute("PRAGMA journal_mode=WAL")
    path = repository._connection.execute("PRAGMA database_list").fetchone()[2]
    get_record = repository.get_record
    changed = False

    def read_then_revoke(property_id):
        nonlocal changed
        record = get_record(property_id)
        if not changed:
            changed = True
            with Repository(path) as other:
                submit_review(other, property_id, ReviewStatus.REJECTED, reviewer_name="Other reviewer",
                    expected_snapshot_id=approved.snapshot_id, expected_revision=approved.revision)
        return record

    monkeypatch.setattr(repository, 'get_record', read_then_revoke)
    proposal = generate_payload(repository, 'P001')
    provenance = json.loads(proposal['records'][0]['Source_Provenance__c'])
    assert provenance['revision'] == approved.revision
    assert get_record('P001').review_status == ReviewStatus.REJECTED
    with pytest.raises(PayloadError, match='Only a currently approved'):
        generate_payload(repository, 'P001')


@pytest.mark.parametrize('timestamp,expected_date', [
    ('2001-12-31T23:30:00-02:00', '2002-01-01'),
    ('2002-01-01T00:30:00+02:00', '2001-12-31'),
])
def test_verification_date_uses_historical_approval_in_utc(repository, monkeypatch, timestamp, expected_date):
    monkeypatch.setattr('net_lease_ownership.repository._timestamp', lambda: timestamp)
    approve(repository)
    fields = generate_payload(repository, 'P001')['records'][0]
    assert fields['Last_Verified_Date__c'] == expected_date
    assert json.loads(fields['Source_Provenance__c'])['reviewed_at'] == timestamp


@pytest.mark.parametrize('timestamp', [
    '0001-01-01T00:00:00+14:00', '9999-12-31T23:59:59-14:00',
])
def test_timestamp_utc_overflow_is_clear_payload_error(repository, monkeypatch, timestamp):
    monkeypatch.setattr('net_lease_ownership.repository._timestamp', lambda: timestamp)
    approve(repository)
    with pytest.raises(PayloadError, match='cannot be represented in UTC'):
        generate_payload(repository, 'P001')


@pytest.mark.parametrize('field,value', [
    ('policy_version', None), ('policy_version', '  '), ('matching_version', 2),
    ('normalization_version', ''), ('policy_fingerprint', 'not-a-hash'),
    ('discrepancies', 'wrong-shape'), ('discrepancies', (None,)), ('discrepancies', (' ',)),
])
def test_imported_malformed_metadata_is_rejected_at_export(repository, field, value):
    result = reconcile_dataset(load_dataset(ROOT / 'data'))[0]
    repository.save_results([replace(result, **{field: value})])
    approve(repository)
    with pytest.raises(PayloadError):
        generate_payload(repository, 'P001')


@pytest.mark.parametrize('level,field,value', [
    ('property', 'disposition', 'unknown'), ('candidate', 'disposition', 'unknown'),
    ('candidate', 'discrepancies', 'wrong-shape'), ('candidate', 'discrepancies', [None]),
])
def test_invalid_dispositions_and_candidate_metadata_are_rejected(repository, monkeypatch, level, field, value):
    record = approve(repository)
    history = repository.history('P001')
    evidence = json.loads(json.dumps(record.evidence))
    target = evidence if level == 'property' else evidence['candidates'][0]
    target[field] = value
    content = json.dumps(evidence, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    malformed = replace(record, evidence=evidence, fingerprint=hashlib.sha256(content.encode()).hexdigest())
    event = replace(history[-1], note='Test override prevents note validation from masking this defect')
    monkeypatch.setattr(repository, 'get_review_context', lambda _: (malformed, (*history[:-1], event)))
    with pytest.raises(PayloadError, match='disposition|discrepancies'):
        generate_payload(repository, 'P001')


@pytest.mark.parametrize('blank', ['', '   ', '\t\n'])
def test_blank_optional_values_export_null_and_preserve_originals(repository, blank):
    dataset = load_dataset(ROOT / 'data')
    entities = tuple(replace(entity, entity_status=blank, registered_agent=blank)
                     if entity.property_id == 'P001' else entity for entity in dataset.entity_records)
    results = reconcile_dataset(replace(dataset, entity_records=entities))
    repository.save_results(results)
    approve(repository, note='Test reviewer deliberately approves despite missing status')
    fields = generate_payload(repository, 'P001')['records'][0]
    assert fields['Entity_Status__c'] is None
    assert fields['Registered_Agent__c'] is None
    source = json.loads(fields['Source_Provenance__c'])['entity_record']
    assert source['entity_status'] == blank
    assert source['registered_agent'] == blank
