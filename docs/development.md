# Phase 3 development and contract verification

[Project README](../README.md) · [Design analysis](phase-03-analysis.md) · [Linux access](setup.md)

The application package provides shared records, configuration validation and a labeled synthetic replay. Its commands run as the ordinary `qos` user. The observer, classifier algorithms, estimator algorithms, real actuator, watchdog, API and dashboard are later implementation steps.

## Install in the reference VM

The Windows checkout is authoritative for edits. The VM's `/opt/adaptive-qos` directory is a source snapshot. Export and verify that snapshot before testing; do not copy Windows virtual environments into Linux.

From PowerShell 7 in the Windows checkout, using the project's Windows Python environment:

```powershell
.venv/Scripts/python.exe lab/export-source.py
scp.exe -P 2222 -i work/ubuntu-reference/id_ed25519 -o UserKnownHostsFile=work/ubuntu-reference/known_hosts work/source-sync/source.tar.gz work/source-sync/source-files.json qos@127.0.0.1:/home/qos/
```

The exporter allows only named source directories/files and rejects symlinks. It excludes Git metadata, local keys/disks under `work`, virtual environments and generated builds. Its manifest records every included file's SHA256 and the base Git revision. Uncommitted source is identified by those exact file hashes rather than a claim that the base commit contains it.

Connect using the setup guide, then run inside Ubuntu:

```bash
cd /opt/adaptive-qos
tar -xzf /home/qos/source.tar.gz -C /opt/adaptive-qos --no-same-owner
python3 lab/export-source.py --verify /home/qos/source-files.json
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements/dev.txt
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
```

The dependency files pin the resolved Python 3.12 environment, including transitive runtime packages and build tools. The Windows-only colorama dependency is marked explicitly. A fresh Windows environment can use the same files with `.venv/Scripts/python.exe`. The base Ubuntu package environment and the application `.venv` remain separate.

## Available commands

```bash
.venv/bin/python -m adaptive_qos validate-config --config-dir configs
.venv/bin/python -m adaptive_qos validate-config --config-dir configs --runtime-only
.venv/bin/python -m adaptive_qos check-contracts --examples examples/contracts --config-dir configs
.venv/bin/python -m adaptive_qos schema
.venv/bin/python -m pytest
```

The installed `adaptive-qos` console entry point accepts the same arguments. Configuration/examples are explicit repository inputs rather than hidden package resources; pass their paths when running from another directory.

`validate-config` performs offline cross-checks across the lab, controller and fallback settings. `--runtime-only` reads just controller/fallback settings. The `RuntimeConfig` object excludes the lab model and emulator setup. This input boundary does not itself provide filesystem isolation; the later service/privilege design must preserve it.

`check-contracts` validates nine records covering eight types, rejects the 16 indexed negative examples for their declared reasons and replays linked IDs, rate units, correction provenance, intent expiry, policy expiry and stale observations. Execution is explicitly simulated and makes no network changes. The output is a contract check, not a QoS benchmark.

Command success exits 0. Validation errors or invalid command arguments exit 2, with an error on stderr. For the existing standalone Linux preflight, use `sudo bash lab/preflight.sh`; it continues using the system Python independently of this package.

If a restricted Windows session cannot create pytest's default temporary directories, use an explicit directory under ignored `work`:

```powershell
.venv/Scripts/python.exe -m pytest --basetemp=work/pytest-tmp
```

Pytest clears the named temporary directory; reserve it for test scratch files.

## Contract conventions

- `schema_version`, record type, IDs, producer instance, sequence, boot/clock identity and aware UTC creation time are explicit.
- Monotonic nanoseconds establish observation windows and runtime deadlines. Freshness is evaluated at consumption with an injected clock; a saved state label cannot renew a grant.
- Configured rates are positive integer bit/s. Flow counters are unsigned 64-bit deltas; resets are unavailable with a reason. Observed traffic, capacity estimates and selected shaper rates have different fields.
- Records and nested values are frozen. JSON parsing rejects duplicate keys and non-finite constants. Export revalidates records so an unchecked model copy cannot bypass boundary validation.
- Prediction, user correction and chosen service treatment remain distinct. Registry-dependent checks receive a typed observed-flow mapping explicitly.
- Apply results track upload, download and marking independently, including errors, partial outcomes and read-back. Verified kernel state does not itself establish user-experience improvement.
- Managed policies have finite leases. Safe policies contain no temporary treatments and remain until replaced. Pure context checks enforce configured bounds, known targets, input freshness and issuer/generation ordering for new proposals.
- Flow-registry keys must match the stored flow identity. A managed policy cannot cite multiple snapshots of the same intent; the caller must reconcile them before submitting a proposal.
- Synthetic replay resolves every declared cause, rejects causal cycles and checks that classifications, estimates, decisions and events do not precede their inputs. Equal monotonic timestamps are allowed, and input file order does not determine causality.

## Build and verify distribution

```bash
.venv/bin/python -m build --no-isolation
```

Install the resulting wheel in a newly created environment, change to a directory outside the source tree, and verify imports, the console entry point, configuration validation and the example replay using absolute config/example paths. Confirm `adaptive_qos.__file__` belongs to that environment's `site-packages`. Editable installation alone does not verify distribution contents.

The committed phase-3 verification record under `docs/environments/` records the actual test environment, source hashes, dependency versions and wheel identity. Generated archives, wheelhouse, virtual environments and raw run output stay under ignored `work`/`dist` directories.
