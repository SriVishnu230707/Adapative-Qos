from copy import deepcopy
from contextlib import nullcontext
import json
from pathlib import Path

import pytest

from adaptive_qos.config import ConfigurationError, load_lab_config
from adaptive_qos.lab import Lab, LabError, LabState, OwnedNamespace, OwnedProcess, main, validate_runnable


ROOT = Path(__file__).resolve().parents[2]


class MemoryStore:
    def __init__(self):
        self.value = None
        self.pins = {}

    def load(self):
        return self.value

    def save(self, value):
        self.value = value

    def clear(self):
        self.value = None

    def pin(self, owned, commands):
        self.pins[owned.name] = owned

    def verify_pin(self, owned):
        assert self.pins[owned.name] == owned

    def unpin(self, owned, commands):
        del self.pins[owned.name]


class FakeCommands:
    def __init__(self, names=(), fail=None):
        self.names = set(names)
        self.calls = []
        self.fail = fail
        self.pids = {}

    def run(self, args):
        self.calls.append(args)
        if self.fail and self.fail(args):
            raise LabError("injected command failure")
        if args == ["ip", "netns", "list"]:
            return "\n".join(sorted(self.names))
        if args[:3] == ["ip", "netns", "add"]:
            self.names.add(args[3])
        if args[:3] == ["ip", "netns", "delete"]:
            self.names.remove(args[3])
        if args[:3] == ["ip", "netns", "pids"]:
            return self.pids.get(args[3], "")
        return ""

    def ip(self, namespace, *args):
        return self.run(["ip", "-n", namespace, *args])

    def inside(self, namespace, *args):
        return self.run(["ip", "netns", "exec", namespace, *args])


def fake_lab(fail=None):
    config, digest = load_lab_config(ROOT / "configs/lab.yaml")
    commands, store = FakeCommands(fail=fail), MemoryStore()
    identities = {name: OwnedNamespace(name=name, device=1, inode=i + 100) for i, name in enumerate(config.namespaces)}

    def identity(name):
        if name not in commands.names:
            raise FileNotFoundError(name)
        return identities[name]

    lab = Lab(store, commands, identity=identity, boot_id="test-boot", hold_namespace=lambda name: nullcontext(), before_down=lambda lab: None)
    return lab, config, digest, identities


def seed(lab, config, digest, identities):
    lab.commands.names.update(config.namespaces)
    lab.store.pins.update(identities)
    lab.store.save(LabState(run_id="test-run", boot_id="test-boot", phase="ready", config=config,
                            config_sha256=digest, owned=tuple(identities.values())))


@pytest.mark.parametrize("filename,count,clients", [("lab.yaml", 4, 1), ("lab-multi.yaml", 8, 4)])
def test_declared_topologies_are_runnable(filename, count, clients):
    config, _ = load_lab_config(ROOT / "configs" / filename)
    validate_runnable(config)
    assert len(config.namespaces) == count
    assert len(config.verification.clients) == clients


def test_collision_never_claims_existing_namespace():
    lab, config, digest, _ = fake_lab()
    lab.commands.names.add("qos-client")
    with pytest.raises(LabError, match="collision"):
        lab.up(config, digest)
    assert lab.store.load() is None
    assert lab.commands.calls == [["ip", "netns", "list"]]


def test_partial_setup_cleans_only_successfully_created_namespaces():
    lab, config, digest, _ = fake_lab(fail=lambda args: args == ["ip", "netns", "add", "qos-gw"])
    lab.commands.names.add("unrelated")
    with pytest.raises(LabError, match="injected"):
        lab.up(config, digest)
    assert lab.commands.names == {"unrelated"}
    assert lab.store.load() is None
    assert [a for a in lab.commands.calls if a[:3] == ["ip", "netns", "delete"]] == [["ip", "netns", "delete", "qos-client"]]


def test_cleanup_attempts_other_owned_namespaces_and_preserves_failed_journal():
    lab, config, digest, identities = fake_lab(fail=lambda args: args == ["ip", "netns", "delete", "qos-server"])
    seed(lab, config, digest, identities)
    with pytest.raises(LabError, match="injected"):
        lab.down()
    assert lab.commands.names == {"qos-server"}
    assert lab.store.load().phase == "cleanup_failed"
    lab.commands.fail = None
    assert lab.down()["status"] == "absent"
    assert lab.down() == {"status": "absent", "removed": []}


