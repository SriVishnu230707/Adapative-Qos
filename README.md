# Adaptive QoS

A reference-implementation project for intent-aware QoS on mixed home broadband traffic.

## Documentation

- **[Requirements and acceptance checks](docs/requirements.md):** source-traced features, module responsibilities, verification procedures and initial project targets.
- **[Linux setup guide](docs/setup.md):** reference VM access, tool installation, capability checks and troubleshooting.
- **[Verified environment manifest](docs/environments/ubuntu-24.04-virtualbox.json):** actual Ubuntu/kernel/tool versions and step-02 preflight evidence.
- **[Phase 3 design analysis](docs/phase-03-analysis.md):** proposed package structure, shared records, validation rules and completion checks.
- **[Development and contract checks](docs/development.md):** install the package, synchronize source to Ubuntu, validate configuration and replay examples.
- **[Phase 3 verification](docs/environments/phase-03-verification.json):** Linux/Windows test results, source hashes and fresh-wheel installation evidence.
- **[Implementation checklist](docs/implementation-checklist.md):** 16 ordered build steps with tasks, expected outputs and completion checks.
- **[Detailed implementation plan](docs/implementation-plan.md):** architecture, module design, algorithms, experiments and acceptance evidence.
- **[Computer networking learning map](docs/cn-learning/README.md):** a secondary README containing 67 connected topics, practical exercises, self-checks and project mappings.
- **[Interactive learning mind map](docs/cn-learning/mindmap.html):** download the file and open it in a browser for clickable branches and browser-local learning progress.

## Intended project scope

- Classify broad traffic categories without reading private payloads.
- Estimate changing link conditions and apply inspectable queueing and shaping policies.
- Support temporary user priorities while preserving fairness and bulk-transfer progress.
- Verify outcomes with reproducible baseline comparisons and reversible policy changes.

## Current status

Steps 01-03 are complete: requirements, a verified Ubuntu environment, and an installable Python package with eight validated records, separate runtime/lab configuration, deterministic example replay and tests. Phase 3 passed 119 tests on Ubuntu and 118 tests on Windows, with one Windows symlink-permission skip. A built wheel also passed checks in a fresh environment outside the repository. The VM currently uses one vCPU and 3 GiB RAM after an early-boot clock stall at four vCPUs; the historical phase-2 manifest retains its original four-vCPU measurements.

The traffic observer, classifier/estimator algorithms, real actuator and dashboard are the next implementation work. The example apply results are simulated; QoS benchmark results have not been produced.

## Run the phase-3 checks

After creating a Python 3.12 virtual environment, install the pinned development dependencies and this package:

```bash
python -m pip install -r requirements/dev.txt
python -m pip install --no-build-isolation --no-deps -e .
python -m pytest
python -m adaptive_qos validate-config --config-dir configs
python -m adaptive_qos check-contracts --examples examples/contracts --config-dir configs
```

The commands above run unprivileged. See the development guide for VM synchronization and distribution checks. Linux prerequisite verification remains available separately through `sudo bash lab/preflight.sh`.
