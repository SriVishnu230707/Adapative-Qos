"""Record an uncontrolled experiment in the isolated four-device lab."""

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import signal
from statistics import mean, median
import subprocess
import sys
import time

from adaptive_qos.isp import Isp
from adaptive_qos.lab import Lab, LabError, OwnedProcess, process_start_ticks
from adaptive_qos.workload import SECOND


CALL, GAME, TV, BULK = "qos-client", "qos-game", "qos-tv", "qos-bulk"
CALL_SIZE, CALL_INTERVAL = 1200, 10_000_000
ECHO_SIZE, ECHO_INTERVAL = 64, 20_000_000
SEGMENT_SIZE, SEGMENT_INTERVAL = 1_000_000, 2 * SECOND
TCP_DRAIN_SECONDS = 24


def write_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_jsonl(path):
    with path.open(encoding="utf-8") as source:
        return [json.loads(line) for line in source]


class Processes:
    def __init__(self, lab, state, output, emit):
        self.lab, self.state, self.output, self.emit = lab, state, output, emit
        self.children, self.started, self.finished = {}, {}, {}
        self.owned = []

    def __enter__(self):
        return self

    def start(self, name, namespace, args, *, suffix="jsonl"):
        output = self.output / f"{name}.{suffix}"
        with output.open("x") as stdout, (self.output / f"{name}.stderr").open("x") as stderr:
            self.started[name] = time.monotonic_ns()
            process = subprocess.Popen(["ip", "netns", "exec", namespace, *args], stdout=stdout, stderr=stderr)
        self.children[name] = process
        self.owned.append(OwnedProcess(pid=process.pid, start_ticks=process_start_ticks(process.pid), namespace=namespace))
        self.lab.store.save(self.state.model_copy(update={"processes": tuple(self.owned)}))
        self.emit("process_started", name=name, namespace=namespace, started_ns=self.started[name], args=args)
        return output

    def refresh(self):
        for name, process in self.children.items():
            if name not in self.finished and (status := process.poll()) is not None:
                self.finished[name] = time.monotonic_ns()
                self.emit("process_exited", name=name, status=status, observed_ns=self.finished[name])

    def require_success(self, names):
        self.refresh()
        for name in names:
            status = self.children[name].returncode
            if status != 0:
                raise LabError(f"{name} did not finish successfully ({status}). See its stderr and raw output.")

    def __exit__(self, *_):
        for process in self.children.values():
            if process.poll() is None:
                process.terminate()
        for process in self.children.values():
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        self.refresh()
        self.lab.store.save(self.state)


def wait_ready(processes, name, ready):
    deadline = time.monotonic() + 8
    while processes.children[name].poll() is None and time.monotonic() < deadline:
        if ready():
            processes.emit("listener_ready", name=name)
            return
        time.sleep(0.05)
    raise LabError(f"{name} did not become ready")


def ready_line(path):
    with path.open(encoding="utf-8") as source:
        return '"event": "ready"' in source.readline()


def sample_network(lab, namespaces):
    result = {}
    for namespace in namespaces:
        observed = time.monotonic_ns()
        links = json.loads(lab.commands.ip(namespace, "-j", "-s", "link", "show"))
        qdiscs = json.loads(lab.commands.inside(namespace, "tc", "-j", "-s", "qdisc", "show"))
        result[namespace] = {
            "started_ns": observed, "finished_ns": time.monotonic_ns(),
            "links": [{"interface": row["ifname"], "counters": row.get("stats64", row.get("stats"))}
                      for row in links if row["ifname"] != "lo"],
            "qdiscs": [row for row in qdiscs if row.get("dev") != "lo"],
        }
    return result


def iperf_result(output, name):
    result = json.loads((output / f"{name}.json").read_text(encoding="utf-8"))
    if result.get("error"):
        raise LabError(f"{name}: {result['error']}")
    return result


