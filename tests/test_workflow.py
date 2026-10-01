"""Exercise the documented workflow across real CLI process boundaries."""

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def test_report_review_export_and_revocation_end_to_end(tmp_path):
    database = tmp_path / 'demo.sqlite3'
    report = tmp_path / 'matching.json'
    environment = os.environ | {'PYTHONPATH': str(ROOT / 'src')}

    def run(module, *arguments, expected_code=0):
        result = subprocess.run([sys.executable, '-m', module, *map(str, arguments)],
            cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=30)
        assert result.returncode == expected_code, result.stderr
        return result

    def review(*arguments, expected_code=0):
        return run('net_lease_ownership.review_cli', '--db', database,
                   *arguments, expected_code=expected_code)

    run('net_lease_ownership', '--data-dir', ROOT / 'data',
        '--config', ROOT / 'config/matching.toml', '--output', report)
    analysis = json.loads(report.read_text())
    assert analysis['summary'] == {'ready_for_review': 6, 'needs_research': 8, 'conflict': 1}
    assert all(record['review_status'] == 'unreviewed' for record in analysis['results'])
    review('load', '--data-dir', ROOT / 'data', '--config', ROOT / 'config/matching.toml')
    shown = json.loads(review('show', 'P013').stdout)
    assert shown['review_status'] == 'unreviewed'
    assert len(shown['evidence']['candidates']) == 2
    blocked = review('export', 'P013', expected_code=2)
    assert blocked.stdout == ''
    assert 'Only a currently approved' in blocked.stderr

    decision = json.loads(review('decide', 'P013', '--action', 'approved',
        '--reviewer', 'Demo Reviewer', '--snapshot-id', shown['snapshot_id'],
        '--revision', shown['revision'], '--candidate', 1,
        '--note', 'Verified the second company independently.').stdout)
    assert decision['selected_candidate'] == 1
    proposal = json.loads(review('export', 'P013').stdout)
    assert proposal['dry_run'] is True
    assert proposal['operation'] == 'upsert'
    assert proposal['external_id_field'] == 'External_Property_ID__c'
    assert len(proposal['records']) == 1
    fields = proposal['records'][0]
    chosen = shown['evidence']['candidates'][1]
    assert fields['Ownership_Entity__c'] == chosen['entity_record']['entity_name']
    assert fields['Ownership_Entity__c'] != shown['evidence']['candidates'][0]['entity_record']['entity_name']
    assert fields['Ownership_Confidence__c'] == chosen['confidence_score']
    provenance = json.loads(fields['Source_Provenance__c'])
    assert provenance['selected_candidate'] == 1
    assert provenance['reviewer_name'] == 'Demo Reviewer'
    assert provenance['revision'] == decision['revision']
    assert provenance['entity_record'] == chosen['entity_record']
    assert json.loads(review('show', 'P013').stdout) == decision

    review('decide', 'P013', '--action', 'rejected', '--reviewer', 'Demo Reviewer',
        '--snapshot-id', decision['snapshot_id'], '--revision', decision['revision'],
        '--note', 'New information contradicts ownership.')
    blocked = review('export', 'P013', expected_code=2)
    assert blocked.stdout == ''
    assert 'Only a currently approved' in blocked.stderr
    final = json.loads(review('show', 'P013').stdout)
    assert final['selected_candidate'] is None
    events = json.loads(review('history', 'P013').stdout)
    assert [event['new_state'] for event in events] == ['unreviewed', 'approved', 'rejected']
    assert events[-1]['note'] == 'New information contradicts ownership.'
    assert final['evidence'] == shown['evidence']
    assert not (tmp_path / 'ownership.sqlite3').exists()
