import json
from pathlib import Path
import socket
import subprocess

import pytest

from adaptive_qos.cli import main
from adaptive_qos.contracts import check_contracts, replay_records

ROOT = Path(__file__).resolve().parents[2]


def test_contract_chain_and_negative_fixtures(runtime):
    result = check_contracts(ROOT / "examples/contracts", runtime)
    assert result["valid_records"] == 9
    assert len(result["record_types"]) == 8
    assert result["invalid_records_rejected"] == 16
    assert result["replay"]["estimated_capacity_bps"] == {"upload": None, "download": None}
    assert result["replay"]["predicted_category"] == "bulk_like"
    assert result["replay"]["corrected_category"] == "interactive_like"
    assert result["replay"]["intent_expired"] and result["replay"]["observation_stale"]
    assert result["replay"]["execution"] == "simulated"


def test_validation_cli_never_starts_processes_or_networks(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("contract validation attempted external execution")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(socket, "socket", forbidden)
    assert main(["validate-config", "--config-dir", str(ROOT / "configs")]) == 0
    assert json.loads(capsys.readouterr().out)["network_changes"] is False
    assert main(["check-contracts", "--examples", str(ROOT / "examples/contracts"), "--config-dir", str(ROOT / "configs")]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "pass"


def test_cli_bad_path_is_nonzero(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["validate-config", "--config-dir", str(tmp_path / "missing")])
    assert exit_info.value.code == 2
    assert "controller.yaml" in capsys.readouterr().err


def test_cli_exports_all_eight_schema_tags(capsys):
    assert main(["schema"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert len(schema["discriminator"]["mapping"]) == 8


def test_replay_rejects_duplicate_records(records, runtime):
    with pytest.raises(ValueError, match="duplicate record ID"):
        replay_records(records + (records[0],), runtime)
