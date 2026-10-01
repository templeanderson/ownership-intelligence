import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from net_lease_ownership.review_cli import main


ROOT = Path(__file__).resolve().parents[1]


def invoke(monkeypatch, capsys, db, *arguments):
    monkeypatch.setattr(sys, "argv", ["review", "--db", str(db), *arguments])
    main()
    return json.loads(capsys.readouterr().out)


def test_cli_load_show_review_history_and_reload(tmp_path, monkeypatch, capsys):
    db = tmp_path / "ownership.sqlite3"
    load = ["load", "--data-dir", str(ROOT / "data"), "--config", str(ROOT / "config/matching.toml")]
    assert invoke(monkeypatch, capsys, db, *load)["loaded"] == 15
    shown = invoke(monkeypatch, capsys, db, "show", "P013")
    assert shown["review_status"] == "unreviewed"
    assert shown["evidence"]["disposition"] == "needs_research"
    reviewed = invoke(monkeypatch, capsys, db, "decide", "P013", "--action", "approved",
                      "--reviewer", "Example Reviewer", "--snapshot-id", str(shown["snapshot_id"]),
                      "--revision", str(shown["revision"]), "--candidate", "1", "--note", "Checked second candidate")
    assert reviewed["review_status"] == "approved"
    assert reviewed["selected_candidate"] == 1
    history = invoke(monkeypatch, capsys, db, "history", "P013")
    assert [event["new_state"] for event in history] == ["unreviewed", "approved"]
    assert history[-1]["note"] == "Checked second candidate"
    invoke(monkeypatch, capsys, db, *load)
    assert invoke(monkeypatch, capsys, db, "show", "P013") == reviewed
    queue = invoke(monkeypatch, capsys, db, "list")
    assert len(queue) == 15
    assert sum(row["review_status"] == "approved" for row in queue) == 1


def test_cli_refuses_missing_database_instead_of_creating_empty_one(tmp_path, monkeypatch, capsys):
    db = tmp_path / "missing.sqlite3"
    monkeypatch.setattr(sys, "argv", ["review", "--db", str(db), "list"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "load evidence first" in capsys.readouterr().err
    assert not db.exists()


@pytest.mark.parametrize("target", ["properties.csv", "county_records.csv", "entity_records.csv", "config"])
@pytest.mark.parametrize("alias", ["direct", "symlink", "hardlink"])
def test_cli_database_cannot_overwrite_source_files(tmp_path, monkeypatch, capsys, target, alias):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    config = tmp_path / "matching.toml"
    shutil.copyfile(ROOT / "config/matching.toml", config)
    protected = config if target == "config" else data / target
    before = protected.read_bytes()
    db = protected
    if alias != "direct":
        db = tmp_path / "alias.sqlite3"
        if alias == "symlink":
            db.symlink_to(protected)
        else:
            db.hardlink_to(protected)
    monkeypatch.setattr(sys, "argv", ["review", "--db", str(db), "load", "--data-dir", str(data),
                                     "--config", str(config)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert "Database path must not overwrite" in capsys.readouterr().err
    assert protected.read_bytes() == before


def test_module_entry_point_works_in_separate_processes(tmp_path):
    db = tmp_path / "ownership.sqlite3"
    command = [sys.executable, "-m", "net_lease_ownership.review_cli", "--db", str(db)]
    environment = os.environ | {"PYTHONPATH": str(ROOT / "src")}
    loaded = subprocess.run([*command, "load"], cwd=ROOT, env=environment,
                            capture_output=True, text=True, check=True)
    assert json.loads(loaded.stdout)["loaded"] == 15
    shown = subprocess.run([*command, "show", "P001"], cwd=ROOT, env=environment,
                           capture_output=True, text=True, check=True)
    assert json.loads(shown.stdout)["review_status"] == "unreviewed"
