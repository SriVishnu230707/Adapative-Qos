#!/usr/bin/env bash
set -euo pipefail
if [[ "$(uname -s)" != Linux || "${EUID}" -ne 0 ]]; then
  printf '%s\n' 'Run with sudo inside the dedicated Ubuntu lab VM.' >&2
  exit 2
fi
task_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "${task_script_dir}/../.venv/bin/python" -m adaptive_qos.isp "$@"
