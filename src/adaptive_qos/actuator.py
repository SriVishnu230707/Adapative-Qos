"""Apply and verify fixed CAKE policies on the owned Linux lab gateway."""

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

from adaptive_qos.config import load_runtime_config, load_static_profile
from adaptive_qos.lab import Lab, LabError, StaticInstallation


DSCP = {"interactive": 46, "streaming": 34, "best_effort": 0, "background": 8}
TIN_ORDER = ("background", "best_effort", "streaming", "interactive")
CHAIN = "mark_forward"


@dataclass(frozen=True)
class Queue:
    direction: str
    interface: str
    handle: str
    isolation: str

    @property
    def ingress(self):
        return self.direction == "download"


def gateway_queues(targets):
    return (
        Queue("upload", targets.upload_interface, "7101:", "dual-srchost"),
        Queue("download", targets.download_interface, "7102:", "dual-dsthost"),
    )


def table_name(state):
    # Kernel object names are derived from the journal and never supplied as CLI arguments.
    return "aqos_" + hashlib.sha256(state.run_id.encode()).hexdigest()[:24]


def mark_rules(lab, state, profile):
    targets = state.config.gateway
    for direction, source, dest, address_field in (
        ("upload", targets.download_interface, targets.upload_interface, "saddr"),
        ("download", targets.upload_interface, targets.download_interface, "daddr"),
    ):
        path = f'iifname "{source}" oifname "{dest}"'
        for family, ip, nfproto in ((4, "ip", "ipv4"), (6, "ip6", "ipv6")):
            for mark in profile.marks:
                address = lab.endpoint_ip(state, mark.device, family)
                label = f"{direction}:{family}:{mark.service_class}:{mark.device}"
                match = f"{path} meta l4proto {{ tcp, udp }} {ip} {address_field} {address}"
                yield label, f"{match} counter {ip} dscp set {DSCP[mark.service_class]} return"
            label = f"{direction}:{family}:best_effort:default"
            yield label, f"{path} meta nfproto {nfproto} counter {ip} dscp set 0 return"