def test_replaced_namespace_prevents_all_deletion():
    lab, config, digest, identities = fake_lab()
    seed(lab, config, digest, identities)
    identities["qos-client"] = OwnedNamespace(name="qos-client", device=1, inode=999)
    with pytest.raises(LabError, match="identity changed"):
        lab.down()
    assert not any(a[:3] == ["ip", "netns", "delete"] for a in lab.commands.calls)


def test_untracked_process_is_not_killed_or_namespace_deleted():
    lab, config, digest, identities = fake_lab()
    seed(lab, config, digest, identities)
    lab.commands.pids["qos-client"] = "1234"
    with pytest.raises(LabError, match="untracked processes"):
        lab.down()
    assert lab.commands.names == set(config.namespaces)


def test_rerun_requires_explicit_cleanup():
    lab, config, digest, identities = fake_lab()
    seed(lab, config, digest, identities)
    with pytest.raises(LabError, match="already exists"):
        lab.up(config, digest)
    assert lab.commands.calls == []


def test_old_boot_cannot_authorize_namespace_deletion():
    lab, config, digest, identities = fake_lab()
    seed(lab, config, digest, identities)
    lab.boot_id = "new-boot"
    with pytest.raises(LabError, match="another boot"):
        lab.down()
    assert lab.commands.calls == []


def test_reused_process_id_is_never_signalled(monkeypatch):
    lab, config, digest, identities = fake_lab()
    seed(lab, config, digest, identities)
    process = OwnedProcess(pid=1234, start_ticks=100, namespace="qos-server")
    lab.store.save(lab.store.load().model_copy(update={"processes": (process,)}))
    monkeypatch.setattr("adaptive_qos.lab.process_start_ticks", lambda pid: 101)
    def forbidden(*args):
        raise AssertionError("attempted to signal a reused PID")
    monkeypatch.setattr("adaptive_qos.lab.os.kill", forbidden)
    with pytest.raises(LabError, match="PID was reused"):
        lab.down()
    assert lab.commands.names == set(config.namespaces)


def test_linux_root_gate_runs_before_any_lab_creation(monkeypatch):
    monkeypatch.setattr("adaptive_qos.lab.platform.system", lambda: "Windows")
    with pytest.raises(SystemExit) as result:
        main(["up"])
    assert result.value.code == 2


@pytest.mark.parametrize("change,reason", [
    (lambda d: d["routes"][0].update(address_family=True), "must be an integer"),
    (lambda d: d["routes"][0].update(via="10.99.0.1"), "local subnet"),
    (lambda d: d["routes"][0].update(via="10.10.0.2"), "another address"),
    (lambda d: d["routes"][0].update(via="fd42::1"), "match address family"),
    (lambda d: d["routes"].append(deepcopy(d["routes"][0])), "unique destination"),
    (lambda d: d.update(routers=["qos-gw", "missing"]), "declared namespaces"),
    (lambda d: d["verification"].update(server="qos-client"), "distinct declared endpoints"),
])
def test_invalid_route_roles_are_rejected(tmp_path, change, reason):
    config, _ = load_lab_config(ROOT / "configs/lab.yaml")
    data = config.model_dump(mode="json")
    change(data)
    path = tmp_path / "lab.yaml"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigurationError, match=reason):
        load_lab_config(path)


@pytest.mark.parametrize("change,reason", [
    (lambda d: d["bridges"][0]["ports"].append("p-client"), "unique declared endpoints"),
    (lambda d: d["bridges"][0]["ports"].append("missing"), "unique declared endpoints"),
    (lambda d: d["links"][0]["b"].update(ipv4_cidr="10.10.0.9/24"), "unnumbered"),
    (lambda d: d["links"][1]["a"].update(ipv4_cidr="10.10.0.2/24"), "duplicate lab IP"),
])
def test_invalid_bridge_topology_is_rejected(tmp_path, change, reason):
    config, _ = load_lab_config(ROOT / "configs/lab-multi.yaml")
    data = config.model_dump(mode="json")
    change(data)
    path = tmp_path / "lab.yaml"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ConfigurationError, match=reason):
        load_lab_config(path)