def run_workloads(lab, state, family, output, emit, durations, namespaces):
    server = state.config.verification.server
    server_ip = lab.endpoint_ip(state, server, family)
    call_ip = lab.endpoint_ip(state, CALL, family)
    quiet, loaded, recovery = durations
    iperf = ["iperf3", f"-{family}", "-J"]
    with Processes(lab, state, output, emit) as processes:
        def endpoint(name, namespace, role, host, port, **settings):
            flags = [item for key, value in settings.items() for item in (f"--{key}", str(value))]
            return processes.start(name, namespace, [sys.executable, "-m", "adaptive_qos.workload", role,
                                                      host, str(port), *flags])

        def listener(name, namespace, role, host, port, **settings):
            path = endpoint(name, namespace, role, host, port, **settings)
            wait_ready(processes, name, lambda: ready_line(path))

        listener("echo-server", server, "echo-server", server_ip, 5390)
        listener("tv-server", server, "tv-server", server_ip, 5391, size=SEGMENT_SIZE)
        ports = {"download": 5394, "upload": 5395}
        for direction, port in ports.items():
            name = f"bulk-{direction}-server"
            processes.start(name, server, [*iperf, "-s", "-1", "-B", server_ip, "-p", str(port)], suffix="json")
            wait_ready(processes, name, lambda: f":{port}" in lab.commands.inside(server, "ss", "-H", "-ltn"))

        timeline = (output / "timeline.json").resolve()
        for direction, namespace, address, port in (("upload", server, server_ip, 5392),
                                                     ("download", CALL, call_ip, 5393)):
            listener(f"call-{direction}-receiver", namespace, "call-listener", address, port, timeline=timeline)
        listener("call-upload-sender", CALL, "call-source", server_ip, 5392,
                 timeline=timeline, interval=CALL_INTERVAL, size=CALL_SIZE)
        listener("call-download-sender", server, "call-source", call_ip, 5393,
                 timeline=timeline, interval=CALL_INTERVAL, size=CALL_SIZE)
        listener("echo-client", GAME, "echo-source", server_ip, 5390,
                 timeline=timeline, interval=ECHO_INTERVAL, size=ECHO_SIZE)
        origin = time.monotonic_ns() + SECOND
        load_target = origin + quiet * SECOND
        end = load_target + (loaded + recovery + TCP_DRAIN_SECONDS + 8) * SECOND
        write_json(timeline, {"start_ns": origin, "end_ns": end})
        emit("timeline", quiet_start_ns=origin, requested_load_start_ns=load_target, interactive_end_ns=end)

        next_sample = time.monotonic_ns()

        def collect(until, waiting=()):
            nonlocal next_sample
            while time.monotonic_ns() < until:
                processes.refresh()
                if waiting and all(name in processes.finished for name in waiting):
                    break
                now = time.monotonic_ns()
                if now >= next_sample:
                    emit("network_sample", interfaces=sample_network(lab, namespaces))
                    next_sample = now + SECOND
                else:
                    time.sleep(max(0, min(50_000_000, until - now, next_sample - now)) / SECOND)
            processes.refresh()

        collect(load_target)
        bulk_names = []
        for direction, port in ports.items():
            name = f"bulk-{direction}-client"
            args = [*iperf, "-c", server_ip, "-p", str(port), "-t", str(loaded), "-i", "1",
                    "--connect-timeout", "2000"]
            if direction == "download":
                args.append("-R")
            processes.start(name, BULK, args, suffix="json")
            bulk_names.append(name)
        nominal_ends = [processes.started[name] + loaded * SECOND for name in bulk_names]
        tv_start = time.monotonic_ns() + 500_000_000
        endpoint("tv-client", TV, "tv-client", server_ip, 5391, start=tv_start,
                 end=max(nominal_ends), interval=SEGMENT_INTERVAL, size=SEGMENT_SIZE)
        load_names = [*bulk_names, "bulk-download-server", "bulk-upload-server", "tv-client"]
        collect(max(nominal_ends) + TCP_DRAIN_SECONDS * SECOND, load_names)
        processes.require_success(load_names)
        recovery_start = max(processes.finished[name] for name in load_names) + SECOND
        loaded_end = min(*nominal_ends, *(processes.finished[name] for name in bulk_names))
        windows = {"quiet": [origin, min(processes.started[name] for name in bulk_names)],
                   "loaded": [max(processes.started[name] for name in bulk_names) + SECOND,
                              loaded_end - SECOND],
                   "recovery": [recovery_start, recovery_start + recovery * SECOND]}
        if windows["loaded"][1] - windows["loaded"][0] < 2 * SECOND or windows["recovery"][1] > end:
            raise LabError("insufficient measured overlap or recovery time")
        emit("measurement_windows", windows=windows)
        interactive = ["echo-client", "call-upload-sender", "call-download-sender",
                       "call-upload-receiver", "call-download-receiver"]
        collect(end + 5 * SECOND, interactive)
        processes.require_success(interactive)
        for name in ("echo-server", "tv-server"):
            if processes.children[name].poll() is not None:
                raise LabError(f"{name} exited unexpectedly")


