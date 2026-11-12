"""Opt-in kernel checks for the static CAKE actuator and its failure recovery."""

import json
import os
from pathlib import Path
import sys

import pytest

from adaptive_qos.actuator import Actuator
from adaptive_qos.config import StaticProfile, load_lab_config, load_runtime_config, load_static_profile
from adaptive_qos.lab import Commands, Lab, LabError
from test_routed_lab import host_state


pytestmark = pytest.mark.skipif(
    sys.platform != "linux" or os.environ.get("AQOS_RUN_STATIC_INTEGRATION") != "1"
    or getattr(os, "geteuid", lambda: -1)() != 0,
    reason="opt-in root static QoS integration: AQOS_RUN_STATIC_INTEGRATION=1",
)
CONFIG = Path(__file__).resolve().parents[2] / "configs"


@pytest.fixture
def lab():
    manager = Lab()
    before = host_state()
    root_queues = manager.commands.run(["tc", "-j", "qdisc", "show"])
    with manager.store.locked():
        assert manager.store.load() is None, "clean up the existing project lab first"
        manager.up(*load_lab_config(CONFIG / "lab-multi.yaml"))
        try:
            manager.commands.inside("qos-gw", "nft", "add", "table", "inet", "aqos_unrelated_test")
            yield manager
            gateway_tables = json.loads(manager.commands.inside("qos-gw", "nft", "-j", "list", "tables"))
            assert any(row.get("table", {}).get("name") == "aqos_unrelated_test" for row in gateway_tables["nftables"])
        finally:
            manager.down()
    assert host_state() == before
    assert manager.commands.run(["tc", "-j", "qdisc", "show"]) == root_queues


def test_reject_out_of_bounds_rate_before_mutating(lab):
    profile = StaticProfile(schema_version=1, mode="besteffort", upload_bps=80_000, download_bps=18_000_000)
    actuator = Actuator(lab)
    state = lab.state()
    before = lab.commands.inside("qos-gw", "nft", "-j", "list", "ruleset")
    with pytest.raises(LabError, match="upload rate is outside configured bounds"):
        actuator.apply(state, profile, load_runtime_config(CONFIG))
    assert lab.state() == state
    assert actuator.inspect(state) == {"status": "absent"}
    assert lab.commands.inside("qos-gw", "nft", "-j", "list", "ruleset") == before


def test_kernel_readback_best_effort_and_dual_stack_marking(lab):
    actuator = Actuator(lab)
    runtime = load_runtime_config(CONFIG)
    plain = StaticProfile(schema_version=1, mode="besteffort", upload_bps=4_500_000, download_bps=18_000_000)
    actuator.apply(lab.state(), plain, runtime)
    active = actuator.inspect(lab.state())
    assert active["marking"] is None
    assert {direction: row["dev"] for direction, row in active["queues"].items()} == {"upload": "wan0", "download": "lan0"}
    assert all(len(row["tins"]) == 1 for row in active["queues"].values())
    assert actuator.restore(lab.state()) == {"status": "restored"}

    profile = load_static_profile(CONFIG / "static-qos.yaml")
    actuator.apply(lab.state(), profile, runtime)
    state = lab.state()
    before = actuator.inspect(state)
    for family in (4, 6):
        address = lab.endpoint_ip(state, "qos-server", family)
        lab.commands.inside("qos-client", "ping", f"-{family}", "-Q", "0xb8", "-c", "2", "-W", "2", address)
    after = actuator.inspect(state)
    for direction in ("upload", "download"):
        for family in (4, 6):
            label = f"{direction}:{family}:best_effort:default"
            assert after["marking"]["counters"][label]["packets"] - before["marking"]["counters"][label]["packets"] >= 2
        # The queue also carries locally generated traffic outside the forward hook.
        assert after["queues"][direction]["tins"][1]["sent_packets"] - before["queues"][direction]["tins"][1]["sent_packets"] >= 4
    assert actuator.restore(lab.state()) == {"status": "restored"}
    assert actuator.restore(lab.state()) == {"status": "absent"}


def test_restore_after_second_kernel_install_reports_failure(lab):
    class FailAfterSecondInstall(Commands):
        def inside(self, namespace, *args, **kwargs):
            result = super().inside(namespace, *args, **kwargs)
            if namespace == "qos-gw" and args[:6] == ("tc", "qdisc", "add", "dev", "lan0", "root"):
                raise LabError("simulated failed acknowledgement after the second kernel install")
            return result

    lab.commands = FailAfterSecondInstall()
    actuator = Actuator(lab)
    original = lab.state()
    with pytest.raises(LabError, match="simulated failed acknowledgement"):
        actuator.apply(original, load_static_profile(CONFIG / "static-qos.yaml"), load_runtime_config(CONFIG))
    assert lab.state() == original
    assert actuator.inspect(lab.state()) == {"status": "absent"}
