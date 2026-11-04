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


@pytest.mark.parametrize("record_id,changes", [
    ("record-classification-1", {"observed_mono_ns": 1_999_999_999}),
    ("record-link-upload", {"observed_mono_ns": 1_999_999_999}),
    ("record-link-download", {"observed_mono_ns": 1_999_999_999}),
    ("record-event-1", {"observed_mono_ns": 1_999_999_999}),
])
def test_replay_rejects_effects_that_precede_their_causes(record_id, changes, records, runtime):
    altered = tuple(record.model_copy(update=changes) if record.record_id == record_id else record
                    for record in records)
    with pytest.raises(ValueError, match="precedes"):
        replay_records(altered, runtime)


@pytest.mark.parametrize("record_id,field", [
    ("record-link-upload", "input_record_ids"),
    ("record-link-download", "input_record_ids"),
    ("record-event-1", "caused_by_record_ids"),
])
def test_replay_resolves_every_declared_reference(record_id, field, records, runtime):
    altered = tuple(record.model_copy(update={field: getattr(record, field) + ("missing-record",)})
                    if record.record_id == record_id else record for record in records)
    with pytest.raises(ValueError, match="missing"):
        replay_records(altered, runtime)


@pytest.mark.parametrize("cycle", [
    {"record-link-upload": "record-link-upload"},
    {"record-link-upload": "record-link-download", "record-link-download": "record-link-upload"},
    {"record-policy-1": "record-apply-1"},
])
def test_replay_rejects_causal_cycles_even_at_equal_timestamps(cycle, records, runtime):
    altered = tuple(record.model_copy(update={"input_record_ids": record.input_record_ids + (cycle[record.record_id],)})
                    if record.record_id in cycle else record for record in records)
    with pytest.raises(ValueError, match="causal cycle"):
        replay_records(altered, runtime)


def test_replay_is_independent_of_input_order(records, runtime):
    assert replay_records(tuple(reversed(records)), runtime) == replay_records(records, runtime)
