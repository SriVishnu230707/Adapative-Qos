"""Owned Linux namespace lab. No changes are made in the initial network namespace.

This runner is separate from the unprivileged runtime controller. Its root-owned
journal lives in /run, and survives an interrupted command until cleanup succeeds.
"""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
from ipaddress import ip_interface
import json
import os
from pathlib import Path
import platform
import signal
import stat
import subprocess
import sys
import time
import uuid
from typing import Literal

from adaptive_qos.config import LabConfig, load_lab_config
from adaptive_qos.models import Identifier, NamespaceName, NonNegativeInt, PositiveInt, ValueModel, VersionOne


class LabError(RuntimeError):
    pass


class OwnedNamespace(ValueModel):
    name: NamespaceName
    device: NonNegativeInt
    inode: PositiveInt


class OwnedProcess(ValueModel):
    pid: PositiveInt
    start_ticks: NonNegativeInt
    namespace: NamespaceName


class LabState(ValueModel):
    schema_version: VersionOne = 1
    run_id: Identifier
    boot_id: Identifier
    phase: Literal["building", "ready", "cleanup_failed"]
    config: LabConfig
    config_sha256: str
    owned: tuple[OwnedNamespace, ...] = ()
    processes: tuple[OwnedProcess, ...] = ()


class Commands:
    def run(self, args, *, timeout=20):
        result = subprocess.run(args, text=True, capture_output=True, timeout=timeout,
                                env={**os.environ, "LC_ALL": "C"}, check=False)
        if result.returncode:
            raise LabError(f"{' '.join(args)}: {(result.stderr or result.stdout).strip()[:2000]}")
        return result.stdout.strip()

    def ip(self, namespace, *args):
        return self.run(["ip", "-n", namespace, *args])

    def inside(self, namespace, *args, **kwargs):
        return self.run(["ip", "netns", "exec", namespace, *args], **kwargs)


