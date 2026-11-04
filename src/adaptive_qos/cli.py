"""Unprivileged configuration/contract utilities for checklist step 03."""

import argparse
import json
from typing import Sequence

from adaptive_qos import __version__
from adaptive_qos.config import load_config_bundle, load_runtime_config
from adaptive_qos.contracts import check_contracts
from adaptive_qos.models import RECORD_ADAPTER


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="adaptive-qos")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    config = commands.add_parser("validate-config", help="validate configuration without network changes")
    config.add_argument("--config-dir", default="configs")
    config.add_argument("--runtime-only", action="store_true", help="read only controller and fallback files")
    contracts = commands.add_parser("check-contracts", help="validate and replay labeled synthetic examples")
    contracts.add_argument("--examples", default="examples/contracts")
    contracts.add_argument("--config-dir", default="configs")
    commands.add_parser("schema", help="print JSON Schema for the eight record types")
    args = parser.parse_args(argv)
    try:
        if args.command == "validate-config":
            runtime = load_runtime_config(args.config_dir) if args.runtime_only else load_config_bundle(args.config_dir).runtime
            result = {"status": "pass", "scope": "runtime" if args.runtime_only else "offline_setup",
                      "effective_sha256": runtime.effective_sha256,
                      "source_files": [source.model_dump() for source in runtime.source_files], "network_changes": False}
        elif args.command == "check-contracts":
            result = check_contracts(args.examples, load_runtime_config(args.config_dir))
        else:
            result = RECORD_ADAPTER.json_schema()
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0