class Actuator:
    """The caller holds the lab journal lock throughout each operation."""

    def __init__(self, lab):
        self.lab = lab

    def scope(self, state):
        if state.phase != "ready" or state.processes:
            raise LabError("static QoS requires a ready lab without tracked workloads")
        targets = state.config.gateway
        owned = next((item for item in state.owned if item.name == targets.namespace), None)
        if owned is None or not self.lab.owned_identity(owned):
            raise LabError("the gateway namespace is not owned by this lab")
        indexes = {}
        for queue in gateway_queues(targets):
            rows = json.loads(self.lab.commands.ip(targets.namespace, "-j", "-d", "link", "show", "dev", queue.interface))
            if (len(rows) != 1 or rows[0].get("ifalias") != f"adaptive-qos:{state.run_id}"
                    or rows[0].get("linkinfo", {}).get("info_kind") != "veth"):
                raise LabError(f"gateway interface is not the expected owned veth: {queue.interface}")
            indexes[queue.direction] = rows[0]["ifindex"]
            if state.static_qos and indexes[queue.direction] != getattr(state.static_qos, f"{queue.direction}_ifindex"):
                raise LabError(f"gateway interface identity changed: {queue.interface}")
        return indexes

    def inside(self, state, *args, **kwargs):
        return self.lab.commands.inside(state.config.gateway.namespace, *args, timeout=5, **kwargs)

    def roots(self, state):
        roots = {}
        for queue in gateway_queues(state.config.gateway):
            rows = json.loads(self.inside(state, "tc", "-j", "-d", "-s", "qdisc", "show", "dev", queue.interface))
            if len(rows) != 1 or rows[0].get("root") is not True:
                raise LabError(f"unexpected queue tree on {queue.interface}")
            row = rows[0]
            if row.get("kind") == "noqueue" and row.get("handle") == "0:":
                roots[queue.direction] = None
            elif state.static_qos and row.get("kind") == "cake" and row.get("handle") == queue.handle:
                # Device-filtered tc output can omit the device name.
                roots[queue.direction] = {**row, "dev": queue.interface}
            else:
                raise LabError(f"refusing foreign queue on {queue.interface}")
        return roots

    def table_exists(self, state):
        rows = json.loads(self.inside(state, "nft", "-j", "list", "tables"))["nftables"]
        return any(row.get("table", {}).get("family") == "inet" and row["table"]["name"] == table_name(state)
                   for row in rows)

    def read_marks(self, state):
        table = table_name(state)
        rows = json.loads(self.inside(state, "nft", "-j", "-n", "list", "table", "inet", table))["nftables"]
        objects = [row for row in rows if "metainfo" not in row]
        if any(set(row) not in ({"table"}, {"chain"}, {"rule"}) for row in objects):
            raise LabError("unexpected objects in the static marking table")
        chains = [row["chain"] for row in objects if "chain" in row]
        if (len(chains) != 1 or chains[0].get("name") != CHAIN or chains[0].get("hook") != "forward"
                or chains[0].get("prio") != -150 or chains[0].get("policy") != "accept"):
            raise LabError("static marking chain differs from the expected forward hook")
        expected = [label for label, _ in mark_rules(self.lab, state, state.static_qos.profile)]
        rules = [row["rule"] for row in objects if "rule" in row]
        if [rule.get("comment") for rule in rules] != expected:
            raise LabError("static marking rules differ from the expected ordered selectors")
        counters = {}
        for rule in rules:
            expressions = rule["expr"]
            found = [expr["counter"] for expr in expressions if "counter" in expr]
            if len(found) != 1 or any(type(found[0].get(key)) is not int or found[0][key] < 0 for key in ("packets", "bytes")):
                raise LabError("static marking rule lacks valid packet and byte counters")
            counters[rule["comment"]] = found[0]
            rule["expr"] = [{"counter": None} if "counter" in expr else expr for expr in expressions]
        digest = hashlib.sha256(json.dumps(objects, sort_keys=True).encode()).hexdigest()
        previous = state.static_qos.nft_sha256
        if previous is not None and digest != previous:
            raise LabError("static marking table changed since verification")
        return {"table": table, "sha256": digest, "counters": counters}

    def read(self, state):
        roots = self.roots(state)
        profile = state.static_qos.profile
        for queue in gateway_queues(state.config.gateway):
            row = roots[queue.direction]
            options = row.get("options", {}) if row is not None else {}
            rate = options.get("bandwidth")  # iproute2 JSON represents rates in bytes/s.
            if (type(rate) is not int or rate * 8 != getattr(profile, f"{queue.direction}_bps")
                    or options.get("diffserv") != profile.mode or options.get("flowmode") != queue.isolation
                    or options.get("ingress", False) is not queue.ingress or "autorate" in options
                    or options.get("nat", False) or options.get("wash", False)
                    or options.get("fwmark", 0) not in (0, "0", "0x0")
                    or options.get("ack-filter") != "disabled" or options.get("split_gso") is not True
                    or options.get("raw") is not True or options.get("overhead", 0) != 0):
                raise LabError(f"CAKE read-back differs from the requested policy on {queue.interface}: {options}")
            expected_tins = 4 if profile.mode == "diffserv4" else 1
            if len(row.get("tins", ())) != expected_tins:
                raise LabError(f"CAKE tin statistics are unavailable on {queue.interface}")
        marks = self.read_marks(state) if profile.mode == "diffserv4" else None
        return {"profile": profile.model_dump(mode="json"), "queues": roots, "marking": marks}

    def inspect(self, state):
        self.scope(state)
        if state.static_qos is None:
            self.roots(state)
            if self.table_exists(state):
                raise LabError("unexpected static marking table without an installation journal")
            return {"status": "absent"}
        if state.static_qos.phase != "active":
            raise LabError("static QoS operation was interrupted; run restore")
        return {"status": "active", **self.read(state)}

    def apply(self, state, profile, runtime):
        if runtime.controller.targets != state.config.gateway:
            raise LabError("controller targets differ from the owned lab gateway")
        for direction in ("upload", "download"):
            rate = getattr(profile, f"{direction}_bps")
            bounds = getattr(runtime.controller, direction)
            if not bounds.minimum_bps <= rate <= bounds.maximum_bps:
                raise LabError(f"static {direction} rate is outside configured bounds")
        if {mark.device for mark in profile.marks} - set(state.config.verification.clients):
            raise LabError("static marks may reference only configured lab clients")
        if state.static_qos is not None:
            raise LabError("a static policy is already journaled; run restore before applying another")
        indexes = self.scope(state)
        self.roots(state)
        table = table_name(state)
        if self.table_exists(state):
            raise LabError("static marking table name is already in use")
        script = None
        if profile.mode == "diffserv4":
            lines = [f"create table inet {table}",
                     f"add chain inet {table} {CHAIN} {{ type filter hook forward priority mangle; policy accept; }}"]
            lines.extend(f'add rule inet {table} {CHAIN} {rule} comment "{label}"'
                         for label, rule in mark_rules(self.lab, state, profile))
            script = "\n".join(lines) + "\n"
            self.inside(state, "nft", "-c", "-f", "-", input=script)
        installation = StaticInstallation(phase="applying", profile=profile, upload_ifindex=indexes["upload"],
                                          download_ifindex=indexes["download"])
        applying = state.model_copy(update={"static_qos": installation})
        self.lab.store.save(applying)
        try:
            if script is not None:
                self.inside(state, "nft", "-f", "-", input=script)
            for queue in gateway_queues(state.config.gateway):
                rate = getattr(profile, f"{queue.direction}_bps")
                self.inside(state, "tc", "qdisc", "add", "dev", queue.interface, "root", "handle", queue.handle,
                            "cake", "bandwidth", f"{rate}bit", profile.mode, queue.isolation,
                            "ingress" if queue.ingress else "egress", "nonat", "nowash", "split-gso", "raw")
            actual = self.read(applying)
            digest = actual["marking"]["sha256"] if actual["marking"] else None
            active = installation.model_copy(update={"phase": "active", "nft_sha256": digest})
            self.lab.store.save(state.model_copy(update={"static_qos": active}))
        except BaseException as failure:
            try:
                self.restore(applying)
            except (OSError, ValueError, LabError, subprocess.SubprocessError, KeyboardInterrupt) as rollback:
                raise LabError(f"apply failed: {failure}; restoration incomplete: {rollback}; run restore") from failure
            raise
        return {"status": "active", **actual}

    def restore(self, state):
        if state.static_qos is None:
            return self.inspect(state)
        self.scope(state)
        roots = self.roots(state)
        exists = self.table_exists(state)
        if exists:
            if state.static_qos.profile.mode != "diffserv4":
                raise LabError("refusing removal of an unexpected marking table")
            self.read_marks(state)
        restoring = state.static_qos.model_copy(update={"phase": "restoring"})
        self.lab.store.save(state.model_copy(update={"static_qos": restoring}))
        for queue in reversed(gateway_queues(state.config.gateway)):
            if roots[queue.direction] is not None:
                self.inside(state, "tc", "qdisc", "delete", "dev", queue.interface, "root", "handle", queue.handle)
        if exists:
            self.inside(state, "nft", "delete", "table", "inet", table_name(state))
        restored = state.model_copy(update={"static_qos": None})
        self.inspect(restored)
        self.lab.store.save(restored)
        return {"status": "restored"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    apply = commands.add_parser("apply")
    apply.add_argument("--profile", type=Path, default=Path("configs/static-qos.yaml"))
    apply.add_argument("--config-dir", type=Path, default=Path("configs"))
    commands.add_parser("inspect")
    commands.add_parser("restore")
    args = parser.parse_args(argv)
    if sys.platform != "linux" or os.geteuid() != 0:
        parser.error("run with sudo inside the dedicated Linux lab VM")

    def interrupted(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        lab = Lab()
        actuator = Actuator(lab)
        with lab.store.locked():
            state = lab.state()
            if args.command == "apply":
                result = actuator.apply(state, load_static_profile(args.profile), load_runtime_config(args.config_dir))
            else:
                result = getattr(actuator, args.command)(state)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (LabError, OSError, ValueError, KeyError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        print(f"error: {exc or type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
