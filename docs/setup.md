# Linux environment setup - step 02

[Project README](../README.md) · [Implementation checklist](implementation-checklist.md#step-02) · [Requirements](requirements.md)

This guide prepares the Linux tools and kernel capabilities required by the later QoS lab. The preflight checks capabilities; it does not run the project's QoS acceptance experiments or measure benchmark performance.

## 1. Reference environment

The project uses a dedicated Ubuntu 24.04 LTS VirtualBox VM named `adaptive-qos-ubuntu-24.04`, created on 4 November 2026. The actual OS, running kernel, package/tool versions, resources, check outcomes and script hashes belong in the [reference environment manifest](environments/ubuntu-24.04-virtualbox.json).

| Setting | Selected configuration |
| --- | --- |
| Host | Windows 11 Home Single Language, build 26200; x86-64 |
| Hypervisor | Oracle VirtualBox 7.2.10r174163 |
| Guest image | Canonical Ubuntu 24.04 LTS amd64 cloud VMDK, release build 20261017 |
| vCPUs / RAM | Current phase-3 profile: 1 vCPU / 3072 MiB RAM; phase 2 was verified with 4 vCPUs |
| Disk | Dynamically allocated VDI with 30 GiB virtual capacity |
| Firmware / adapter | EFI / virtio network adapter using VirtualBox NAT |
| SSH | Windows loopback `127.0.0.1:2222` -> guest TCP 22; public-key login as `qos` |
| Guest project files | `/opt/adaptive-qos/lab/` |
| Host VM, image and key files | Ignored `work/vms/` and `work/ubuntu-reference/` directories |

**Verified result:** 34 required capability checks passed, with one RAM-allocation warning. Five preflight safety tests passed, and a clean VM restart finished with cloud-init status `done` and no errors. The manifest records the final post-reboot run.

During phase 3, a subsequent four-vCPU boot stalled with clock-source delays before SSH became available. Retrying with one vCPU recovered startup. The [phase-3 verification record](environments/phase-03-verification.json) records the actual one-vCPU environment and a new passing standalone preflight. The phase-2 JSON is retained as its original four-vCPU historical snapshot.

The plan suggested 6-8 GB RAM as an adjustable development allocation. This host had low free memory, so initial capability verification uses 3 GiB. Preflight reports the lower allocation as a warning. Before scored experiments, check memory pressure, swap and CPU saturation and increase resources or reduce the declared workload if needed. A capability pass does not establish benchmark readiness.

An existing WSL Ubuntu 26.04 installation with kernel `6.6.87.2-microsoft-standard-WSL2` was inspected as an alternate. It is not the selected reference environment. Its inspection did not establish CAKE/IFB/enforcement support, and no WSL kernel or Windows virtualization features were changed.

## 2. Access the existing VM

Run these commands from the project root in PowerShell 7 (tested with 7.6.5). Start the VM only if it is stopped:

```powershell
& 'C:\Program Files\Oracle\VirtualBox\VBoxManage.exe' startvm 'adaptive-qos-ubuntu-24.04' --type headless
ssh.exe -i work/ubuntu-reference/id_ed25519 -p 2222 -o UserKnownHostsFile=work/ubuntu-reference/known_hosts -o StrictHostKeyChecking=accept-new qos@127.0.0.1
```

The private key is local and ignored by Git. Do not upload it, copy it into documentation or replace it with an example password. Keep host-key verification enabled. The seed enables passwordless `sudo` only for this SSH-key-controlled administrator in the dedicated lab VM; production access controls remain a later design task.

On first boot, cloud-init installs tools and runs an initial preflight. From the SSH session, inspect progress without starting a second package installer:

```bash
cloud-init status
sudo tail -n 40 /var/log/cloud-init-output.log
```

Wait for cloud-init to finish before manually rerunning installation. When finished working, shut down cleanly from the guest with `sudo shutdown -h now`; the SSH connection will close. Later restart using the Windows command above. The existing Kali VM is unrelated to this project.

For the cloud-image bootstrap directory, give the development account ownership after bootstrap finishes:

```bash
sudo chown -R qos:qos /opt/adaptive-qos
```

This was applied and a temporary write/remove check passed as `qos`. Keep ordinary source editing and the later application virtual environment under this account; use sudo for the installer and privileged namespace checks. The ownership command is scoped to the dedicated VM's project directory.

## 3. Install the Linux tools

For a fresh Ubuntu guest, copy or clone the repository there, enter its root, then run:

```bash
sudo bash lab/install-tools.sh
```

The created cloud-image VM already receives the three bootstrap files in `/opt/adaptive-qos/lab/`. From that guest use `cd /opt/adaptive-qos` before the commands in this guide. The bootstrap is a source snapshot, not an automatic sync of Windows edits. Copy changed lab files again before validating a new version; the manifest's script hashes identify exactly what ran.

| Tools | Purpose |
| --- | --- |
| `ip`, `tc` from iproute2 | Namespaces, virtual interfaces, addresses, routes and inspectable queues |
| nftables and conntrack | Stateful rules and per-flow packet/byte accounting |
| iperf3 and ping | Later throughput workloads and initial reachability probes |
| Python 3, venv and pip | Isolated Python development environments |
| ethtool, jq and sysstat | Interface inspection, JSON inspection and CPU observations |
| kmod, procps and util-linux | Kernel-module inspection, namespace-local sysctls and Linux utilities |
| Git and CA certificates | Source retrieval and version identity |

The installer uses Ubuntu's package manager and prevents a new iperf3 installation from automatically opting into a background server. It does not configure QoS on a host interface. Ubuntu cloud kernels can omit optional networking modules; if CAKE is absent, it installs `linux-modules-extra` for the **running** generic kernel. If that package cannot be obtained, fix the kernel/package mismatch and rerun; never substitute a reported capability pass.

The installer rejects WSL to keep reference setup explicit. A WSL alternative can be assessed separately with preflight, but `--require-reference` must reject it. A Python virtual environment is exercised temporarily by preflight; the permanent application `.venv` and package structure are step 03.

## 4. Run and interpret preflight

Inside the Linux guest:

```bash
cd /opt/adaptive-qos
sudo bash lab/preflight.sh --require-reference --output work/preflight/ubuntu-reference.json
python3 -m json.tool work/preflight/ubuntu-reference.json
```

From a normal repository clone, substitute its root for `/opt/adaptive-qos`. Exit code **0** means all required capability checks passed. Exit **1** means one or more checks failed/were blocked. The shell wrapper uses exit **2** for an unsupported OS or missing Python; invalid command-line arguments also exit 2. A resource warning alone is not a failure. Preserve failing reports when diagnosing setup.

The preflight performs these real operations:

1. Records Ubuntu/kernel/virtualization identity and tool/package versions; requires root for active checks.
2. Creates a disposable Python venv, verifies its pip and removes it.
3. Creates two randomly named `aqpf-*` network namespaces and connects them with a veth pair. Both ends are created inside owned namespaces.
4. Configures temporary test addresses, tests IPv4/IPv6 between namespaces and checks namespace-local forwarding sysctls.
5. Installs and reads back CAKE, FQ-CoDel, HTB and netem queues on a temporary interface; creates an IFB device.
6. Installs a temporary stateful nftables rule, enables accounting inside the test namespace and verifies packet/byte counters on a newly generated ICMP flow.
7. Records interfaces and IPv4/IPv6 routes, then deletes only namespaces whose creation succeeded and verifies their names are gone.

The preflight does not change the Windows network, guest main-namespace routes/firewall/sysctls or any pre-existing namespace. Linux may automatically load required kernel modules, which remain available afterward. Queue creation/read-back proves capability, not correct shaping accuracy or fairness; later implementation steps test those effects under load.

Run preflight again after changes to the kernel, VM resources, tools or scripts. Use a fresh output filename to retain earlier evidence. For the reference lab an IPv6 failure is a failed preflight; any later conditional IPv6 exception must include the failed check and an explicit scope limitation under R07.

## 5. Save and review the manifest

Copy a successful report back to the Windows project, preserving the actual measurements. For example, from Windows PowerShell:

```powershell
scp.exe -P 2222 -i work/ubuntu-reference/id_ed25519 -o UserKnownHostsFile=work/ubuntu-reference/known_hosts qos@127.0.0.1:/opt/adaptive-qos/work/preflight/ubuntu-reference.json work/ubuntu-reference/latest-preflight.json
```

The versioned reference snapshot in `docs/environments/` also records hypervisor/image provenance and the verified image checksum. It is evidence for a particular run, not a template with guessed versions. Preserve raw failure reports locally in `work/`; never paste secrets into the manifest. The nine project performance targets and T01-T16 acceptance procedures in the requirements document remain unrun until their corresponding components and experiments exist.

## 6. Recreate the environment

Use [Canonical's Ubuntu 24.04 release images](https://cloud-images.ubuntu.com/releases/noble/release/). The image used here was `ubuntu-24.04-server-cloudimg-amd64.vmdk` with SHA256 `9a1e3959c9f4ad9d4752101fd4ff9a43191fb7d72e61fa20e033f9fd235ce794`. Verify the download against the accompanying `SHA256SUMS`; the release pointer can change, so compare its hash/build with the recorded manifest before claiming an identical image. A mismatch must stop provisioning.

A conventional fresh Ubuntu 24.04 Server ISO installation in a dedicated VirtualBox VM is also an acceptable setup route: allocate resources, install Ubuntu, obtain this repository and run the installer/preflight above. Record its different image and actual versions as a new environment rather than calling it this exact snapshot.

To reproduce the cloud-image route on Windows, use a working Python 3 installation and OpenSSH client. In a fresh ignored work directory, download/verify the VMDK, generate a dedicated Ed25519 key and build a NoCloud seed:

```powershell
New-Item -ItemType Directory -Path work/ubuntu-reference -Force | Out-Null
ssh-keygen.exe -t ed25519 -N '' -C adaptive-qos-local-lab -f work/ubuntu-reference/id_ed25519
icacls.exe work/ubuntu-reference/id_ed25519 /inheritance:r /grant:r "$($env:USERNAME):(F)"
python -m pip install --target work/vm-tools pycdlib==1.22.0
python lab/create-seed.py --public-key work/ubuntu-reference/id_ed25519.pub --output work/ubuntu-reference/seed.iso
```

`create-seed.py` is a provisioning helper; pycdlib is not a runtime QoS dependency. It accepts only a public `.pub` key, sets SSH-key-only access, and copies the current installer/preflight files into the guest for first-boot execution. It refuses to overwrite an existing seed. Cloud-init reads the `CIDATA` volume according to its [NoCloud documentation](https://docs.cloud-init.io/en/latest/reference/datasources/nocloud.html#source-2-drive-with-labeled-filesystem).

In [VirtualBox](https://docs.oracle.com/en/virtualization/virtualbox/7.2/user/vboxmanage.html), create a **new** VM with the settings in section 1. Clone the downloaded VMDK to a VDI, enlarge its virtual capacity to 30720 MiB, attach it on SATA port 0 and attach the seed ISO as a DVD on port 1. Set EFI, disk boot, NAT/virtio and the loopback-only SSH forwarding rule `aqos-ssh,tcp,127.0.0.1,2222,,22`. Avoid reusing an existing VM/disk/SSH port. Ubuntu cloud-init grows the guest filesystem on first boot and runs the bootstrap. Record any changed allocations in the new manifest.

## 7. Troubleshooting and completion

| Symptom | Action |
| --- | --- |
| Windows says WSL access denied | That does not establish a Linux capability failure. Use the reference VM; inspect WSL separately with appropriate host access if needed. |
| SSH banner timeout immediately after first start | Allow the guest boot/cloud-init to progress. Inspect `work/ubuntu-reference/serial.log` or the VirtualBox console. A listening NAT port alone does not mean sshd is ready. |
| Package manager is busy | Wait for cloud-init/apt to finish. Do not delete package locks. |
| `Operation not permitted` during namespace creation | Run with sudo in the dedicated guest. Containers may lack network/mount privileges even with UID 0. |
| CAKE, IFB or another qdisc is unavailable | Inspect the running kernel and matching module package. Repair/install modules, rerun preflight and keep the failed report. |
| nftables/conntrack check fails | Inspect the exact failed command and kernel support. The preflight intentionally does not rewrite the main guest firewall to make a check pass. |
| Namespaces remain after a forced kill | Inspect the exact `aqpf-*` names printed/recorded by that run and `sudo ip netns pids NAME`; remove only those verified owned names. Never use a wildcard deletion of all namespaces. |
| VM is sluggish or swaps | Free host resources or change the VM allocation with the guest shut down. Rerun preflight and record the changed environment before experiments. |

Step 02 is complete only after the reference preflight passes, the manifest is recorded, and namespace interface/route inspection and cleanup are verified. This establishes the environment for step 03's application structure and step 04's routed client-gateway-ISP-server lab.
