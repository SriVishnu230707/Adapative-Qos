#!/usr/bin/env python3
"""Exercise step-02 Linux capabilities only inside namespaces created by this run."""

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid


class Probe:
    def __init__(self):
        self.checks = []
        self.owned = []

    def record(self, name, status, detail):
        self.checks.append({"name": name, "status": status, "detail": detail})
        print(f"{status.upper():7} {name}: {str(detail)[:250]}", flush=True)

    def command(self, args, *, data=None, timeout=15):
        result = subprocess.run(
            args, input=data, text=True, capture_output=True, timeout=timeout,
            env={**os.environ, "LC_ALL": "C"}, check=False,
        )
        if result.returncode:
            raise RuntimeError(f"{' '.join(args)}: {(result.stderr or result.stdout).strip()[:1800]}")
        return result.stdout.strip()

    def attempt(self, name, action):
        try:
            detail = action()
            self.record(name, "pass", detail)
            return True
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError) as exc:
            self.record(name, "fail", str(exc))
            return False

    def cleanup(self):
        for namespace in reversed(self.owned):
            def remove(ns=namespace):
                self.command(["ip", "netns", "delete", ns])
                remaining = [line.split()[0] for line in self.command(["ip", "netns", "list"]).splitlines() if line.split()]
                if ns in remaining:
                    raise RuntimeError(f"Namespace {ns} is still registered after deletion")
                return "removed and verified absent"
            self.attempt(f"cleanup:{namespace}", remove)


def read_os_release():
    values = {}
    source = Path("/etc/os-release")
    if source.exists():
        for line in source.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                values[key] = value.strip('"')
    return values