def in_window(stamp, window):
    return window[0] <= stamp < window[1]


def packet_metrics(sent, received, window, *, echo=False):
    packets = [row for row in sent if row["event"] == "sent" and in_window(row["monotonic_ns"], window)]
    arrivals = {}
    for row in received:
        if row["event"] == "received":
            arrivals.setdefault(row["sequence"], row["monotonic_ns"])
    delivered = [(row, arrivals[row["sequence"]]) for row in packets if row["sequence"] in arrivals]
    if echo:
        rtts = [(arrival - row["monotonic_ns"]) / 1e6 for row, arrival in delivered
                if 0 <= arrival - row["monotonic_ns"] <= SECOND]
        return {"requests": len(packets), "replies_within_deadline": len(rtts),
                "deadline_miss_pct": (1 - len(rtts) / len(packets)) * 100 if packets else None,
                "median_rtt_ms": median(rtts) if rtts else None,
                "p95_rtt_ms": sorted(rtts)[math.ceil(0.95 * len(rtts)) - 1] if rtts else None,
                "mean_absolute_rtt_change_ms": mean(abs(b - a) for a, b in zip(rtts, rtts[1:]))
                if len(rtts) > 1 else None}
    variation = [abs((b_rx - a_rx) - (b["monotonic_ns"] - a["monotonic_ns"])) / 1e6
                 for (a, a_rx), (b, b_rx) in zip(delivered, delivered[1:])
                 if b["sequence"] == a["sequence"] + 1]
    seconds = (window[1] - window[0]) / SECOND
    return {"packets_sent": len(packets), "packets_delivered": len(delivered),
            "loss_pct": (1 - len(delivered) / len(packets)) * 100 if packets else None,
            "offered_payload_bps": sum(row["payload_bytes"] for row in packets) * 8 / seconds,
            "delivered_payload_bps": sum(row["payload_bytes"] for row, _ in delivered) * 8 / seconds,
            "mean_absolute_spacing_change_ms": mean(variation) if variation else None}


