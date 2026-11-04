#!/usr/bin/env bash
# Install the step-02 tools in a dedicated Ubuntu guest, not on the Windows host.
set -euo pipefail

if [[ "$(uname -s)" != Linux || "${EUID}" -ne 0 ]]; then
  printf '%s\n' 'Run inside the dedicated Ubuntu VM: sudo bash lab/install-tools.sh' >&2
  exit 2
fi
. /etc/os-release
if [[ "${ID:-}" != ubuntu ]]; then
  printf '%s\n' 'This installer supports Ubuntu only.' >&2
  exit 2
fi
if [[ "$(uname -r)" == *[Mm]icrosoft* ]]; then
  printf '%s\n' 'The reference setup requires an Ubuntu VM kernel. WSL is an alternate to assess separately.' >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
printf 'iperf3 iperf3/start_daemon boolean false\n' | debconf-set-selections
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates git python3 python3-venv python3-pip \
  iproute2 nftables conntrack iperf3 iputils-ping ethtool jq sysstat \
  kmod procps util-linux

# Ubuntu cloud images can omit optional networking modules. Match the RUNNING kernel.
if [[ "$(uname -r)" == *-generic ]] && ! modinfo sch_cake >/dev/null 2>&1; then
  apt-get install -y --no-install-recommends "linux-modules-extra-$(uname -r)"
fi
printf '%s\n' 'Tools installed. Run preflight to establish actual kernel capabilities.'
