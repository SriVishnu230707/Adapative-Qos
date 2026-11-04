from pathlib import Path
import subprocess

import pytest
import yaml

from adaptive_qos.config import ConfigurationError, load_config_bundle, load_runtime_config


def edit(directory, filename, change):
    path = directory / filename
    data = yaml.safe_load(path.read_text())
    change(data)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_offline_configuration_and_runtime_separation(config_dir, monkeypatch):
    bundle = load_config_bundle(config_dir)
    assert bundle.runtime.controller.targets == bundle.lab.gateway
    assert not hasattr(bundle.runtime, "lab")
    assert not hasattr(bundle.runtime, "emulator")
    original = Path.read_bytes

    def read(path):
        if path.name == "lab.yaml":
            raise AssertionError("runtime tried to read lab truth")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    assert load_runtime_config(config_dir).effective_sha256 == bundle.runtime.effective_sha256


def test_runtime_config_does_not_require_lab_file(config_dir):
    (config_dir / "lab.yaml").unlink()
    assert load_runtime_config(config_dir).controller.targets.namespace == "qos-gw"
    with pytest.raises(ConfigurationError, match="lab.yaml"):
        load_config_bundle(config_dir)


def test_effective_hash_normalizes_comments_but_keeps_source_hashes(config_dir):
    first = load_runtime_config(config_dir)
    path = config_dir / "controller.yaml"
    path.write_text("# Extra comment\n" + path.read_text(), encoding="utf-8")
    second = load_runtime_config(config_dir)
    assert first.effective_sha256 == second.effective_sha256
    assert first.source_files[0].sha256 != second.source_files[0].sha256


@pytest.mark.parametrize("change", [
    lambda d: d.update(schema_version=2),
    lambda d: d.update(schema_version=True),
    lambda d: d.update(actual_capacity_bps=20_000_000),
    lambda d: d.update(observation_interval_ms=True),
    lambda d: d.update(telemetry_max_age_ms=1000),
    lambda d: d.update(cooldown_ms=500),
    lambda d: d.update(policy_lease_ms=1000),
    lambda d: d.update(max_intent_duration_ms=0),
    lambda d: d["upload"].update(initial_bps=0),
    lambda d: d["upload"].update(initial_bps="18000000"),
    lambda d: d["download"].update(initial_bps=101_000_000),
    lambda d: d["download"].update(minimum_bps=91_000_000),
    lambda d: d["heartbeat"].update(recovery_target_ms=4999),
    lambda d: d["heartbeat"].update(interval_ms=2000),
])
def test_invalid_controller_values_are_rejected(config_dir, change):
    edit(config_dir, "controller.yaml", change)
    with pytest.raises(ConfigurationError):
        load_runtime_config(config_dir)


@pytest.mark.parametrize("change", [
    lambda d: d.update(temporary_grants_allowed=True),
    lambda d: d.update(temporary_grants_allowed=0),
    lambda d: d.update(upload_shaper_rate_bps=6_000_000),
    lambda d: d.update(upload_shaper_rate_bps=100_000),
    lambda d: d.update(shell_command="unused"),
])
def test_invalid_fallback_is_rejected(config_dir, change):
    edit(config_dir, "safe-policy.yaml", change)
    with pytest.raises(ConfigurationError):
        load_runtime_config(config_dir)


def test_offline_validator_checks_target_bindings(config_dir):
    edit(config_dir, "controller.yaml", lambda d: d["targets"].update(upload_interface="other0"))
    with pytest.raises(ConfigurationError, match="gateway bindings"):
        load_config_bundle(config_dir)


def test_offline_validator_checks_envelope(config_dir):
    edit(config_dir, "lab.yaml", lambda d: d["emulator"].update(minimum_supported_download_rate_bps=19_000_000))
    with pytest.raises(ConfigurationError, match="fallback assumptions"):
        load_config_bundle(config_dir)


def test_duplicate_link_endpoint_is_rejected(config_dir):
    edit(config_dir, "lab.yaml", lambda d: d["links"][1]["a"].update(interface="lan0"))
    with pytest.raises(ConfigurationError, match="duplicate namespace/interface"):
        load_config_bundle(config_dir)


@pytest.mark.parametrize("text,fragment", [
    ("schema_version: 1\nschema_version: 1\n", "duplicate configuration key"),
    ("upload:\n  minimum_bps: 1\n  minimum_bps: 2\n", "duplicate configuration key"),
    ("a: &a [1]\nb: *a\n", "aliases are not supported"),
    ("[]", "one mapping document"),
    ("", "one mapping document"),
    ("schema_version: 1\n---\nschema_version: 1", "one mapping document"),
    ("1: value", "keys must be strings"),
    ("x: !!python/object/apply:subprocess.run [unused]", "constructor"),
])
def test_yaml_ambiguity_and_object_tags_are_rejected(config_dir, monkeypatch, text, fragment):
    def forbidden(*args, **kwargs):
        raise AssertionError("configuration executed a process")
    monkeypatch.setattr(subprocess, "run", forbidden)
    (config_dir / "controller.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ConfigurationError, match=fragment):
        load_runtime_config(config_dir)