def summarize(output):
    info = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    events = read_jsonl(output / "events.jsonl")
    windows = next(row["windows"] for row in events if row["event"] == "measurement_windows")
    samples = [row["interfaces"] for row in events if row["event"] == "network_sample"]
    echo = read_jsonl(output / "echo-client.jsonl")
    flows = {direction: (read_jsonl(output / f"call-{direction}-sender.jsonl"),
                         read_jsonl(output / f"call-{direction}-receiver.jsonl"))
             for direction in ("download", "upload")}
    phases = {name: {"window_ns": window, "game": packet_metrics(echo, echo, window, echo=True),
                     "call": {direction: packet_metrics(*flow, window) for direction, flow in flows.items()}}
              for name, window in windows.items()}
    bulk = {}
    for direction, receiver in (("download", "client"), ("upload", "server")):
        result = iperf_result(output, f"bulk-{direction}-{receiver}")
        totals = result["end"]["sum_received"]
        bulk[direction] = {"receiver": receiver, "payload_bytes": totals["bytes"],
                           "duration_seconds": totals["seconds"], "goodput_bps": totals["bits_per_second"]}
    segments = read_jsonl(output / "tv-client.jsonl")
    queues = {}
    for binding in info["isp_bindings"]:
        name, device = binding["namespace"], binding["interface"]
        observed = [(sample[name]["finished_ns"], qdisc) for sample in samples for qdisc in sample[name]["qdiscs"]
                    if qdisc.get("dev") == device and qdisc["kind"] == "pfifo"]
        if not observed:
            raise LabError(f"no FIFO samples for {name}/{device}")
        queues[binding["direction"]] = {
            "interface": f"{name}/{device}",
            "fifo_drop_delta_packets": observed[-1][1]["drops"] - observed[0][1]["drops"],
            "peak_sampled_backlog_bytes": {
                phase: max((qdisc["backlog"] for stamp, qdisc in observed if in_window(stamp, window)), default=None)
                for phase, window in windows.items()},
        }
    counter_deltas = []
    for namespace in samples[0]:
        for first, last in zip(samples[0][namespace]["links"], samples[-1][namespace]["links"], strict=True):
            if first["interface"] != last["interface"]:
                raise LabError(f"interface inventory changed in {namespace}")
            counter_deltas.append({"namespace": namespace, "interface": first["interface"],
                                   **{f"{direction}_bytes": last["counters"][direction]["bytes"] -
                                      first["counters"][direction]["bytes"] for direction in ("rx", "tx")}})
    return {"schema_version": 1, "family": info["family"], "isp_rates_bps": info["isp_rates_bps"],
            "phases": phases, "bulk": bulk, "isp_fifo": queues, "network_sample_count": len(samples),
            "interface_counter_deltas": counter_deltas,
            "tv": {"attempted_segments": len(segments),
                   "completed_segments": sum(row["received_bytes"] == SEGMENT_SIZE for row in segments),
                   "deadline_misses": sum(not row["met_deadline"] for row in segments),
                   "received_payload_bytes": sum(row["received_bytes"] for row in segments)}}


def verify_baseline(summary, gateway):
    if any(flow["payload_bytes"] <= 0 for flow in summary["bulk"].values()):
        raise LabError("both TCP receivers must show delivery")
    for phase, measured in summary["phases"].items():
        if not measured["game"]["requests"] or any(not flow["packets_sent"] for flow in measured["call"].values()):
            raise LabError(f"missing interactive workload samples during {phase}")
    quiet = summary["phases"]["quiet"]
    if not quiet["game"]["replies_within_deadline"] or any(not flow["packets_delivered"] for flow in quiet["call"].values()):
        raise LabError("quiet-period interactive traffic did not arrive")
    if not summary["tv"]["attempted_segments"] or not summary["tv"]["received_payload_bytes"]:
        raise LabError("TV workload did not deliver payload")
    for namespace in {gateway, *(queue["interface"].split("/")[0] for queue in summary["isp_fifo"].values())}:
        counters = [row for row in summary["interface_counter_deltas"] if row["namespace"] == namespace]
        if not counters or any(row["rx_bytes"] <= 0 or row["tx_bytes"] <= 0 for row in counters):
            raise LabError(f"missing bidirectional counter progress in {namespace}")
    for direction, queue in summary["isp_fifo"].items():
        if queue["peak_sampled_backlog_bytes"]["loaded"] is None:
            raise LabError(f"no FIFO observation during loaded {direction}")
    if not any(queue["peak_sampled_backlog_bytes"]["loaded"] > 0 or queue["fifo_drop_delta_packets"] > 0
               for queue in summary["isp_fifo"].values()):
        raise LabError("no ISP contention was observed during the baseline")


