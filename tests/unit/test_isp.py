from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaptive_qos.config import load_lab_config
from adaptive_qos.isp import Binding, Isp, Rates, Scenario, Worker
from adaptive_qos.lab import LabError, OwnedNamespace


ROOT = Path(__file__).resolve().parents[2]
BASE = Rates(download_bps=100_000_000, upload_bps=20_000_000)
LOW = Rates(download_bps=20_000_000, upload_bps=5_000_000)


class MemoryStore:
    def __init__(self):
        self.value, self.events, self.scenario = None, [], None

    def locked(self):
        return nullcontext()

    def load(self):
        return self.value

    def save(self, value):
        self.value = value

    def clear(self):
        self.value = None

    def write_record(self, state, filename, value, **kwargs):
        if filename == "events.jsonl":
            self.events.append(value)
        else:
            self.scenario = value

    def records(self, state):
        return {"events": self.events, "scenario": self.scenario}


class FakeBackend:
    def __init__(self):
        self.trees, self.calls, self.foreign, self.fail = {}, [], set(), None

    def binding(self, ns, iface, direction, run):
        return Binding(namespace=ns, interface=iface, direction=direction, ifindex=10 if direction == "download" else 11)

    def check_link(self, binding, run):
        pass

    def free(self, binding):
        if binding.direction in self.foreign:
            raise LabError("foreign queue")

    def install(self, binding, rate, limit, burst):
        self.calls.append(("install", binding.direction, rate))
        self.trees[binding.direction] = rate

    def read(self, binding, limit):
        return self.trees[binding.direction]

    def change(self, binding, rate, burst):
        self.calls.append(("change", binding.direction, rate))
        if self.fail and self.fail(binding.direction, rate):
            raise LabError("injected update failure")
        self.trees[binding.direction] = rate

    def remove(self, binding):
        self.calls.append(("remove", binding.direction))
        self.trees.pop(binding.direction, None)


def manager():
    config, _ = load_lab_config(ROOT / "configs/lab-multi.yaml")
    state = SimpleNamespace(phase="ready", config=config, run_id="lab-run", boot_id="boot-1",
                            owned=(OwnedNamespace(name="qos-isp", device=1, inode=99),))
    lab = SimpleNamespace(state=lambda: state, owned_identity=lambda owned: True, store=MemoryStore())
    store, backend = MemoryStore(), FakeBackend()
    return Isp(lab, store, backend, now=lambda: 1_000_000_000, alive=lambda worker: False)


def test_install_direction_mapping_and_in_place_rate_updates():
    m = manager()
    m.install()
    assert [(b.direction, b.interface) for b in m.store.load().bindings] == [("download", "gw0"), ("upload", "srv0")]
    m.backend.calls.clear()
    assert m.apply(LOW)["rates"] == LOW.model_dump()
    assert m.backend.calls == [("change", "download", 20_000_000), ("change", "upload", 5_000_000)]
    assert m.inspect()["readback"] == LOW.model_dump()
    assert m.restore()["rates"] == BASE.model_dump()
    assert m.remove()["status"] == "absent"
    assert m.remove()["status"] == "absent"


def test_foreign_tree_refused_before_any_mutation():
    m = manager()
    m.backend.foreign.add("upload")
    with pytest.raises(LabError, match="foreign"):
        m.install()
    assert m.backend.calls == [] and m.store.load() is None


def test_invalid_bounds_fail_before_any_change():
    m = manager()
    m.install()
    m.backend.calls.clear()
    with pytest.raises(ValueError, match="envelope"):
        m.apply(Rates(download_bps=101_000_000, upload_bps=20_000_000))
    assert m.backend.calls == []


def test_second_direction_failure_restores_previous_pair():
    m = manager()
    m.install()
    m.backend.fail = lambda direction, rate: direction == "upload" and rate == 5_000_000
    with pytest.raises(LabError, match="injected"):
        m.apply(LOW)
    assert m.inspect()["readback"] == BASE.model_dump()
    assert m.store.load().phase == "failed"
    assert m.store.events[-1]["kind"] == "apply_failed"
    assert m.store.events[-1]["rollback_errors"] == []


