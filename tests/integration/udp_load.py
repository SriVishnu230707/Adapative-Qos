"""Linux namespace benchmark helper with paced UDP segmentation and interval counts."""

import argparse
import json
import socket
import struct
import time


PAYLOAD_BYTES = 1400
SEGMENTS = 8
SECOND_NS = 1_000_000_000
MAX_PACING_DEBT_NS = 5_000_000
# Linux UAPI values from <linux/udp.h>.
UDP_SEGMENT = 103
UDP_GRO = 104


def send(sock, start_ns, end_ns, offer_bps, counts):
    payload = bytes(PAYLOAD_BYTES * SEGMENTS)
    segment = [(socket.IPPROTO_UDP, UDP_SEGMENT, struct.pack("=H", PAYLOAD_BYTES))]
    period_ns = len(payload) * 8 * SECOND_NS // offer_bps
    deadline = start_ns
    while True:
        now = time.monotonic_ns()
        if now < deadline:
            time.sleep((deadline - now) / SECOND_NS)
            continue
        if now >= end_ns:
            return
        sent = sock.sendmsg([payload], segment)
        counts[(now - start_ns) // SECOND_NS] += sent
        # Keep short scheduling delays from permanently reducing the offered rate.
        deadline = max(deadline + period_ns, now - MAX_PACING_DEBT_NS)


def receive(sock, start_ns, end_ns, counts):
    while time.monotonic_ns() < end_ns:
        try:
            size = len(sock.recv(65536))
        except TimeoutError:
            continue
        now = time.monotonic_ns()
        if start_ns <= now < end_ns:
            counts[(now - start_ns) // SECOND_NS] += size


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("send", "receive"))
    parser.add_argument("address")
    parser.add_argument("port", type=int)
    parser.add_argument("start_ns", type=int)
    parser.add_argument("seconds", type=int)
    parser.add_argument("offer_bps", type=int)
    args = parser.parse_args()
    family = socket.AF_INET6 if ":" in args.address else socket.AF_INET
    counts = [0] * args.seconds
    end_ns = args.start_ns + args.seconds * SECOND_NS
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        if args.role == "receive":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 212992)
            sock.setsockopt(socket.IPPROTO_UDP, UDP_GRO, 1)
            sock.settimeout(0.05)
            sock.bind((args.address, args.port))
            print("ready", flush=True)
            receive(sock, args.start_ns, end_ns, counts)
        else:
            sock.connect((args.address, args.port))
            send(sock, args.start_ns, end_ns, args.offer_bps, counts)
    print(json.dumps({"window_bytes": counts, "start_ns": args.start_ns, "end_ns": end_ns}))


if __name__ == "__main__":
    main()