class StateStore:
    def __init__(self, directory=Path("/run/adaptive-qos-lab")):
        self.directory = Path(directory)
        self.path = self.directory / "state.json"

    @contextmanager
    def locked(self):
        import fcntl  # Imported only after the Linux/root gate.
        self.directory.mkdir(mode=0o700, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise LabError("lab journal directory must be root-owned, mode 0700, and not a symlink")
        fd = os.open(self.directory / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LabError("another lab operation holds the journal lock") from exc
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
            if info.st_uid != 0 or info.st_mode & 0o077 or not stat.S_ISREG(info.st_mode):
                raise LabError("lab journal must be a private root-owned regular file")
            return LabState.model_validate_json(source.read(), strict=True)

    def save(self, state):
        temporary = self.directory / f"state-{uuid.uuid4().hex}.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w") as target:
                target.write(state.model_dump_json(indent=2) + "\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def clear(self):
        self.path.unlink(missing_ok=True)

    def pin(self, owned, commands):
        directory = self.directory / "pins"
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise LabError("namespace pins must use a private root-owned directory")
        path = directory / owned.name
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        try:
            commands.run(["mount", "--bind", f"/run/netns/{owned.name}", str(path)])
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        self.verify_pin(owned)

    def verify_pin(self, owned):
        try:
            info = (self.directory / "pins" / owned.name).stat()
        except FileNotFoundError as exc:
            raise LabError(f"ownership pin is missing: {owned.name}; manual reconciliation required") from exc
        if (info.st_dev, info.st_ino) != (owned.device, owned.inode):
            raise LabError(f"ownership pin identity changed: {owned.name}")

    def unpin(self, owned, commands):
        self.verify_pin(owned)
        path = self.directory / "pins" / owned.name
        commands.run(["umount", str(path)])
        path.unlink()


def namespace_identity(name):
    info = Path("/run/netns", name).stat()
    return OwnedNamespace(name=name, device=info.st_dev, inode=info.st_ino)


def process_start_ticks(pid):
    # comm is parenthesized and can contain spaces or closing parentheses.
    return int(Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[19])


def validate_runnable(config):
    if any(not name.startswith("qos-") for name in config.namespaces):
        raise LabError("lab namespace names must start with qos-")
    if config.verification is None or config.gateway.namespace not in config.routers or len(config.routers) < 2:
        raise LabError("routed lab requires explicit clients/server and at least two routers including the gateway")
    for ns in (*config.verification.clients, config.verification.server):
        endpoints = [e for link in config.links for e in (link.a, link.b) if e.namespace == ns]
        if len(endpoints) != 1 or endpoints[0].ipv4_cidr is None or endpoints[0].ipv6_cidr is None:
            raise LabError("each verification endpoint requires one dual-stack interface")
        if {r.address_family for r in config.routes if r.namespace == ns and r.destination == "default"} != {4, 6}:
            raise LabError("verification endpoints require IPv4 and IPv6 default routes")


class Lab:
    def __init__(self, store=None, commands=None, *, identity=namespace_identity, boot_id=None, hold_namespace=None, before_down=None):
        self.store = store or StateStore()
        self.commands = commands or Commands()
        self.identity = identity
        self.hold_namespace = hold_namespace or (lambda name: Path("/run/netns", name).open("rb"))
        self.boot_id = boot_id or Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        self.before_down = before_down or cleanup_isp

    def owned_identity(self, owned):
        # A persistent bind mount keeps the original namespace alive between CLI
        # invocations, preventing its inode being recycled for a replacement.
        self.store.verify_pin(owned)
        try:
            actual = self.identity(owned.name)
        except FileNotFoundError:
            return False
        if actual != owned:
            raise LabError(f"namespace identity changed; refusing to touch {owned.name}")
        return True

    def state(self):
        state = self.store.load()
        if state is None:
            raise LabError("lab is not set up; run lab/up.sh")
        validate_runnable(state.config)
        if state.boot_id != self.boot_id:
            raise LabError("journal belongs to another boot; reconcile it without deleting unverified namespaces")
        return state

    def up(self, config, digest):
        validate_runnable(config)
        if self.store.load() is not None:
            raise LabError("lab journal already exists; inspect it and run lab/down.sh before setup")
        existing = {line.split()[0] for line in self.commands.run(["ip", "netns", "list"]).splitlines() if line}
        collisions = existing.intersection(config.namespaces)
        if collisions:
            raise LabError(f"namespace collision; nothing changed: {', '.join(sorted(collisions))}")
        state = LabState(run_id=uuid.uuid4().hex, boot_id=self.boot_id, phase="building", config=config, config_sha256=digest)
        self.store.save(state)
        try:
            for name in config.namespaces:
                self.commands.run(["ip", "netns", "add", name])
                # Keep an FD open until pinning succeeds, including error cleanup.
                with self.hold_namespace(name):
                    owned = self.identity(name)
                    try:
                        self.store.pin(owned, self.commands)
                    except BaseException:
                        if self.identity(name) == owned:
                            self.commands.run(["ip", "netns", "delete", name])
                        raise
                state = state.model_copy(update={"owned": state.owned + (owned,)})
                self.store.save(state)
                self.commands.ip(name, "link", "set", "lo", "up")
                forwarding = "1" if name in config.routers else "0"
                self.commands.inside(name, "sysctl", "-qw", f"net.ipv4.ip_forward={forwarding}",
                                     f"net.ipv6.conf.all.forwarding={forwarding}")
            for index, link in enumerate(config.links):
                peer = f"aqv{state.run_id[:7]}{index}"
                self.commands.ip(link.a.namespace, "link", "add", link.a.interface, "type", "veth", "peer", "name", peer)
                self.commands.ip(link.a.namespace, "link", "set", peer, "netns", link.b.namespace)
                self.commands.ip(link.b.namespace, "link", "set", peer, "name", link.b.interface)
                for endpoint in (link.a, link.b):
                    self.commands.ip(endpoint.namespace, "link", "set", endpoint.interface, "alias", f"adaptive-qos:{state.run_id}")
            for bridge in config.bridges:
                self.commands.ip(bridge.namespace, "link", "add", bridge.interface, "type", "bridge", "stp_state", "0", "forward_delay", "0")
                self.commands.ip(bridge.namespace, "link", "set", bridge.interface, "alias", f"adaptive-qos:{state.run_id}")
                for port in bridge.ports:
                    self.commands.ip(bridge.namespace, "link", "set", port, "master", bridge.interface)
                self.commands.ip(bridge.namespace, "link", "set", bridge.interface, "up")
            for link in config.links:
                for endpoint in (link.a, link.b):
                    for family in (4, 6):
                        cidr = getattr(endpoint, f"ipv{family}_cidr")
                        if cidr:
                            self.commands.ip(endpoint.namespace, f"-{family}", "address", "add", cidr, "dev", endpoint.interface)
                    self.commands.ip(endpoint.namespace, "link", "set", endpoint.interface, "up")
            for route in config.routes:
                self.commands.ip(route.namespace, f"-{route.address_family}", "route", "add", route.destination,
                                 "via", route.via, "dev", route.interface)
            self.wait_for_ipv6(state)
            state = state.model_copy(update={"phase": "ready"})
            self.store.save(state)
            inspected = self.inspect()
            self.validate_snapshot(state.config, inspected)
            return {"status": "ready", "run_id": state.run_id, "namespaces": list(config.namespaces), "config_sha256": digest}
        except BaseException as exc:
            try:
                self.down()
            except Exception as cleanup:
                raise LabError(f"setup failed: {exc}; cleanup incomplete: {cleanup}") from exc
            raise

    def wait_for_ipv6(self, state):
        deadline = time.monotonic() + 8
        while True:
            tentative = False
            for owned in state.owned:
                rows = json.loads(self.commands.ip(owned.name, "-j", "-6", "address", "show"))
                for row in rows:
                    for address in row.get("addr_info", []):
                        flags = address.get("flags", [])
                        if address.get("dadfailed") or "dadfailed" in flags:
                            raise LabError(f"IPv6 duplicate address detected in {owned.name}")
                        tentative |= bool(address.get("tentative") or "tentative" in flags)
            if not tentative:
                return
            if time.monotonic() >= deadline:
                raise LabError("IPv6 duplicate-address detection did not finish")
            time.sleep(0.1)

    def inspect(self):
        state = self.state()
        nodes = {}
        for owned in state.owned:
            if not self.owned_identity(owned):
                raise LabError(f"owned namespace is missing: {owned.name}")
            forwarding = self.commands.inside(owned.name, "sysctl", "-n", "net.ipv4.ip_forward", "net.ipv6.conf.all.forwarding").splitlines()
            nodes[owned.name] = {
                "identity": owned.model_dump(),
                "links": json.loads(self.commands.ip(owned.name, "-j", "-s", "link", "show")),
                "addresses": json.loads(self.commands.ip(owned.name, "-j", "address", "show")),
                "ipv4_routes": json.loads(self.commands.ip(owned.name, "-j", "-4", "route", "show")),
                "ipv6_routes": json.loads(self.commands.ip(owned.name, "-j", "-6", "route", "show")),
                "forwarding": {"ipv4": int(forwarding[0]), "ipv6": int(forwarding[1])},
                "qdiscs": json.loads(self.commands.inside(owned.name, "tc", "-j", "-s", "qdisc", "show")),
                "pids": self.commands.run(["ip", "netns", "pids", owned.name]).splitlines(),
            }
        return {"status": state.phase, "run_id": state.run_id, "config_sha256": state.config_sha256,
                "boot_id": state.boot_id, "namespaces": nodes}

    @staticmethod
    def validate_snapshot(config, snapshot):
        for name in config.namespaces:
            node = snapshot["namespaces"][name]
            expected = 1 if name in config.routers else 0
            if set(node["forwarding"].values()) != {expected}:
                raise LabError(f"forwarding differs from configured role in {name}")
            links = {row["ifname"]: row for row in node["links"]}
            if "UP" not in links["lo"]["flags"]:
                raise LabError(f"loopback is down in {name}")
            for link in config.links:
                for endpoint in (link.a, link.b):
                    if endpoint.namespace != name:
                        continue
                    if "UP" not in links.get(endpoint.interface, {}).get("flags", []):
                        raise LabError(f"interface is down: {name}/{endpoint.interface}")
                    addresses = {f"{a['local']}/{a['prefixlen']}" for row in node["addresses"]
                                 if row["ifname"] == endpoint.interface for a in row.get("addr_info", [])}
                    for cidr in (endpoint.ipv4_cidr, endpoint.ipv6_cidr):
                        if cidr and str(ip_interface(cidr)) not in addresses:
                            raise LabError(f"configured address missing: {name}/{cidr}")
            for route in config.routes:
                if route.namespace == name and not any(
                    row.get("dst") == route.destination and row.get("gateway") == route.via and row.get("dev") == route.interface
                    for row in node[f"ipv{route.address_family}_routes"]
                ):
                    raise LabError(f"configured route missing: {name}/{route.destination}")
            for bridge in config.bridges:
                if bridge.namespace == name:
                    if "UP" not in links.get(bridge.interface, {}).get("flags", []):
                        raise LabError(f"bridge is down in {name}")
                    for port in bridge.ports:
                        if links.get(port, {}).get("master") not in (bridge.interface, links[bridge.interface]["ifindex"]):
                            raise LabError(f"bridge membership missing: {name}/{port}")

    def stop_tracked(self, state):
        for process in state.processes:
            try:
                if process_start_ticks(process.pid) != process.start_ticks:
                    raise LabError(f"tracked PID was reused; refusing to signal {process.pid}")
                owned = next(o for o in state.owned if o.name == process.namespace)
                if Path(f"/proc/{process.pid}/ns/net").stat().st_ino != owned.inode:
                    raise LabError(f"tracked process left its owned namespace: {process.pid}")
                os.kill(process.pid, signal.SIGTERM)
                deadline = time.monotonic() + 2
                while Path(f"/proc/{process.pid}").exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
                if Path(f"/proc/{process.pid}").exists():
                    raise LabError(f"tracked process did not exit: {process.pid}")
            except FileNotFoundError:
                pass

    def down(self):
        state = self.store.load()
        if state is None:
            return {"status": "absent", "removed": []}
        state = self.state()
        # Preflight every identity and occupant before deleting anything.
        for owned in state.owned:
            self.owned_identity(owned)
        self.before_down(self)
        self.stop_tracked(state)
        for owned in state.owned:
            if self.owned_identity(owned):
                pids = self.commands.run(["ip", "netns", "pids", owned.name]).split()
                if pids:
                    raise LabError(f"untracked processes in {owned.name}; stop them before cleanup: {', '.join(pids)}")
        removed, errors = [], []
        for owned in reversed(state.owned):
            try:
                if self.owned_identity(owned):
                    self.commands.run(["ip", "netns", "delete", owned.name])
                    if self.owned_identity(owned):
                        raise LabError(f"namespace still exists after delete: {owned.name}")
                self.store.unpin(owned, self.commands)
                removed.append(owned.name)
                state = state.model_copy(update={"owned": tuple(item for item in state.owned if item.name != owned.name)})
                self.store.save(state)
            except (OSError, LabError, subprocess.SubprocessError) as exc:
                errors.append(str(exc))
        if errors:
            self.store.save(state.model_copy(update={"phase": "cleanup_failed"}))
            raise LabError("; ".join(errors))
        self.store.clear()
        return {"status": "absent", "removed": removed}

    def endpoint_ip(self, state, name, family):
        endpoint = next(e for link in state.config.links for e in (link.a, link.b) if e.namespace == name)
        return str(ip_interface(getattr(endpoint, f"ipv{family}_cidr")).ip)

    def transfer(self, state, client, family, direction):
        server_ns = state.config.verification.server
        address = self.endpoint_ip(state, server_ns, family)
        server = subprocess.Popen(["ip", "netns", "exec", server_ns, "iperf3", f"-{family}", "-s", "-1",
                                   "-B", address, "-p", "5284", "-J"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            process = OwnedProcess(pid=server.pid, start_ticks=process_start_ticks(server.pid), namespace=server_ns)
            self.store.save(state.model_copy(update={"processes": (process,)}))
            deadline = time.monotonic() + 5
            while ":5284" not in self.commands.inside(server_ns, "ss", "-H", "-ltn"):
                if server.poll() is not None or time.monotonic() >= deadline:
                    raise LabError("iperf3 server did not become ready")
                time.sleep(0.05)
            args = ["iperf3", f"-{family}", "-c", address, "-p", "5284", "-n", "1048576", "-J", "--connect-timeout", "2000"]
            if direction == "download":
                args.append("-R")
            result = json.loads(self.commands.inside(client, *args, timeout=20))
            output, errors = server.communicate(timeout=5)
            server_result = json.loads(output)
            if server.returncode or result.get("error") or server_result.get("error"):
                raise LabError(f"iperf3 reported failure: {result.get('error') or server_result.get('error') or errors}")
            received = result["end"]["sum_received"]["bytes"]
            if received < 1048576:
                raise LabError(f"incomplete {direction} delivery: {received} bytes")
            return {"client": client, "address_family": family, "direction": direction,
                    "received_bytes": received, "client_result": result, "server_result": server_result}
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.communicate(timeout=2)
            # Clear only after the child has exited; an interrupted runner retains its journal.
            self.store.save(state.model_copy(update={"processes": ()}))

    def verify(self):
        state = self.state()
        if state.phase != "ready" or state.processes:
            raise LabError("lab is not ready or contains an interrupted tracked transfer")
        before = self.inspect()
        self.validate_snapshot(state.config, before)
        results, probes, routes = [], [], []
        for client in state.config.verification.clients:
            for family in (4, 6):
                address = self.endpoint_ip(state, state.config.verification.server, family)
                routes.append({"client": client, "family": family,
                               "route": json.loads(self.commands.ip(client, "-j", f"-{family}", "route", "get", address))})
                output = self.commands.inside(client, "ping", f"-{family}", "-n", "-c", "2", "-W", "2", address)
                probes.append({"client": client, "family": family, "output": output})
                for direction in ("upload", "download"):
                    results.append(self.transfer(state, client, family, direction))
        after = self.inspect()
        deltas = []
        for link in state.config.links:
            for endpoint in (link.a, link.b):
                if endpoint.namespace not in state.config.routers:
                    continue
                def counters(snapshot):
                    row = next(r for r in snapshot["namespaces"][endpoint.namespace]["links"] if r["ifname"] == endpoint.interface)
                    return row.get("stats64", row.get("stats"))
                old, new = counters(before), counters(after)
                rx, tx = (new[key]["bytes"] - old[key]["bytes"] for key in ("rx", "tx"))
                if rx <= 0 or tx <= 0:
                    raise LabError(f"traffic did not traverse both directions: {endpoint.namespace}/{endpoint.interface}")
                deltas.append({"namespace": endpoint.namespace, "interface": endpoint.interface, "rx_bytes": rx, "tx_bytes": tx})
        return {"status": "pass", "scope": "step-04 isolated routing and delivery; no QoS benchmark",
                "verified_at_utc": datetime.now(timezone.utc).isoformat(), "run_id": state.run_id,
                "config_sha256": state.config_sha256, "clients": list(state.config.verification.clients),
                "probes": probes, "routes": routes, "transfers": results, "router_counter_deltas": deltas,
                "before": before, "after": after}


def cleanup_isp(lab):
    from adaptive_qos.isp import cleanup_for_lab
    cleanup_for_lab(lab)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("up", "inspect", "down", "verify"))
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parents[2] / "configs/lab.yaml")
    args = parser.parse_args(argv)
    if platform.system() != "Linux" or os.geteuid() != 0:
        parser.exit(2, "error: run with sudo inside the dedicated Linux lab VM\n")
    def interrupted(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    try:
        lab = Lab()
        with lab.store.locked():
            if args.command == "up":
                config, digest = load_lab_config(args.config)
                result = lab.up(config, digest)
            else:
                result = getattr(lab, args.command)()
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, LabError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        print(f"error: {exc or 'interrupted; inspect the journal and run lab/down.sh'}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
