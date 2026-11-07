"""Opt-in real networking checks; run only in the dedicated Ubuntu VM as root."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest

from adaptive_qos.config import load_lab_config
from adaptive_qos.lab import Commands, Lab, LabError, StateStore, namespace_identity


pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("AQOS_RUN_LAB_INTEGRATION") != "1" or getattr(os, "geteuid", lambda: -1)() != 0,
    reason="opt-in root integration in the dedicated Ubuntu VM: AQOS_RUN_LAB_INTEGRATION=1",
)
ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "work/step04/integration"


def invoke(command, *args, ok=True):
    result = subprocess.run([sys.executable, "-m", "adaptive_qos.lab", command, *map(str, args)],
                            capture_output=True, text=True, timeout=120)
    if ok:
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    assert result.returncode != 0, result.stdout
    return result.stderr


def host_state():
    c = Commands()
    links = json.loads(c.run(["ip", "-j", "link", "show"]))
    addresses = json.loads(c.run(["ip", "-j", "address", "show"]))
    result = {
        "links": [(r["ifindex"], r["ifname"], r.get("address"), r["flags"]) for r in links],
        "addresses": [(r["ifname"], a["family"], a["local"], a["prefixlen"]) for r in addresses for a in r.get("addr_info", [])],
        "forwarding": c.run(["sysctl", "-n", "net.ipv4.ip_forward", "net.ipv6.conf.all.forwarding"]),
        "firewall": json.loads(c.run(["nft", "-j", "list", "ruleset"])),
    }
    # nft metadata is tool-version information, not mutable firewall state.
    result["firewall"]["nftables"] = [r for r in result["firewall"].get("nftables", []) if "metainfo" not in r]
    for family in (4, 6):
        result[f"routes{family}"] = [{k: r[k] for k in ("dst", "gateway", "dev", "protocol", "table") if k in r}
                                     for r in json.loads(c.run(["ip", "-j", f"-{family}", "route", "show", "table", "all"]))]
    return result


@pytest.fixture(scope="module", autouse=True)
def preserve_unrelated_state():
    assert StateStore().load() is None, "clean up the existing project lab before integration testing"
    before = host_state()
    sentinel = "aq04-" + uuid.uuid4().hex[:10]
    c = Commands()
    c.run(["ip", "netns", "add", sentinel])
    identity = namespace_identity(sentinel)
    REPORTS.mkdir(parents=True, exist_ok=True)
    try:
        yield
        assert StateStore().load() is None
        assert not list((StateStore().directory / "pins").glob("*")), "namespace ownership pins leaked"
        assert namespace_identity(sentinel) == identity
        assert host_state() == before, "initial network namespace changed"
        (REPORTS / "host-preservation.json").write_text(json.dumps({"status": "pass", "before": before, "after": host_state()}, indent=2))
    finally:
        assert namespace_identity(sentinel) == identity
        c.run(["ip", "netns", "delete", sentinel])


@pytest.mark.parametrize("filename,clients", [("lab.yaml", 1), ("lab-multi.yaml", 4)])
def test_routed_delivery_and_repeatable_reset(filename, clients):
    run_ids = []
    for cycle in range(2):
        created = invoke("up", "--config", ROOT / "configs" / filename)
        run_ids.append(created["run_id"])
        try:
            assert "already exists" in invoke("up", "--config", ROOT / "configs" / filename, ok=False)
            report = invoke("verify")
            assert len(report["transfers"]) == clients * 4
            assert all(r["received_bytes"] >= 1048576 for r in report["transfers"])
            assert len(report["router_counter_deltas"]) == 4
            (REPORTS / f"{filename.removesuffix('.yaml')}-{cycle}.json").write_text(json.dumps(report, indent=2))
        finally:
            invoke("down")
        assert invoke("down")["removed"] == []
    assert len(set(run_ids)) == 2


def test_collision_preserves_preexisting_namespace():
    c = Commands()
    c.run(["ip", "netns", "add", "qos-client"])
    identity = namespace_identity("qos-client")
    try:
        assert "collision" in invoke("up", ok=False)
        assert namespace_identity("qos-client") == identity
        assert StateStore().load() is None
    finally:
        c.run(["ip", "netns", "delete", "qos-client"])


def test_real_partial_setup_failure_rolls_back():
    class FailRoute(Commands):
        def run(self, args, **kwargs):
            if "route" in args and "add" in args:
                raise LabError("injected route failure")
            return super().run(args, **kwargs)
    lab = Lab(commands=FailRoute())
    config, digest = load_lab_config(ROOT / "configs/lab.yaml")
    with lab.store.locked(), pytest.raises(LabError, match="injected route"):
        lab.up(config, digest)
    assert lab.store.load() is None
    names = Commands().run(["ip", "netns", "list"])
    assert not any(name in names for name in config.namespaces)


def test_broken_routes_and_forwarding_cannot_pass_verification():
    invoke("up")
    c = Commands()
    try:
        c.ip("qos-isp", "route", "delete", "10.10.0.0/24")
        assert "configured route missing" in invoke("verify", ok=False)
        c.ip("qos-isp", "route", "add", "10.10.0.0/24", "via", "10.20.0.1", "dev", "gw0")
        c.inside("qos-isp", "sysctl", "-qw", "net.ipv6.conf.all.forwarding=0")
        assert "forwarding differs" in invoke("verify", ok=False)
    finally:
        invoke("down")


def test_namespace_replacement_is_not_deleted():
    invoke("up")
    c = Commands()
    c.run(["ip", "netns", "delete", "qos-client"])
    c.run(["ip", "netns", "add", "qos-client"])
    replacement = namespace_identity("qos-client")
    try:
        assert "identity changed" in invoke("down", ok=False)
        assert namespace_identity("qos-client") == replacement
        assert namespace_identity("qos-gw")
    finally:
        assert namespace_identity("qos-client") == replacement
        c.run(["ip", "netns", "delete", "qos-client"])
        invoke("down")


def test_untracked_workload_is_preserved_until_stopped():
    invoke("up")
    process = subprocess.Popen(["ip", "netns", "exec", "qos-client", "sleep", "30"])
    try:
        deadline = time.monotonic() + 3
        while str(process.pid) not in Commands().run(["ip", "netns", "pids", "qos-client"]).split():
            assert time.monotonic() < deadline
            time.sleep(0.05)
        assert "untracked processes" in invoke("down", ok=False)
        assert process.poll() is None
        assert namespace_identity("qos-gw")
    finally:
        process.terminate()
        process.wait(timeout=3)
        invoke("down")
