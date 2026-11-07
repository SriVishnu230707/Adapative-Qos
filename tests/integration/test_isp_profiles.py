"""Sustained ISP shaping tests; explicitly opt in inside the dedicated VM."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time

import pytest

from adaptive_qos.config import load_lab_config
from adaptive_qos.isp import Isp, PrivateStore, Rates
from adaptive_qos.lab import Commands, Lab, LabError
from test_routed_lab import host_state


pytestmark = pytest.mark.skipif(sys.platform != "linux" or os.environ.get("AQOS_RUN_ISP_INTEGRATION") != "1"
                              or getattr(os, "geteuid", lambda: -1)() != 0,
                              reason="opt-in root ISP integration: AQOS_RUN_ISP_INTEGRATION=1")
ROOT = Path(__file__).resolve().parents[2]
REPORTS = ROOT / "work/step05/integration"
BASE = Rates(download_bps=100_000_000, upload_bps=20_000_000)
LOW = Rates(download_bps=20_000_000, upload_bps=5_000_000)


def save(name, data):
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{name}.json"
    pending = path.with_suffix(".tmp")
    pending.write_text(json.dumps(data, indent=2) + "\n")
    pending.replace(path)


def steady_bps(report):
    windows = report["window_bytes"][2:-1]
    assert len(windows) >= 2
    return sum(windows) * 8 / len(windows)


def measure(client, family, direction, seconds=6, *, port=5310, offer_bps):
    lab = Lab()
    sender, receiver = ("qos-server", client) if direction == "download" else (client, "qos-server")
    address = lab.endpoint_ip(lab.state(), receiver, family)
    started = time.monotonic_ns()
    start_ns = started + 2_000_000_000
    c = Commands()
    args = [address, str(port), str(start_ns), str(seconds), str(offer_bps)]
    helper = str(Path(__file__).with_name("udp_load.py"))
    listener = subprocess.Popen(["ip", "netns", "exec", receiver, sys.executable, helper, "receive", *args],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready, _, _ = select.select([listener.stdout], [], [], 5)
        assert ready and listener.stdout.readline().strip() == "ready", "UDP listener did not become ready"
        sender_report = json.loads(c.inside(sender, sys.executable, helper, "send", *args, timeout=seconds+12))
        out, err = listener.communicate(timeout=10)
        finished = time.monotonic_ns()
        assert listener.returncode == 0, err
        receiver_report = json.loads(out)
        return {"client": client, "family": family, "direction": direction,
                "requested_offer_bps": offer_bps, "steady_sender_bps": steady_bps(sender_report),
                "steady_receiver_bps": steady_bps(receiver_report), "started_ns": started, "finished_ns": finished,
                "sender": sender_report, "receiver": receiver_report}
    finally:
        if listener.poll() is None:
            listener.terminate()
            try:
                listener.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                listener.kill()
                listener.communicate(timeout=3)


@pytest.fixture(scope="module")
def environment():
    lab = Lab()
    before = host_state()
    with lab.store.locked():
        if lab.store.load():
            lab.down()
        config, digest = load_lab_config(ROOT / "configs/lab-multi.yaml")
        lab.up(config, digest)
    manager = Isp(lab=lab)
    try:
        yield manager
    finally:
        with lab.store.locked():
            lab.down()
        assert host_state() == before
        save("host-preservation", {"status": "pass", "before": before, "after": host_state()})


@pytest.fixture
def isp(environment):
    m = environment
    if m.store.load() is None:
        m.install()
    m.restore()
    try:
        yield m
    finally:
        if m.store.load():
            m.cancel()
            m.restore()


def test_unshaped_headroom(isp):
    isp.remove()
    results = []
    for direction in ("download", "upload"):
        capacity = getattr(BASE, f"{direction}_bps")
        result = measure("qos-client", 4, direction, offer_bps=capacity * 3 // 2)
        result["required_bps"] = capacity * 6 // 5
        results.append(result)
    save("unshaped-headroom", {"results": results})
    for result in results:
        assert result["steady_receiver_bps"] >= result["required_bps"], result


def test_static_capacity_both_directions_and_families(isp):
    results = []
    for rates in (BASE, LOW):
        isp.apply(rates)
        for family in (4, 6):
            for direction in ("download", "upload"):
                expected = getattr(rates, f"{direction}_bps")
                binding = next(b for b in isp.store.load().bindings if b.direction == direction)
                before = isp.backend.qdiscs(binding)
                r = measure("qos-client", family, direction, offer_bps=expected * 3 // 2)
                after = isp.backend.qdiscs(binding)
                before_leaf = next(q for q in before if q["kind"] == "pfifo")
                after_leaf = next(q for q in after if q["kind"] == "pfifo")
                assert after_leaf["bytes"] > before_leaf["bytes"]
                r["qdiscs_before"], r["qdiscs_after"] = before, after
                r["expected_bps"] = expected
                results.append(r)
                save("static-capacities-progress", {"results": results})
                assert_sustained_capacity(r, expected)
    save("static-capacities", {"results": results, "lower_ratio": 0.75, "upper_ratio": 1.15,
                               "warmup_excluded_seconds": 2, "measurement_scope": "endpoint useful-payload delivery"})


def assert_sustained_capacity(result, expected):
    assert result["steady_sender_bps"] >= 1.05*expected, "offered load did not saturate the configured bottleneck"
    actual = result["steady_receiver_bps"]
    assert 0.75*expected <= actual <= 1.15*expected, (result["family"], result["direction"], expected, actual)


def start_worker(isp, name, events, duration=36000):
    path = isp.store.directory / f"test-{name}.json"
    scenario = {"schema_version": 1, "scenario_id": name, "duration_ms": duration, "events": events}
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as target:
        target.write(json.dumps(scenario))
    process = subprocess.Popen([sys.executable, "-m", "adaptive_qos.isp", "run", "--scenario", str(path)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic()+10
    while time.monotonic()<deadline:
        state = isp.store.load()
        if state.worker:
            return process, state.worker.worker_id
        assert process.poll() is None, process.communicate()
        time.sleep(0.05)
    raise AssertionError("scenario worker did not register")


def wait_event(isp, worker_id, offset):
    deadline = time.monotonic()+25
    while time.monotonic()<deadline:
        with isp.store.locked():
            state = isp.store.load()
            matches = [e for e in isp.store.records(state)["events"] if e.get("worker_id") == worker_id
                       and e.get("kind") == "applied" and e.get("requested_offset_ms") == offset]
        if matches:
            return matches[-1]
        time.sleep(0.05)
    raise AssertionError("scheduled event was not verified")


@pytest.mark.parametrize("direction", ["download", "upload"])
def test_timed_directional_drop_and_recovery(isp, direction):
    reduced = Rates(download_bps=20_000_000, upload_bps=20_000_000) if direction == "download" else Rates(download_bps=100_000_000, upload_bps=5_000_000)
    events = [{"at_ms": at, "rates": rates.model_dump()} for at, rates in ((0, BASE), (12000, reduced), (24000, BASE))]
    process, worker_id = start_worker(isp, f"timed-{direction}", events)
    results = []
    try:
        for at, rates in ((0, BASE), (12000, reduced), (24000, BASE)):
            expected = getattr(rates, f"{direction}_bps")
            # A phase-4 verification holds this topology lease during traffic.
            # The ISP worker must still be able to apply its timed event.
            with isp.lab.store.locked():
                event = wait_event(isp, worker_id, at)
                r = measure("qos-client", 4, direction, offer_bps=expected*6//5)
            r.update(expected_bps=expected, event_offset_ms=at, event_verified_ns=event["finished_ns"])
            results.append(r)
            save(f"timed-{direction}-progress", {"results": results})
            assert_sustained_capacity(r, expected)
        out, err = process.communicate(timeout=20)
        assert process.returncode == 0, err
        report = isp.report()
        applied = [e for e in report["events"] if e.get("worker_id") == worker_id and e.get("requested_offset_ms") is not None]
        assert [e["requested_offset_ms"] for e in applied] == [0, 12000, 24000]
        for i, result in enumerate(results):
            assert result["started_ns"] >= applied[i]["finished_ns"]
            if i+1 < len(applied):
                assert result["finished_ns"] <= applied[i+1]["started_ns"], "measurement crossed a capacity transition"
        assert isp.inspect()["readback"] == BASE.model_dump()
        save(f"timed-{direction}", {"results": results, "ground_truth": report})
    finally:
        if process.poll() is None:
            isp.cancel()
            process.communicate(timeout=10)


def test_mixed_family_shared_bottleneck_and_low_demand(isp):
    isp.apply(LOW)
    jobs = [(name, 4 if i%2 == 0 else 6, 5320+i) for i, name in enumerate(isp.lab.state().config.verification.clients)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda job: measure(job[0], job[1], "download", port=job[2], offer_bps=7_000_000), jobs))
    aggregate = sum(r["steady_receiver_bps"] for r in results)
    assert sum(r["steady_sender_bps"] for r in results) > 20_000_000
    assert 0.75*20_000_000 <= aggregate <= 1.15*20_000_000
    assert all(r["steady_receiver_bps"] > 0 for r in results)
    low = measure("qos-client", 6, "upload", offer_bps=1_000_000)
    assert 0.7*1_000_000 <= low["steady_receiver_bps"] <= 1.3*1_000_000
    assert isp.inspect()["readback"] == LOW.model_dump()
    save("shared-and-low-demand", {"clients": results, "aggregate_bps": aggregate, "low_demand": low})


def test_foreign_tree_refusal_and_partial_update_rollback(isp, monkeypatch):
    isp.remove()
    c = Commands()
    c.inside("qos-isp", "tc", "qdisc", "add", "dev", "srv0", "root", "fq_codel")
    try:
        with pytest.raises(LabError, match="foreign"):
            isp.install()
        assert "noqueue" in c.inside("qos-isp", "tc", "qdisc", "show", "dev", "gw0")
    finally:
        c.inside("qos-isp", "tc", "qdisc", "delete", "dev", "srv0", "root")
    isp.install()
    original = isp.backend.change
    failed = False
    def fail_once(binding, rate, burst):
        nonlocal failed
        if binding.direction == "upload" and not failed:
            failed = True
            raise LabError("injected second-direction failure")
        return original(binding, rate, burst)
    monkeypatch.setattr(isp.backend, "change", fail_once)
    with pytest.raises(LabError, match="injected"):
        isp.apply(LOW)
    assert isp.inspect()["readback"] == BASE.model_dump()
    save("partial-update", isp.report())


def test_private_trace_cancel_and_dead_worker_recovery(isp):
    events = [{"at_ms": 0, "rates": LOW.model_dump()}, {"at_ms": 3000, "rates": BASE.model_dump()}]
    process, worker_id = start_worker(isp, "cancel-test", events, 6000)
    wait_event(isp, worker_id, 0)
    protected = isp.store.directory / "runs" / isp.store.load().install_id / "scenario.json"
    probe = subprocess.run(["runuser", "-u", "nobody", "--", "/usr/bin/python3", "-c",
                            "from pathlib import Path; import sys; Path(sys.argv[1]).read_text()", str(protected)], capture_output=True, text=True)
    assert probe.returncode != 0 and "PermissionError" in probe.stderr
    isp.cancel()
    out, err = process.communicate(timeout=10)
    assert process.returncode == 0 and json.loads(out)["status"] == "cancelled", err
    time.sleep(3.2)
    assert not any(e.get("worker_id") == worker_id and e.get("requested_offset_ms") == 3000 for e in isp.report()["events"])
    process, worker_id = start_worker(isp, "crash-test", [{"at_ms": 0, "rates": LOW.model_dump()}], 30000)
    wait_event(isp, worker_id, 0)
    process.kill()
    process.communicate(timeout=5)
    assert isp.restore()["status"] == "restored"
    assert isp.inspect()["readback"] == BASE.model_dump()
    save("cancel-and-crash", {"runtime_identity_denied": True, "ground_truth": isp.report()})


def test_lab_cleanup_cancels_worker_before_namespace_removal(isp):
    process, worker_id = start_worker(isp, "cleanup-test", [{"at_ms": 0, "rates": LOW.model_dump()}], 30000)
    wait_event(isp, worker_id, 0)
    with isp.lab.store.locked():
        isp.lab.down()
    out, err = process.communicate(timeout=10)
    assert process.returncode == 0 and json.loads(out)["status"] == "cancelled", err
    assert isp.store.load() is None
    config, digest = load_lab_config(ROOT / "configs/lab-multi.yaml")
    with isp.lab.store.locked():
        isp.lab.up(config, digest)
    save("cleanup-worker", {"status": "pass", "worker_stopped": True, "reset_ready": True})