def run(output, family, durations):
    output.mkdir(parents=True, mode=0o700, exist_ok=False)
    lab = Lab()
    isp = Isp(lab=lab)
    info = {"schema_version": 1, "status": "running", "mode": "uncontrolled", "synthetic": True,
            "started_at_utc": datetime.now(timezone.utc).isoformat(), "family": family,
            "requested_seconds": dict(zip(("quiet", "loaded", "recovery"), durations)),
            "environment": {"kernel": platform.release(), "python": platform.python_version(),
                            "os": platform.freedesktop_os_release()["PRETTY_NAME"], "logical_cpus": os.cpu_count()}}
    manifest = output / "manifest.json"
    write_json(manifest, info)
    try:
        with lab.store.locked(), isp.store.locked():
            state = lab.state()
            if state.phase != "ready" or state.processes or set(state.config.verification.clients) != {CALL, GAME, TV, BULK}:
                raise LabError("start the ready four-device lab with configs/lab-multi.yaml")
            if not all(lab.owned_identity(owned) for owned in state.owned):
                raise LabError("an owned lab namespace is missing")
            isp_state = isp.state()
            if isp_state.worker or isp_state.phase not in ("installed", "completed", "cancelled"):
                raise LabError("use a verified static ISP profile without a scheduled worker")
            gateway = state.config.gateway.namespace
            provider = state.config.emulator.namespace
            namespaces = (gateway, provider)
            initial = sample_network(lab, namespaces)
            if any(row["kind"] != "noqueue" for row in initial[gateway]["qdiscs"]):
                raise LabError("the baseline requires the default gateway queues")
            rates = isp.actual(isp_state).model_dump()
            info.update(lab_run_id=state.run_id, boot_id=state.boot_id, config_sha256=state.config_sha256,
                        isp_rates_bps=rates, isp_verified_ns=time.monotonic_ns(),
                        isp_bindings=[binding.model_dump() for binding in isp_state.bindings],
                        tools={tool: lab.commands.run([tool, flag]).splitlines()[0]
                               for tool, flag in (("iperf3", "--version"), ("tc", "-V"))})
            write_json(manifest, info)
            write_json(output / "ground-truth.json", {
                "scope": "offline evaluator only", "synthetic": True,
                "devices": {CALL: "call-like paced bidirectional UDP", GAME: "game-like UDP echo",
                            TV: "TV-like HTTP segments", BULK: "backlogged TCP upload and download"},
                "call": {"payload_bytes": CALL_SIZE, "interval_ns": CALL_INTERVAL},
                "game": {"payload_bytes": ECHO_SIZE, "interval_ns": ECHO_INTERVAL, "reply_deadline_ns": SECOND},
                "tv": {"segment_payload_bytes": SEGMENT_SIZE, "request_interval_ns": SEGMENT_INTERVAL},
            })
            with (output / "events.jsonl").open("x", encoding="utf-8", buffering=1) as log:
                def emit(event, **fields):
                    log.write(json.dumps({"event": event, "monotonic_ns": time.monotonic_ns(), **fields}) + "\n")

                emit("network_sample", interfaces=initial)
                run_workloads(lab, state, family, output, emit, durations, namespaces)
                emit("network_sample", interfaces=sample_network(lab, namespaces))
                final_rates = isp.actual(isp_state).model_dump()
                emit("isp_readback", rates_bps=final_rates)
                if final_rates != rates:
                    raise LabError("ISP rates changed during the baseline")
            summary = summarize(output)
            write_json(output / "summary.json", summary)
            verify_baseline(summary, gateway)
        info["status"] = "pass"
    except BaseException as exc:
        info.update(status="failed", error=str(exc) or type(exc).__name__)
        raise
    finally:
        info["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        write_json(manifest, info)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("run")
    capture.add_argument("--output", type=Path, required=True)
    capture.add_argument("--family", type=int, choices=(4, 6), default=4)
    for phase, default in (("quiet", 8), ("loaded", 18), ("recovery", 7)):
        capture.add_argument(f"--{phase}-seconds", type=int, default=default)
    replay = commands.add_parser("summarize")
    replay.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            if sys.platform != "linux" or os.geteuid() != 0:
                parser.error("run with sudo inside the dedicated Linux lab VM")
            durations = (args.quiet_seconds, args.loaded_seconds, args.recovery_seconds)
            if any(seconds < 5 or seconds > 120 for seconds in durations):
                parser.error("phase durations must be between 5 and 120 seconds")
            os.umask(0o077)

            def interrupted(*_):
                raise KeyboardInterrupt

            signal.signal(signal.SIGTERM, interrupted)
            result = run(args.output, args.family, durations)
        else:
            result = summarize(args.run_dir)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (LabError, OSError, ValueError, KeyError, StopIteration, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        print(f"error: {exc or type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
