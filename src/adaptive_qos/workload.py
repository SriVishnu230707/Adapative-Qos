"""Synthetic endpoints for the isolated baseline experiment."""

import argparse
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import select
import socket
import struct
import time


SECOND = 1_000_000_000
SEQUENCE = struct.Struct("!Q")


def record(event, *, flush=False, **fields):
    print(json.dumps({"event": event, "monotonic_ns": time.monotonic_ns(), **fields}), flush=flush)


def wait_until(deadline):
    time.sleep(max(0, deadline - time.monotonic_ns()) / SECOND)


def read_timeline(path):
    deadline = time.monotonic() + 60
    while not path.is_file():
        if time.monotonic() >= deadline:
            raise TimeoutError("runner did not start the experiment")
        time.sleep(0.05)
    return json.loads(path.read_text(encoding="utf-8"))


def udp_listener(host, port, timeline, *, echo):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        sock.bind((host, port))
        sock.settimeout(0.2)
        record("ready", flush=True)
        end_ns = None if echo else read_timeline(timeline)["end_ns"] + 2 * SECOND
        while end_ns is None or time.monotonic_ns() < end_ns:
            try:
                packet, peer = sock.recvfrom(65536)
            except TimeoutError:
                continue
            if len(packet) < SEQUENCE.size:
                continue
            if echo:
                sock.sendto(packet, peer)
            else:
                record("received", sequence=SEQUENCE.unpack_from(packet)[0], payload_bytes=len(packet))


def udp_source(host, port, timeline, interval_ns, size, *, echo):
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    padding = bytes(size - SEQUENCE.size)
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        sock.connect((host, port))
        sock.setblocking(False)
        record("ready", flush=True)
        schedule = read_timeline(timeline)
        start_ns, end_ns = schedule["start_ns"], schedule["end_ns"]
        readers = [sock] if echo else []
        deadline = start_ns
        finish = end_ns + SECOND if echo else end_ns
        while time.monotonic_ns() < finish:
            now = time.monotonic_ns()
            if now >= end_ns:
                deadline = end_ns
            if deadline < end_ns and now >= deadline:
                # Skip missed slots without bursting after a scheduling pause.
                sequence = (now - start_ns) // interval_ns
                sock.send(SEQUENCE.pack(sequence) + padding)
                record("sent", sequence=sequence, payload_bytes=size)
                deadline = start_ns + (sequence + 1) * interval_ns
            wake = min(deadline, finish) if deadline < end_ns else finish
            if select.select(readers, [], [], max(0, wake - time.monotonic_ns()) / SECOND)[0]:
                packet = sock.recv(65536)
                if len(packet) == size:
                    record("received", sequence=SEQUENCE.unpack_from(packet)[0], payload_bytes=len(packet))


def tv_server(host, port, size):
    payload = bytes(size)

    class SegmentHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/segment":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(size))
            self.end_headers()
            try:
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError):
                pass  # The requesting device timed out.

        def log_message(self, *_):
            pass

    class SegmentServer(HTTPServer):
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET

    with SegmentServer((host, port), SegmentHandler) as server:
        record("ready", flush=True)
        server.serve_forever()


def tv_client(host, port, start_ns, end_ns, interval_ns, size):
    for scheduled in range(start_ns, end_ns, interval_ns):
        wait_until(scheduled)
        started = time.monotonic_ns()
        deadline = scheduled + interval_ns
        received = 0
        error = None
        if started >= min(deadline, end_ns):
            error = "request slot elapsed before start"
        else:
            connection = HTTPConnection(host, port, timeout=interval_ns / SECOND)
            try:
                connection.request("GET", "/segment")
                with connection.getresponse() as response:
                    if response.status != 200:
                        raise HTTPException(f"HTTP status {response.status}")
                    while chunk := response.read1(64 * 1024):
                        received += len(chunk)
                        if time.monotonic_ns() > deadline:
                            break
                if received != size:
                    error = "incomplete segment"
            except (OSError, HTTPException) as exc:
                error = str(exc)
            finally:
                connection.close()
        finished = time.monotonic_ns()
        record("segment", scheduled_ns=scheduled, started_ns=started, finished_ns=finished,
               deadline_ns=deadline, received_bytes=received,
               met_deadline=received == size and finished <= deadline, error=error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("echo-server", "call-listener", "call-source", "echo-source", "tv-server", "tv-client"))
    parser.add_argument("host")
    parser.add_argument("port", type=int)
    parser.add_argument("--start", type=int)
    parser.add_argument("--end", type=int)
    parser.add_argument("--interval", type=int)
    parser.add_argument("--size", type=int)
    parser.add_argument("--timeline", type=Path)
    args = parser.parse_args()
    if args.role in ("echo-server", "call-listener"):
        udp_listener(args.host, args.port, args.timeline, echo=args.role == "echo-server")
    elif args.role in ("call-source", "echo-source"):
        udp_source(args.host, args.port, args.timeline, args.interval, args.size, echo=args.role == "echo-source")
    elif args.role == "tv-server":
        tv_server(args.host, args.port, args.size)
    else:
        tv_client(args.host, args.port, args.start, args.end, args.interval, args.size)


if __name__ == "__main__":
    main()
