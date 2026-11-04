#!/usr/bin/env python3
"""Build an SSH-key-only NoCloud seed for a new, dedicated project VM.

Provisioning-only dependency: pycdlib==1.22.0. No credentials are embedded in source.
"""

import argparse
import io
import json
from pathlib import Path
import sys
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-key", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "work/vm-tools"))
    try:
        import pycdlib
    except ImportError:
        parser.error("Install provisioning helper: python -m pip install --target work/vm-tools pycdlib==1.22.0")
    public_key = args.public_key.read_text(encoding="utf-8").strip()
    if not public_key.startswith("ssh-ed25519 ") or "PRIVATE KEY" in public_key or "\n" in public_key:
        parser.error("Supply the one-line .pub file from an Ed25519 key, never a private key")
    if args.output.exists():
        parser.error("Output already exists; choose a fresh seed filename/VM instance")

    files = [{"path": f"/opt/adaptive-qos/lab/{name}", "permissions": "0644", "content": (root / "lab" / name).read_text(encoding="utf-8")} for name in ("install-tools.sh", "preflight.sh", "preflight.py")]
    cloud_config = {
        "users": [{"name": "qos", "groups": ["sudo"], "shell": "/bin/bash", "lock_passwd": True, "sudo": "ALL=(ALL) NOPASSWD:ALL", "ssh_authorized_keys": [public_key]}],
        "ssh_pwauth": False, "disable_root": True,
        "write_files": files,
        "runcmd": [
            ["bash", "/opt/adaptive-qos/lab/install-tools.sh"],
            ["bash", "/opt/adaptive-qos/lab/preflight.sh", "--require-reference", "--output", "/opt/adaptive-qos/work/preflight/ubuntu-reference.json"],
        ],
    }
    # JSON is valid YAML and avoids interpolating keys or script bodies into shell code.
    user_data = ("#cloud-config\n" + json.dumps(cloud_config, indent=2) + "\n").encode()
    meta_data = f"instance-id: adaptive-qos-{uuid.uuid4().hex}\nlocal-hostname: adaptive-qos-lab\n".encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image = pycdlib.PyCdlib()
    image.new(vol_ident="CIDATA", joliet=3, rock_ridge="1.09")
    try:
        for data, iso_name, real_name in ((user_data, "USER_DAT.;1", "user-data"), (meta_data, "META_DAT.;1", "meta-data")):
            image.add_fp(io.BytesIO(data), len(data), iso_path="/" + iso_name, joliet_path="/" + real_name, rr_name=real_name)
        image.write(str(args.output))
    finally:
        image.close()
    print(f"Created {args.output}; contains public key and bootstrap scripts, no private key")


if __name__ == "__main__":
    main()
