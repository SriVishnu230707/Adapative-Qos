# Adaptive QoS

A reference-implementation project for intent-aware QoS on mixed home broadband traffic.

## Documentation

- **[Requirements and acceptance checks](docs/requirements.md):** source-traced features, module responsibilities, verification procedures and initial project targets.
- **[Linux setup guide](docs/setup.md):** reference VM access, tool installation, capability checks and troubleshooting.
- **[Verified environment manifest](docs/environments/ubuntu-24.04-virtualbox.json):** actual Ubuntu/kernel/tool versions and step-02 preflight evidence.
- **[Phase 3 design analysis](docs/phase-03-analysis.md):** proposed package structure, shared records, validation rules and completion checks.
- **[Development and contract checks](docs/development.md):** install the package, synchronize source to Ubuntu, validate configuration and replay examples.
- **[Routed lab](docs/routed-lab.md):** create, inspect, verify and clean up single-client or four-device IPv4/IPv6 topologies.
- **[Step 4 verification](docs/environments/phase-04-verification.json):** real transfer, reset, ownership and host-network preservation evidence.
- **[Phase 5 design analysis](docs/phase-05-analysis.md):** independent ISP bottlenecks, scenario timing, lifecycle changes and the required verification plan.
- **[ISP emulation guide](docs/isp-emulation.md)** covers bandwidth profiles, scenario commands and evidence export.
- **[Phase 5 verification](docs/environments/phase-05-verification.json)** records measured static and timed rates, recovery checks and the reference VM configuration.
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

Steps 01–05 are complete. The routed IPv4/IPv6 lab supports single-client and four-device configurations, with independently controlled ISP upload and download limits. Phase 5 verified 201 Linux test cases across the full suite and a targeted static-capacity rerun. Windows passed 185 tests with 16 skips. The reference Ubuntu VM uses one vCPU and 3 GiB RAM.

Workloads and the uncontrolled baseline are step 06. The observer, classifier, estimator, QoS actuator, dashboard and full QoS acceptance experiments follow.

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
