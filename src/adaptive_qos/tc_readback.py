"""Read-only RTM_GETTCLASS inspection, using Linux UAPI tcmsg/HTB layouts.

Some iproute2 builds do not implement JSON for HTB classes. Read exact kernel
byte/s values instead of parsing the rounded human-readable display.
"""

import argparse
import json
import platform
import socket
import struct


def attributes(data):
    values, offset = {}, 0
    while offset + 4 <= len(data):
        size, kind = struct.unpack_from("=HH", data, offset)
        if size < 4 or offset + size > len(data):
            raise ValueError("truncated netlink attribute")
        kind &= 0x3fff
        payload = data[offset+4:offset+size]
        if kind in values and payload:
            raise ValueError("duplicate netlink attribute")
        values[kind] = payload
        offset += (size + 3) & ~3
    if offset < len(data) and any(data[offset:]):
        raise ValueError("invalid netlink padding")
    return values


def decode_class(body, ifindex, handle):
    if len(body) < 20:
        raise ValueError("truncated tcmsg")
    _, index, observed, _, _ = struct.unpack_from("=B3xiIII", body)
    if index != ifindex or observed != handle:
        return None
    attrs = attributes(body[20:])
    if attrs.get(1, b"").rstrip(b"\0") != b"htb":
        raise ValueError("requested class is not HTB")
    options = attributes(attrs.get(2, b""))
    params = options.get(1, b"")
    if len(params) < 44:
        raise ValueError("HTB parameters are missing or truncated")
    rate = struct.unpack_from("=I", params, 8)[0]
    ceiling = struct.unpack_from("=I", params, 20)[0]
    for key, name in ((6, "rate"), (7, "ceiling")):
        if key in options:
            if len(options[key]) != 8:
                raise ValueError("invalid HTB 64-bit rate")
            value = struct.unpack("=Q", options[key])[0]
            if name == "rate":
                rate = value
            else:
                ceiling = value
    return {"rate_bytes_per_second": rate, "ceil_bytes_per_second": ceiling,
            "source": "NETLINK_ROUTE/RTM_GETTCLASS"}


def read_class(ifindex, classid):
    if platform.system() != "Linux":
        raise ValueError("Linux is required for netlink class read-back")
    if not 0 < ifindex <= 2**31-1:
        raise ValueError("invalid interface index")
    major, minor = classid.split(":")
    handle = (int(major, 16) << 16) | int(minor, 16)
    body = struct.pack("=B3xiIII", 0, ifindex, 0, 0, 0)
    request = struct.pack("=IHHII", 16+len(body), 42, 0x301, 1, 0) + body
    found = []
    with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, socket.NETLINK_ROUTE) as channel:
        channel.settimeout(2)
        channel.bind((0, 0))
        channel.sendto(request, (0, 0))
        for _ in range(256):
            data, _, flags, sender = channel.recvmsg(1_048_576)
            if sender[0] != 0 or flags & socket.MSG_TRUNC:
                raise ValueError("invalid or truncated kernel netlink response")
            offset = 0
            while offset + 16 <= len(data):
                size, kind, msg_flags, sequence, _ = struct.unpack_from("=IHHII", data, offset)
                if size < 16 or offset+size > len(data) or sequence != 1 or msg_flags & 0x10:
                    raise ValueError("invalid or interrupted netlink dump")
                payload = data[offset+16:offset+size]
                if kind == 2:
                    error = struct.unpack_from("=i", payload)[0]
                    if error:
                        raise OSError(-error, "RTM_GETTCLASS failed")
                elif kind == 3:
                    if len(payload) >= 4 and struct.unpack_from("=i", payload)[0]:
                        raise ValueError("netlink dump did not complete")
                    if len(found) != 1:
                        raise ValueError("requested HTB class was not found uniquely")
                    return found[0]
                elif kind == 40:
                    decoded = decode_class(payload, ifindex, handle)
                    if decoded:
                        found.append(decoded)
                offset += (size+3) & ~3
        raise ValueError("netlink dump exceeded the message bound")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ifindex", type=int)
    parser.add_argument("classid")
    args = parser.parse_args()
    try:
        print(json.dumps(read_class(args.ifindex, args.classid)))
    except (OSError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()
