#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != Linux ]]; then
  printf '%s\n' 'Linux is required. Run this script inside the Ubuntu lab VM.' >&2
  exit 2
fi
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' 'python3 is missing. First run: sudo bash lab/install-tools.sh' >&2
  exit 2
fi
task_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "${task_script_dir}/preflight.py" "$@"
