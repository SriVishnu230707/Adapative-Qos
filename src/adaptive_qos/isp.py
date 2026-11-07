"""Root-only ISP emulation. Ground truth stays in a private runner journal."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import signal
import stat
import subprocess
import sys
import time
from typing import Annotated, Literal, Self
import uuid

from pydantic import Field, model_validator

from adaptive_qos.config import _load
from adaptive_qos.lab import Lab, LabError, StateStore, process_start_ticks
from adaptive_qos.models import Identifier, InterfaceName, NamespaceName, NonNegativeInt, PositiveInt, ValueModel, VersionOne


Direction = Literal["download", "upload"]
HANDLES = {"download": ("7a10:", "7a10:1", "7a11:"), "upload": ("7a20:", "7a20:1", "7a21:")}


class Rates(ValueModel):
    download_bps: PositiveInt
    upload_bps: PositiveInt

    @model_validator(mode="after")
    def byte_granularity(self) -> Self:
        if self.download_bps % 8 or self.upload_bps % 8:
            raise ValueError("ISP rates must use whole bytes/s: bit/s must be divisible by 8")
        return self


class ScenarioEvent(ValueModel):
    at_ms: Annotated[int, Field(ge=0, le=3_600_000)]
    rates: Rates


class Scenario(ValueModel):
    schema_version: VersionOne
    scenario_id: Identifier
    duration_ms: Annotated[int, Field(gt=0, le=3_600_000)]
    events: Annotated[tuple[ScenarioEvent, ...], Field(min_length=1, max_length=1000)]

    @model_validator(mode="after")
    def timeline(self) -> Self:
        times = [event.at_ms for event in self.events]
        if times[0] != 0 or times != sorted(set(times)) or times[-1] >= self.duration_ms:
            raise ValueError("scenario starts at zero, increases strictly and ends before duration")
        return self


class Binding(ValueModel):
    direction: Direction
    namespace: NamespaceName
    interface: InterfaceName
    ifindex: PositiveInt


class Worker(ValueModel):
    worker_id: Identifier
    pid: Annotated[int, Field(gt=1)]
    start_ticks: NonNegativeInt
    origin_ns: NonNegativeInt


class IspState(ValueModel):
    schema_version: VersionOne = 1
    install_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    lab_run_id: Identifier
    boot_id: Identifier
    phase: Literal["installing", "installed", "running", "completed", "cancelled", "failed", "partial"]
    bindings: tuple[Binding, ...]
    baseline: Rates
    current: Rates | None
    queue_limit_packets: Annotated[int, Field(gt=0, le=2**32-1)]
    burst_bytes: PositiveInt = 32768
    worker: Worker | None = None
    cancel_requested: bool = False
    scenario_id: Identifier | None = None
    error: str | None = None
    removed: tuple[Direction, ...] = ()


class PrivateStore(StateStore):
    def __init__(self, directory=Path("/run/adaptive-qos-isp")):
        super().__init__(directory)

    @contextmanager
    def locked(self):
        import fcntl
        self.directory.mkdir(mode=0o700, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise LabError("ISP journal directory must be private, root-owned and not a symlink")
        fd = os.open(self.directory / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        deadline = time.monotonic() + 15
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise LabError("ISP transaction lock timed out")
                    time.sleep(0.02)
            yield
        finally:
            os.close(fd)

    def load(self):
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        with os.fdopen(fd) as source:
            info = os.fstat(source.fileno())
            if info.st_uid != 0 or info.st_mode & 0o077 or not stat.S_ISREG(info.st_mode) or info.st_size > 1_000_000:
                raise LabError("ISP journal must be a private root-owned regular file")
            return IspState.model_validate_json(source.read(), strict=True)

    def write_record(self, state, filename, value, *, append=False):
        runs = self.directory / "runs"
        runs.mkdir(mode=0o700, exist_ok=True)
        folder = runs / state.install_id
        folder.mkdir(mode=0o700, exist_ok=True)
        for directory in (folder.parent, folder):
            info = directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
                raise LabError("ISP trace directories must be private and root-owned")
        flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | (os.O_APPEND if append else os.O_TRUNC)
        fd = os.open(folder / filename, flags, 0o600)
        with os.fdopen(fd, "w") as target:
            os.fchmod(target.fileno(), 0o600)
            target.write(json.dumps(value, allow_nan=False) + "\n")
            target.flush()
            os.fsync(target.fileno())

    def records(self, state):
        folder = self.directory / "runs" / state.install_id
        events = folder / "events.jsonl"
        scenario = folder / "scenario.json"
        return {"events": [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else [],
                "scenario": json.loads(scenario.read_text()) if scenario.exists() else None}


def worker_alive(worker):
    try:
        body = Path(f"/proc/{worker.pid}/stat").read_text().rpartition(")")[2].split()
        return body[0] != "Z" and int(body[19]) == worker.start_ticks
    except FileNotFoundError:
        return False


class Backend:
    def __init__(self, commands):
        self.commands = commands

    def tc(self, binding, *args):
        return self.commands.inside(binding.namespace, "tc", *args, timeout=5)

    def link(self, namespace, interface, run_id):
        rows = json.loads(self.commands.ip(namespace, "-j", "-d", "link", "show", "dev", interface))
        if len(rows) != 1 or rows[0].get("ifalias") != f"adaptive-qos:{run_id}" or rows[0].get("linkinfo", {}).get("info_kind") != "veth":
            raise LabError(f"ISP interface is not the expected owned veth: {namespace}/{interface}")
        return rows[0]

    def binding(self, namespace, interface, direction, run_id):
        return Binding(direction=direction, namespace=namespace, interface=interface,
                       ifindex=self.link(namespace, interface, run_id)["ifindex"])

    def check_link(self, binding, run_id):
        if self.link(binding.namespace, binding.interface, run_id)["ifindex"] != binding.ifindex:
            raise LabError("ISP interface identity changed")

    def qdiscs(self, binding):
        return json.loads(self.tc(binding, "-j", "-d", "-s", "qdisc", "show", "dev", binding.interface))

    def free(self, binding):
        rows = self.qdiscs(binding)
        if any(row.get("kind") != "noqueue" for row in rows):
            raise LabError(f"refusing foreign ISP queue tree on {binding.interface}")

    def install(self, binding, rate, limit, burst):
        root, classid, leaf = HANDLES[binding.direction]
        self.tc(binding, "qdisc", "add", "dev", binding.interface, "root", "handle", root, "htb", "default", "1")
        self.tc(binding, "class", "add", "dev", binding.interface, "parent", root, "classid", classid, "htb",
                "rate", f"{rate}bit", "ceil", f"{rate}bit", "burst", str(burst), "cburst", str(burst), "quantum", "1514")
        self.tc(binding, "qdisc", "add", "dev", binding.interface, "parent", classid, "handle", leaf, "pfifo", "limit", str(limit))

    def read(self, binding, limit):
        root, classid, leaf = HANDLES[binding.direction]
        rows = self.qdiscs(binding)
        roots = [r for r in rows if r.get("handle") == root and r.get("kind") == "htb" and r.get("root")]
        leaves = [r for r in rows if r.get("handle") == leaf and r.get("kind") == "pfifo" and r.get("parent") == classid]
        if len(roots) != 1 or len(leaves) != 1:
            raise LabError(f"owned ISP queue hierarchy missing on {binding.interface}: {rows}")
        default = roots[0].get("options", {}).get("default")
        if int(str(default), 0) != 1 or leaves[0].get("options", {}).get("limit") != limit:
            raise LabError(f"ISP default class or FIFO limit mismatch on {binding.interface}")
        actual = json.loads(self.commands.inside(binding.namespace, sys.executable, "-m", "adaptive_qos.tc_readback",
                                                str(binding.ifindex), classid, timeout=5))
        if type(actual.get("rate_bytes_per_second")) is not int or actual.get("ceil_bytes_per_second") != actual["rate_bytes_per_second"]:
            raise LabError(f"ISP kernel rate/ceiling mismatch: {actual}")
        return actual["rate_bytes_per_second"] * 8

    def change(self, binding, rate, burst):
        root, classid, _ = HANDLES[binding.direction]
        self.tc(binding, "class", "change", "dev", binding.interface, "parent", root, "classid", classid, "htb",
                "rate", f"{rate}bit", "ceil", f"{rate}bit", "burst", str(burst), "cburst", str(burst), "quantum", "1514")

    def remove(self, binding):
        rows = self.qdiscs(binding)
        root = HANDLES[binding.direction][0]
        if all(row.get("kind") == "noqueue" for row in rows):
            return
        if not any(r.get("kind") == "htb" and r.get("handle") == root and r.get("root") for r in rows):
            raise LabError(f"refusing removal of a foreign queue on {binding.interface}")
        self.tc(binding, "qdisc", "delete", "dev", binding.interface, "root", "handle", root)
        self.free(binding)


class Isp:
    def __init__(self, lab=None, store=None, backend=None, *, now=time.monotonic_ns, alive=worker_alive):
        self.lab = lab or Lab()
        self.store = store or PrivateStore()
        self.backend = backend or Backend(self.lab.commands)
        self.now, self.alive = now, alive

    def scope(self, state=None):
        lab = self.lab.state()
        if lab.phase != "ready":
            raise LabError("ISP operations require a ready routed lab")
        e = lab.config.emulator
        if e.namespace not in lab.config.routers or e.namespace == lab.config.gateway.namespace:
            raise LabError("ISP target must be an independent router")
        for iface, peer in ((e.download_interface, lab.config.gateway.namespace),
                            (e.upload_interface, lab.config.verification.server)):
            neighbors = [b.namespace for link in lab.config.links for a, b in ((link.a, link.b), (link.b, link.a))
                         if (a.namespace, a.interface) == (e.namespace, iface)]
            if neighbors != [peer]:
                raise LabError("ISP bindings do not match gateway/server orientation")
        owned = next(item for item in lab.owned if item.name == e.namespace)
        if not self.lab.owned_identity(owned):
            raise LabError("owned ISP namespace is missing")
        if state:
            if (state.lab_run_id, state.boot_id) != (lab.run_id, lab.boot_id):
                raise LabError("ISP journal belongs to another lab run or boot")
            for binding in state.bindings:
                self.backend.check_link(binding, lab.run_id)
        return lab

    def state(self):
        state = self.store.load()
        if state is None:
            raise LabError("ISP profile is not installed")
        self.scope(state)
        return state

    def validate_rates(self, rates, lab):
        e = lab.config.emulator
        for direction in ("download", "upload"):
            value = getattr(rates, f"{direction}_bps")
            if not getattr(e, f"minimum_supported_{direction}_rate_bps") <= value <= getattr(e, f"initial_{direction}_rate_bps"):
                raise ValueError(f"ISP {direction} rate is outside the declared supported envelope")

    def actual(self, state):
        if state.removed:
            raise LabError("ISP removal is incomplete")
        return Rates(**{f"{b.direction}_bps": self.backend.read(b, state.queue_limit_packets) for b in state.bindings})

    def event(self, state, kind, started, **values):
        value = {"source_kind": "emulator_ground_truth", "kind": kind, "lab_run_id": state.lab_run_id,
                 "boot_id": state.boot_id, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                 "started_ns": started, "finished_ns": self.now(), **values}
        self.store.write_record(state, "events.jsonl", value, append=True)

    def install(self):
        # Order: topology lock, then ISP lock. Workers never take the topology lock.
        with self.lab.store.locked(), self.store.locked():
            if self.store.load() is not None:
                raise LabError("ISP journal already exists; inspect/restore/remove it first")
            lab = self.scope()
            e = lab.config.emulator
            baseline = Rates(download_bps=e.initial_download_rate_bps, upload_bps=e.initial_upload_rate_bps)
            bindings = tuple(self.backend.binding(e.namespace, getattr(e, f"{d}_interface"), d, lab.run_id) for d in ("download", "upload"))
            for binding in bindings:
                self.backend.free(binding)
            state = IspState(install_id=uuid.uuid4().hex, lab_run_id=lab.run_id, boot_id=lab.boot_id,
                             phase="installing", bindings=bindings, baseline=baseline, current=None,
                             queue_limit_packets=e.queue_limit_packets, burst_bytes=e.burst_bytes)
            self.store.save(state)
            started = self.now()
            try:
                for binding in bindings:
                    self.backend.install(binding, getattr(baseline, f"{binding.direction}_bps"), state.queue_limit_packets, state.burst_bytes)
                actual = self.actual(state)
                if actual != baseline:
                    raise LabError(f"ISP read-back mismatch: requested {baseline}, observed {actual}")
                state = state.model_copy(update={"phase": "installed", "current": actual})
                self.store.save(state)
                self.event(state, "installed", started, rates=actual.model_dump())
                return {"status": "installed", "install_id": state.install_id}
            except BaseException as exc:
                errors = []
                for binding in bindings:
                    try:
                        self.backend.remove(binding)
                    except Exception as error:
                        errors.append(str(error))
                self.event(state, "installation_failed", started, error=str(exc), cleanup_errors=errors)
                if errors:
                    self.store.save(state.model_copy(update={"phase": "partial", "error": str(exc)}))
                else:
                    self.store.clear()
                raise

    def apply_locked(self, state, rates, *, worker_id=None, requested_offset_ms=None):
        self.validate_rates(rates, self.scope(state))
        if state.worker and state.worker.worker_id != worker_id:
            raise LabError("scenario owns ISP rates; cancel it before a manual change")
        old = self.actual(state)
        started, applied = self.now(), {}
        try:
            for binding in state.bindings:
                key = f"{binding.direction}_bps"
                if getattr(old, key) != getattr(rates, key):
                    self.backend.change(binding, getattr(rates, key), state.burst_bytes)
                    if self.backend.read(binding, state.queue_limit_packets) != getattr(rates, key):
                        raise LabError("ISP class update did not match read-back")
                applied[binding.direction] = self.now()
            actual = self.actual(state)
            if actual != rates:
                raise LabError("ISP directional read-back mismatch")
            state = state.model_copy(update={"current": actual, "error": None})
            self.store.save(state)
            self.event(state, "applied", started, requested=rates.model_dump(), observed=actual.model_dump(),
                       direction_verified_ns=applied, requested_offset_ms=requested_offset_ms,
                       worker_id=state.worker.worker_id if state.worker else None,
                       lateness_ns=max(0, started-state.worker.origin_ns-requested_offset_ms*1_000_000)
                       if state.worker and requested_offset_ms is not None else None)
            return state
        except BaseException as exc:
            errors = []
            for binding in state.bindings:
                try:
                    self.backend.change(binding, getattr(old, f"{binding.direction}_bps"), state.burst_bytes)
                except Exception as error:
                    errors.append(str(error))
            try:
                restored = self.actual(state)
                if restored != old:
                    errors.append("rollback read-back differs from previous state")
            except Exception as error:
                restored = None
                errors.append(str(error))
            state = state.model_copy(update={"current": restored, "phase": "partial" if errors else "failed", "error": str(exc)})
            self.store.save(state)
            self.event(state, "apply_failed", started, requested=rates.model_dump(), error=str(exc), rollback_errors=errors)
            raise

    def apply(self, rates):
        with self.store.locked():
            state = self.state()
            if state.phase in ("installing", "partial"):
                raise LabError("ISP requires restoration before a new profile")
            state = self.apply_locked(state, rates)
            return {"status": "applied", "rates": state.current.model_dump()}

    def inspect(self):
        with self.store.locked():
            state = self.state()
            return {"state": state.model_dump(mode="json"), "readback": self.actual(state).model_dump(),
                    "worker_alive": bool(state.worker and self.alive(state.worker))}

    def recover_dead_locked(self, state):
        if state.worker:
            if self.alive(state.worker):
                raise LabError("scenario is still running; cancel it first")
            self.event(state, "worker_disappeared", self.now(), worker_id=state.worker.worker_id)
            state = state.model_copy(update={"worker": None, "phase": "failed", "cancel_requested": False,
                                             "error": "worker exited without completing its scenario"})
            self.store.save(state)
        return state

    def restore(self):
        with self.store.locked():
            state = self.recover_dead_locked(self.state())
            state = self.apply_locked(state, state.baseline)
            self.store.save(state.model_copy(update={"phase": "installed", "cancel_requested": False}))
            return {"status": "restored", "rates": state.baseline.model_dump()}

    def remove(self):
        with self.store.locked():
            if self.store.load() is None:
                return {"status": "absent"}
            state = self.recover_dead_locked(self.state())
            for binding in state.bindings:
                if binding.direction not in state.removed:
                    self.backend.remove(binding)
                    state = state.model_copy(update={"removed": state.removed + (binding.direction,)})
                    self.store.save(state)
            self.event(state, "removed", self.now())
            self.store.clear()
            return {"status": "absent"}

    def cancel(self):
        with self.store.locked():
            state = self.state()
            if state.worker is None:
                return {"status": "idle"}
            worker_id = state.worker.worker_id
            if not self.alive(state.worker):
                self.recover_dead_locked(state)
                # Restore below, after releasing the non-reentrant lock.
                dead = True
            else:
                self.store.save(state.model_copy(update={"cancel_requested": True}))
                dead = False
        if dead:
            return self.restore()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            with self.store.locked():
                current = self.store.load()
                if current is None:
                    return {"status": "absent"}
                if current.install_id != state.install_id or (current.worker and current.worker.worker_id != worker_id):
                    raise LabError("scenario lease changed during cancellation")
                if current.worker is None:
                    if current.phase not in ("cancelled", "completed") or current.current != current.baseline:
                        raise LabError(current.error or "scenario stopped before baseline restoration was verified")
                    return {"status": current.phase}
                if not self.alive(current.worker):
                    self.recover_dead_locked(current)
                    break
            time.sleep(0.1)
        else:
            raise LabError("scenario cancellation timed out; namespace cleanup is refused")
        return self.restore()

    def run(self, scenario):
        with self.store.locked():
            state = self.state()
            if state.worker or state.phase not in ("installed", "completed", "cancelled"):
                raise LabError("ISP is busy or requires restore")
            lab = self.scope(state)
            for event in scenario.events:
                self.validate_rates(event.rates, lab)
            worker = Worker(worker_id=uuid.uuid4().hex, pid=os.getpid(), start_ticks=process_start_ticks(os.getpid()), origin_ns=self.now())
            state = state.model_copy(update={"worker": worker, "phase": "running", "scenario_id": scenario.scenario_id,
                                             "cancel_requested": False, "error": None})
            self.store.write_record(state, "scenario.json", scenario.model_dump(mode="json"))
            self.store.save(state)
            self.event(state, "scenario_started", worker.origin_ns, worker_id=worker.worker_id,
                       scenario_id=scenario.scenario_id, schedule=scenario.model_dump(mode="json"))
        outcome, error = "completed", None
        try:
            for event in scenario.events:
                if not self.wait_until(worker, worker.origin_ns + event.at_ms * 1_000_000):
                    outcome = "cancelled"
                    break
                with self.store.locked():
                    current = self.worker_state(worker)
                    if current.cancel_requested:
                        outcome = "cancelled"
                        break
                    self.apply_locked(current, event.rates, worker_id=worker.worker_id, requested_offset_ms=event.at_ms)
            else:
                if not self.wait_until(worker, worker.origin_ns + scenario.duration_ms * 1_000_000):
                    outcome = "cancelled"
        except BaseException as exc:
            outcome, error = "failed", str(exc) or "scenario interrupted"
        finally:
            with self.store.locked():
                current = self.worker_state(worker)
                try:
                    current = self.apply_locked(current, current.baseline, worker_id=worker.worker_id)
                except Exception as exc:
                    outcome, error = "failed", f"baseline restoration failed: {exc}"
                    current = self.store.load()
                phase = "partial" if error and current.phase == "partial" else outcome
                current = current.model_copy(update={"worker": None, "phase": phase, "cancel_requested": False, "error": error})
                self.store.save(current)
                self.event(current, f"scenario_{outcome}", self.now(), worker_id=worker.worker_id, error=error)
        if error:
            raise LabError(error)
        return {"status": outcome, "scenario_id": scenario.scenario_id}

    def worker_state(self, worker, *, validate=True):
        state = self.state() if validate else self.store.load()
        if state is None or state.worker is None or state.worker.worker_id != worker.worker_id:
            raise LabError("scenario lease belongs to another worker")
        return state

    def wait_until(self, worker, deadline):
        while True:
            with self.store.locked():
                if self.worker_state(worker, validate=False).cancel_requested:
                    return False
            remaining = deadline - self.now()
            if remaining <= 0:
                return True
            time.sleep(min(remaining / 1_000_000_000, 0.1))

    def report(self):
        with self.store.locked():
            state = self.state()
            if state.worker:
                raise LabError("export ground truth only after the scenario worker has stopped")
            return {"state": state.model_dump(mode="json"), **self.store.records(state)}


def cleanup_for_lab(lab):
    store = PrivateStore()
    if not store.path.exists():
        return
    manager = Isp(lab=lab, store=store)
    manager.cancel()
    manager.remove()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("install", "inspect", "apply", "run", "cancel", "restore", "remove", "report"))
    parser.add_argument("--download-bps", type=int)
    parser.add_argument("--upload-bps", type=int)
    parser.add_argument("--scenario", type=Path)
    args = parser.parse_args(argv)
    if platform.system() != "Linux" or os.geteuid() != 0:
        parser.exit(2, "error: ISP emulation requires root in the dedicated Linux lab VM\n")
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        manager = Isp()
        if args.command == "apply":
            result = manager.apply(Rates(download_bps=args.download_bps, upload_bps=args.upload_bps))
        elif args.command == "run":
            if args.scenario is None:
                raise ValueError("run requires --scenario")
            scenario, _ = _load(args.scenario, Scenario)
            result = manager.run(scenario)
        else:
            result = getattr(manager, args.command)()
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, LabError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        print(f"error: {exc or 'interrupted; use restore before retrying'}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