def test_failed_rollback_is_explicit_and_requires_restore():
    m = manager()
    m.install()
    m.backend.fail = lambda direction, rate: direction == "upload"
    with pytest.raises(LabError):
        m.apply(LOW)
    assert m.store.load().phase == "partial"
    with pytest.raises(LabError, match="restoration"):
        m.apply(BASE)
    m.backend.fail = None
    assert m.restore()["status"] == "restored"


def test_worker_lease_blocks_manual_changes_and_truth_export():
    m = manager()
    m.install()
    m.store.save(m.store.load().model_copy(update={"worker": Worker(worker_id="worker-1", pid=1234, start_ticks=4, origin_ns=0)}))
    with pytest.raises(LabError, match="scenario owns"):
        m.apply(LOW)
    with pytest.raises(LabError, match="only after"):
        m.report()
    assert m.restore()["status"] == "restored"  # A stale/reused PID is never signalled.
    assert m.store.load().worker is None
    assert any(e["kind"] == "worker_disappeared" for e in m.store.events)


def test_cancel_reports_failed_baseline_restoration(monkeypatch):
    m = manager()
    m.install()
    worker = Worker(worker_id="worker-1", pid=1234, start_ticks=4, origin_ns=0)
    m.store.save(m.store.load().model_copy(update={"worker": worker, "phase": "running"}))
    m.alive = lambda worker: True
    save = m.store.save

    def worker_fails_to_restore(state):
        if state.cancel_requested:
            state = state.model_copy(update={"worker": None, "phase": "failed", "current": LOW,
                                             "error": "baseline restoration failed"})
        save(state)

    monkeypatch.setattr(m.store, "save", worker_fails_to_restore)
    with pytest.raises(LabError, match="baseline restoration failed"):
        m.cancel()


def test_new_lab_epoch_cannot_reuse_profile():
    m = manager()
    m.install()
    m.lab.state().run_id = "replacement-run"
    with pytest.raises(LabError, match="another lab run"):
        m.apply(LOW)


@pytest.mark.parametrize("value", [True, "20000000", 20_000_000.0, 0, -1, 20_000_001])
def test_strict_rates_and_exact_kernel_granularity(value):
    with pytest.raises(ValueError):
        Rates(download_bps=value, upload_bps=5_000_000)


def scenario(events=None, **kwargs):
    data = {"schema_version": 1, "scenario_id": "test", "duration_ms": 30,
            "events": events or [{"at_ms": 0, "rates": BASE.model_dump()}, {"at_ms": 10, "rates": LOW.model_dump()}]}
    data.update(kwargs)
    import json
    return Scenario.model_validate_json(json.dumps(data), strict=True)


@pytest.mark.parametrize("events", [
    [{"at_ms": 1, "rates": BASE.model_dump()}],
    [{"at_ms": 0, "rates": BASE.model_dump()}, {"at_ms": 0, "rates": LOW.model_dump()}],
    [{"at_ms": 0, "rates": BASE.model_dump()}, {"at_ms": 30, "rates": LOW.model_dump()}],
    [{"at_ms": 0, "rates": BASE.model_dump()}, {"at_ms": 20, "rates": LOW.model_dump()}, {"at_ms": 10, "rates": BASE.model_dump()}],
])
def test_invalid_timelines_are_rejected(events):
    with pytest.raises(ValueError):
        scenario(events)


def test_optional_impairments_are_rejected_until_supported():
    with pytest.raises(ValueError, match="Extra inputs"):
        scenario(delay_ms=20)


@pytest.mark.parametrize("cancelled", [False, True])
def test_scenario_uses_absolute_deadlines_and_restores_baseline(monkeypatch, cancelled):
    m = manager()
    m.install()
    monkeypatch.setattr("adaptive_qos.isp.process_start_ticks", lambda pid: 10)
    deadlines = []
    def wait(worker, deadline):
        deadlines.append(deadline)
        return not (cancelled and len(deadlines) == 2)
    m.wait_until = wait
    result = m.run(scenario())
    assert result["status"] == ("cancelled" if cancelled else "completed")
    assert deadlines[:2] == [1_000_000_000, 1_010_000_000]
    assert m.store.load().worker is None
    assert m.inspect()["readback"] == BASE.model_dump()
    assert m.store.scenario["scenario_id"] == "test"
