#!/usr/bin/env bash
set -euo pipefail
if [[ "$(uname -s)" != Linux || "${EUID}" -ne 0 ]]; then
  printf '%s\n' 'Run with sudo inside the dedicated Ubuntu lab VM.' >&2
  exit 2
fi
task_script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
task_project_dir="$(dirname -- "${task_script_dir}")"
task_python="${task_project_dir}/.venv/bin/python"
if [[ ! -x "${task_python}" ]]; then
  printf '%s\n' 'Project .venv is missing; follow docs/development.md first.' >&2
  exit 2
fi
exec "${task_python}" -m adaptive_qos.lab "$@"