def network_checks(probe):
    suffix = uuid.uuid4().hex[:10]
    first, second = f"aqpf-{suffix}-a", f"aqpf-{suffix}-b"
    for namespace in (first, second):
        probe.command(["ip", "netns", "add", namespace])
        probe.owned.append(namespace)  # Never delete a name whose creation failed.
        probe.record(f"namespace:{namespace}", "pass", "created; scheduled for cleanup")

    def in_ns(namespace, *args, **kwargs):
        return probe.command(["ip", "netns", "exec", namespace, *args], **kwargs)

    in_ns(first, "ip", "link", "add", "q0", "type", "veth", "peer", "name", "q1")
    in_ns(first, "ip", "link", "set", "q1", "netns", second)
    for namespace, device, address in ((first, "q0", "198.18.0.1/30"), (second, "q1", "198.18.0.2/30")):
        in_ns(namespace, "ip", "link", "set", "lo", "up")
        in_ns(namespace, "ip", "address", "add", address, "dev", device)
        in_ns(namespace, "ip", "link", "set", device, "up")
    probe.record("veth", "pass", "pair connects two owned namespaces; no host interface changed")
    probe.attempt("ipv4", lambda: in_ns(first, "ping", "-n", "-c", "2", "-W", "2", "198.18.0.2"))

    def ipv6():
        for namespace, device, address in ((first, "q0", "fd42:ad:2::1/64"), (second, "q1", "fd42:ad:2::2/64")):
            in_ns(namespace, "ip", "-6", "address", "add", address, "dev", device, "nodad")
        return in_ns(first, "ping", "-6", "-n", "-c", "2", "-W", "2", "fd42:ad:2::2")
    probe.attempt("ipv6", ipv6)
    probe.attempt("namespace_forwarding", lambda: in_ns(first, "sysctl", "-w", "net.ipv4.ip_forward=1", "net.ipv6.conf.all.forwarding=1"))

    for number, (kind, settings) in enumerate((("cake", ["bandwidth", "10Mbit", "besteffort"]), ("fq_codel", []), ("htb", ["default", "10"]), ("netem", ["delay", "1ms"])), start=1):
        # A different handle creates a replacement object instead of attempting
        # to change the existing object's qdisc kind under the same handle.
        def qdisc(qdisc_kind=kind, qdisc_settings=settings, root_handle=f"{number}:"):
            in_ns(first, "tc", "qdisc", "replace", "dev", "q0", "root", "handle", root_handle, qdisc_kind, *qdisc_settings)
            if qdisc_kind == "htb":
                in_ns(first, "tc", "class", "add", "dev", "q0", "parent", root_handle, "classid", root_handle + "10", "htb", "rate", "10Mbit")
            observed = json.loads(in_ns(first, "tc", "-j", "-s", "qdisc", "show", "dev", "q0"))
            if not any(row.get("kind") == qdisc_kind for row in observed):
                raise RuntimeError(f"Read-back did not contain {qdisc_kind}")
            return observed
        probe.attempt(f"qdisc:{kind}", qdisc)

    def ifb():
        in_ns(first, "ip", "link", "add", "ifb0", "type", "ifb")
        in_ns(first, "ip", "link", "set", "ifb0", "up")
        return json.loads(in_ns(first, "ip", "-j", "link", "show", "ifb0"))
    probe.attempt("ifb", ifb)

    def firewall():
        rules = "table inet aqpf {\n chain output {\n type filter hook output priority 0; policy accept;\n ct state { new, established, related } counter\n }\n}\n"
        in_ns(first, "nft", "-f", "-", data=rules)
        return json.loads(in_ns(first, "nft", "-j", "list", "table", "inet", "aqpf"))
    firewall_ok = probe.attempt("nftables_stateful_rule", firewall)

    def conntrack():
        in_ns(first, "sysctl", "-w", "net.netfilter.nf_conntrack_acct=1")
        # A new tuple after enabling accounting; preceding pings must not supply the evidence.
        in_ns(first, "ping", "-n", "-e", "4242", "-c", "2", "-W", "2", "198.18.0.2")
        result = in_ns(first, "conntrack", "-L", "-f", "ipv4", "-p", "icmp", "-o", "extended")
        matches = [line for line in result.splitlines() if "id=4242" in line and "packets=" in line and "bytes=" in line]
        if not matches:
            raise RuntimeError("New ICMP flow lacks accounted packet/byte counters")
        return matches
    if firewall_ok:
        probe.attempt("conntrack_accounting", conntrack)
    else:
        probe.record("conntrack_accounting", "blocked", "stateful nftables prerequisite failed")

    for namespace in (first, second):
        probe.attempt(f"inspect:{namespace}", lambda ns=namespace: {
            "links": json.loads(in_ns(ns, "ip", "-j", "address", "show")),
            "ipv4_routes": json.loads(in_ns(ns, "ip", "-j", "route", "show")),
            "ipv6_routes": json.loads(in_ns(ns, "ip", "-j", "-6", "route", "show")),
        })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("work/preflight/latest.json"))
    parser.add_argument("--require-reference", action="store_true", help="Require an Ubuntu 24.04 VM, excluding WSL and containers")
    args = parser.parse_args()
    probe = Probe()
    started = dt.datetime.now(dt.timezone.utc).isoformat()
    os_release = read_os_release()
    kernel = platform.release()
    environment = {"os_release": os_release, "kernel": kernel, "architecture": platform.machine(), "python": platform.python_version(), "logical_cpus": os.cpu_count() or 1}

    if platform.system() != "Linux":
        probe.record("platform", "fail", "Linux required; no network operations attempted")
    elif os.geteuid() != 0:
        probe.record("privileges", "fail", "Run with sudo inside the dedicated lab; no network operations attempted")
    else:
        probe.record("privileges", "pass", "effective UID 0; active operations stay inside owned namespaces")
        detect = shutil.which("systemd-detect-virt")
        detected_vm = subprocess.run([detect, "--vm"], capture_output=True, text=True, check=False).stdout.strip() if detect else "unknown"
        detected_container = subprocess.run([detect, "--container"], capture_output=True, text=True, check=False).stdout.strip() if detect else "unknown"
        environment.update({"virtualization": detected_vm, "container": detected_container, "wsl": "microsoft" in kernel.lower() or "wsl" in kernel.lower()})
        is_reference = os_release.get("ID") == "ubuntu" and os_release.get("VERSION_ID") == "24.04" and detected_vm not in ("none", "unknown", "") and detected_container == "none" and not environment["wsl"]
        environment["reference_profile"] = is_reference
        probe.record("reference_profile", "pass" if is_reference else ("fail" if args.require_reference else "warn"), f"{os_release.get('PRETTY_NAME', 'unknown')}; VM={detected_vm}; container={detected_container}; WSL={environment['wsl']}")
        tools = {"git": ["--version"], "ip": ["-V"], "tc": ["-V"], "nft": ["--version"], "conntrack": ["-V"], "iperf3": ["--version"], "ping": ["-V"], "python3": ["--version"], "ethtool": ["--version"], "jq": ["--version"], "mpstat": ["-V"], "modprobe": ["--version"], "sysctl": ["--version"]}
        all_tools = True
        for tool, flags in tools.items():
            all_tools = probe.attempt(f"tool:{tool}", lambda name=tool, options=flags: probe.command([name, *options])) and all_tools

        def venv_check():
            with tempfile.TemporaryDirectory(prefix="aqpf-venv-") as directory:
                probe.command([sys.executable, "-m", "venv", directory], timeout=60)
                return probe.command([str(Path(directory) / "bin/python"), "-m", "pip", "--version"])
        probe.attempt("python_venv", venv_check)
        environment["memory"] = {line.split(':')[0]: line.split(':')[1].strip() for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith(("MemTotal:", "MemAvailable:", "SwapTotal:"))}
        space = shutil.disk_usage(args.output.parent.resolve() if args.output.parent.exists() else Path.cwd())
        environment["disk_free_bytes"] = space.free
        memory_kib = int(environment["memory"]["MemTotal"].split()[0])
        if environment["logical_cpus"] < 4 or memory_kib < 6 * 1024 * 1024:
            probe.record("planning_resources", "warn", "Below the plan's 4-vCPU/6-GiB allocation; capability checks do not establish benchmark readiness")
        probe.attempt("package_versions", lambda: probe.command(["dpkg-query", "-W", "git", "python3", "python3-venv", "python3-pip", "iproute2", "nftables", "conntrack", "iperf3", "iputils-ping", "ethtool", "jq", "sysstat", "kmod", "procps", "util-linux"]))
        if all_tools:
            def interrupted(*_):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, interrupted)
            try:
                network_checks(probe)
            except (OSError, subprocess.SubprocessError, RuntimeError, ValueError, KeyboardInterrupt) as exc:
                probe.record("network_setup", "fail", str(exc) or "interrupted")
            finally:
                probe.cleanup()
        else:
            probe.record("network_checks", "blocked", "Install missing tools before active checks")

    passed = not any(item["status"] in ("fail", "blocked") for item in probe.checks)
    script_dir = Path(__file__).resolve().parent
    report = {"schema_version": 1, "scope": "step-02 capabilities, not QoS acceptance or benchmark results", "started_at_utc": started, "completed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "status": "pass" if passed else "fail", "reference_required": args.require_reference, "environment": environment, "checks": probe.checks, "owned_namespaces": probe.owned, "script_sha256": {name: hashlib.sha256((script_dir / name).read_bytes()).hexdigest() for name in ("preflight.sh", "preflight.py", "install-tools.sh")}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(f"Manifest: {args.output.resolve()}\nOverall: {report['status'].upper()}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
