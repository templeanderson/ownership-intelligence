import json
import shutil
from pathlib import Path
import sys

import pytest

from net_lease_ownership.__main__ import main


ROOT = Path(__file__).resolve().parents[1]


def test_analysis_report_preserves_evidence_and_never_approves(tmp_path, monkeypatch, capsys):
    output = tmp_path / "reports" / "matching.json"
    monkeypatch.setattr(sys, "argv", ["matching", "--data-dir", str(ROOT / "data"),
                       "--config", str(ROOT / "config/matching.toml"), "--output", str(output)])
    main()
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["report_type"] == "matching_analysis_only"
    assert report["matching_version"] == "2"
    assert report["summary"] == {"ready_for_review": 6, "conflict": 1, "needs_research": 8}
    assert len(report["results"]) == 15
    assert all(result["review_status"] == "unreviewed" for result in report["results"])
    first = report["results"][0]["candidates"][0]
    assert first["county_record"]["owner_name"] == "ABC Medical Holdings LLC"
    assert first["entity_record"]["entity_name"] == "ABC MEDICAL HOLDINGS, L.L.C."
    assert first["entity_record"]["source_as_of"] == "2026-09-16"
    ambiguous = next(result for result in report["results"] if result["property_id"] == "P013")
    assert len(ambiguous["candidates"]) == 2
    assert ambiguous["disposition"] == "needs_research"
    assert "no Salesforce payload" in capsys.readouterr().out
    assert "External_Property_ID__c" not in output.read_text(encoding="utf-8")


def test_invalid_policy_stops_report_generation(tmp_path, monkeypatch, capsys):
    config = tmp_path / "bad.toml"
    config.write_text("name_match_threshold = 5\n", encoding="utf-8")
    output = tmp_path / "matching.json"
    monkeypatch.setattr(sys, "argv", ["matching", "--data-dir", str(ROOT / "data"),
                       "--config", str(config), "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert not output.exists()
    assert "between 0 and 1" in capsys.readouterr().err


@pytest.fixture
def local_inputs(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    config = tmp_path / "matching.toml"
    shutil.copyfile(ROOT / "config/matching.toml", config)
    return data, config


@pytest.mark.parametrize("target", ["properties.csv", "county_records.csv", "entity_records.csv", "config"])
@pytest.mark.parametrize("alias", ["direct", "symlink", "hardlink"])
def test_report_cannot_overwrite_inputs_or_aliases(local_inputs, tmp_path, monkeypatch, capsys, target, alias):
    data, config = local_inputs
    protected = config if target == "config" else data / target
    before = protected.read_bytes()
    output = protected
    if alias == "symlink":
        output = tmp_path / "alias.json"
        output.symlink_to(protected)
    elif alias == "hardlink":
        output = tmp_path / "alias.json"
        output.hardlink_to(protected)
    monkeypatch.setattr(sys, "argv", ["matching", "--data-dir", str(data), "--config", str(config),
                                     "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert protected.read_bytes() == before
    assert "must not overwrite input or configuration" in capsys.readouterr().err


def test_failed_report_replacement_preserves_existing_report(local_inputs, tmp_path, monkeypatch, capsys):
    data, config = local_inputs
    output = tmp_path / "report.json"
    original = '{"previous_report": true}\n'
    output.write_text(original, encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["matching", "--data-dir", str(data), "--config", str(config),
                                     "--output", str(output)])

    def fail_replace(*args, **kwargs):
        raise OSError("Simulated replacement failure")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert output.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob(".matching-*.tmp"))
    assert "Cannot write matching report" in capsys.readouterr().err
