# Phase 3 analysis: project structure and shared data contracts

[Project README](../README.md) · [Step 03 checklist](implementation-checklist.md#step-03) · [Requirements](requirements.md#4-module-responsibilities-and-contracts) · [Linux setup](setup.md)

Prepared on 4 November 2026 as the design for **checklist step 03**, distinct from the detailed plan's step 3 about the routed network lab. The design has now been implemented and verified; see the [development guide](development.md) and [verification record](environments/phase-03-verification.json). The recommendations below preserve the reasoning behind that implementation.

## 1. What phase 3 must achieve

The requirements identify what the project must demonstrate. The reference VM establishes that the Linux facilities exist. Phase 3 establishes the software interfaces through which the observer, classifier, estimator, policy engine, actuator, intent service and reporting components will cooperate.

A contract describes a record's fields, types, units, identity, validity and meaning. For example, a number named `bandwidth` is insufficient: it might mean observed interface throughput, estimated capacity or a requested shaper rate, and it might be expressed in bytes/s or bit/s. A valid contract makes those distinctions explicit before a later component uses the value.

The completion result should be an installable Python package, eight core record types, validated configuration, labeled example records and tests proving that invalid records are rejected. A replay of example records must work without root or Linux network mutations. Real collection, classification decisions, rate adaptation, enforcement and the dashboard belong to later checklist steps.

The most directly relevant requirement is R10/T12, independent module contracts. The design must also support the later checks for privacy, uncertainty, intent expiry, partial application, recovery and measurement provenance. Passing phase-3 model tests alone will not satisfy the complete T12 module/integration procedure.

## 2. Current repository and development environment

- The Windows checkout is `C:\projects\Adaptive-QOS`. It contains requirements, plans, step-02 scripts and five preflight safety tests.
- The reference VM is Ubuntu 24.04.5 / Python 3.12.3, with 4 vCPUs and 3 GiB RAM. Its recorded preflight passed 34 checks with a RAM warning.
- `/opt/adaptive-qos` in the VM is writable by the `qos` developer account. It currently holds the bootstrap snapshot, not a synchronized checkout of every Windows project file.
- At analysis time, step-02 changes were local and GitHub `main` contained the step-01 commit `98f75bc`. The implementation publishes the prerequisite environment and shared package; consult the README for current status.

For the current workflow, keep the Windows checkout authoritative for edits and transfer an explicit source snapshot to the VM before testing. Include package metadata, source, configs, examples and tests. Exclude `.git`, `.venv`, `work`, VM disks, SSH keys and generated builds. Record the source revision/dirty state and source hashes used by the Linux test run. If development later moves to a Git checkout in the VM, document that as the new authoritative workflow.

Create the permanent application `.venv` inside the VM as `qos`. Windows and Linux development environments must be created separately; Python documents virtual environments as disposable and not portable between locations. See [Python's venv documentation](https://docs.python.org/3.12/library/venv.html).

## 3. Proposed package layout

The items below are phase-3 outputs unless marked as existing or reserved for later work.

```text
Adaptive-QOS/
  pyproject.toml
  src/adaptive_qos/
    __init__.py
    __main__.py              # validation/example commands only at this stage
    models.py                # eight core records and small shared value types
    config.py                # parse and validate configuration
    clock.py                 # injectable clock interface and freshness helpers
  configs/
    lab.yaml
    controller.yaml
    safe-policy.yaml
  examples/contracts/
    valid/                   # examples for every record type
    invalid/                 # specific expected validation failures
    README.md                # meanings and proposed replay sequence
  tests/
    test_preflight.py        # existing; keep its path working
    unit/
      test_models.py
      test_config.py
      test_clock.py
    integration/             # reserved for later privileged lab checks
  lab/                       # preserve the existing step-02 tools
  workloads/                 # later synthetic traffic generators
  experiments/               # later scenario runner and evaluator
  ui/                        # later dashboard
  docs/
```

Use package name `adaptive-qos` for distribution metadata and `adaptive_qos` for Python imports. The `src` layout requires installation and helps expose packaging mistakes that can otherwise be hidden by importing files from the repository root. See the [Python Packaging guide](https://packaging.python.org/en/latest/discussions/src-layout-vs-flat-layout/).

`pyproject.toml` should declare the build backend, package discovery, supported Python version, runtime dependencies and test dependencies. Python 3.12 is the verified reference interpreter. My recommendation is Pydantic for the shared validation layer, PyYAML for the YAML files already proposed in the plan, and pytest/build as development dependencies. Resolve and pin versions actually tested on the VM; do not invent installed versions. Package metadata guidance is in [Writing pyproject.toml](https://packaging.python.org/en/latest/guides/writing-pyproject-toml/).

Keep future algorithm modules out of the initial public API until they exist. An import must not execute commands, require root, read a default configuration or modify the network. Preserve the standalone, standard-library preflight so checking Linux prerequisites does not depend on an already installed application environment.

Keep dependencies one-way: `models.py` defines values without importing configuration or algorithms; clock/freshness helpers and configuration validation use those values; the CLI composes them. Evaluate time-dependent and registry-dependent rules with explicit context instead of putting calls to the current system clock or a global flow registry inside model constructors. Treat configs/examples as explicit repository inputs unless they are deliberately bundled and tested as package resources.

## 4. Shared validation and record metadata

Use one validation convention across records and configurations. Pydantic's default coercion should not silently convert a string or Boolean into a bandwidth setting. Enable strict validation, reject extra fields and non-finite numbers, and validate defaults and relationships between fields. Strict JSON validation and strict Python-object validation differ for representations such as dates; test both entry paths explicitly. See [strict mode](https://pydantic.dev/docs/validation/latest/concepts/strict_mode/) and [model configuration](https://pydantic.dev/docs/validation/latest/api/pydantic/config/).

Freeze validated records and prefer tuples plus frozen nested models for collections. A frozen outer object does not prevent a nested dictionary from changing. Unvalidated construction must not be an input path for API requests, saved records or configurations. See [Pydantic's model documentation](https://pydantic.dev/docs/validation/latest/concepts/models/).

Runtime records should share a small metadata envelope:

| Field | Proposed meaning |
| --- | --- |
| `schema_version` | Version of the wire-record format; initially 1. Unsupported versions fail explicitly. |
| `record_type`, `record_id` | Identify the payload type and unique record. A discriminator prevents parsing an event as a policy. |
| `run_id` | Links observations and decisions to one experiment or interactive run. |
| `producer_instance_id`, `sequence` | Identify one producer lifetime and its ordering; do not rely on a reusable process ID alone. |
| `boot_id` | Identifies the clock domain for monotonic timestamps. |
| `created_at_utc` | Timezone-aware timestamp for logs, reports and cross-run interpretation. |
| observation/window references | Identify the actual input interval rather than only when a derived object was serialized. |

Use explicit unit suffixes: integer `*_bps` for configured rates, `*_bytes`, `*_packets`, `*_ms` for displayed durations, and `*_mono_ns` for runtime intervals/deadlines. Numeric zero is a measured zero; unavailable measurements use `null` plus a reason. A queue with zero backlog differs from a missing queue observation.

## 5. Time, freshness and restart rules

Use UTC for human-readable history and monotonic elapsed time for runtime age, cooldowns and expiry. Python's monotonic clock is unaffected by wall-clock adjustments, and only differences within the relevant clock domain are meaningful. See [Python's monotonic clock documentation](https://docs.python.org/3.12/library/time.html#time.monotonic).

Freshness should be evaluated by the consumer using the observation timestamp, current clock, maximum age and clock identity. A saved `fresh: true` flag cannot establish freshness when replayed later. Reject reversed intervals, impossible future timestamps and clock-domain mismatches rather than clamping them to a healthy age.

An injected clock lets tests advance time without real sleeps. Replay uses the recorded timeline. On a controller restart, the initial policy should revoke or require explicit revalidation of persisted temporary grants; it must not compare an old monotonic deadline with an unrelated clock origin or silently renew a priority.

Policy generations also need a producer/controller epoch. An integer generation resetting from 100 to 1 on restart must not be mistaken for either a globally older policy or an automatically authorized new writer. Define ordering within an epoch now; actuator/watchdog authority and reconciliation will be implemented in their later phases.

## 6. Eight core records

The following fields are a proposed minimum. Common metadata from section 4 is implied. Small nested records such as flow identity, observation windows, target bindings and per-resource results are appropriate; avoid substituting unrestricted dictionaries for those contracts.

### FlowWindow: what was observed

**Producer:** observer. **Consumers:** classifier, estimator, storage.

Include `flow_id`, `window_id`, device association, address family, transport protocol, endpoint tuples, gateway/namespace context, observation start/end, upload/download byte and packet deltas, and measurement quality/source. The first classifier can focus on TCP/UDP metadata; ICMP probes can use the metric contract. Declare unsupported traffic rather than inventing transport ports.

Upload/download are defined relative to the household LAN and WAN. A conntrack original/reply tuple or an interface's RX/TX labels are not automatically that orientation. Preserve tuple provenance and leave room for original/reply identities if NAT is added. A tuple reused by a new connection must receive a new flow-instance identity.

Require valid IP-family agreement, protocol-appropriate ports, a positive observation duration and nonnegative counters. Counter resets/restarts produce a flagged unavailable delta, not negative traffic or an invented zero. Derive observed rate using actual elapsed time: `8 * byte_delta / elapsed_seconds`. A coarse counter window cannot justify precise packet inter-arrival statistics. Counter-derived rate is also distinct from confirmed endpoint goodput.

### Classification: what the classifier predicted

**Producer:** classifier. **Consumers:** policy, evaluation, API views, storage.

Include `flow_id`, input `window_id`, `predicted_category`, `confidence_grade`, supporting reason codes, classifier/rule version and validity. Initial categories are `interactive_like`, `streaming_like`, `bulk_like` and `unknown`.

Define exactly what confidence means. Start with documented grades; a rule score is not automatically a calibrated probability. Insufficient or ambiguous evidence must support `unknown`.

Preserve the original prediction when a user corrects it. The later effective-treatment view can combine prediction, a separately identified correction and active intent. A user priority changes service treatment; it does not prove which application generated the traffic. Keep predicted category, corrected category and effective service class conceptually distinct.

### LinkEstimate: what is inferred about the link

**Producer:** estimator. **Consumers:** policy, dashboard, evaluation.

Include direction, observed rate/source, estimated capacity or unavailable, capacity lower bound and optional uncertainty bracket, recommended shaper rate, confidence/load state, probe health, input references and last update.

Keep these distinct: application demand, observed throughput, estimated capacity and configured shaper rate. A quiet link carrying 2 Mbit/s does not establish a 2 Mbit/s maximum. If an initial nominal rate comes from configuration, label it as operator-supplied information.

Require nonnegative observations, positive available capacity estimates, an ordered uncertainty bracket where provided, finite values and explicit low-demand/stale states. Do not claim a precise independent upload/download estimate from RTT alone. The record schema must not contain emulator truth or future change times.

### Intent: what the user wants temporarily

**Producer:** intent service/API. **Consumers:** policy, independent expiry/recovery, dashboard.

Include `intent_id`, observed flow/device selector, goal, creation/expiry, runtime deadline, requester/source, lifecycle state and cancellation information. Suggested lifecycle: pending, active, cancelled, expired and rejected.

Require a known, narrow selector; a positive duration inside the configured maximum; expiry after creation; and deterministic conflict resolution. Shape validation alone cannot prove that a flow belongs to a device: that needs a later check against the observer's flow registry. Distinguish a syntactically valid request from an accepted request and from effective kernel treatment.

Classification correction needs separate provenance and expiry even if its first representation is a small helper record. Never rewrite an evaluated prediction or offline ground-truth label to satisfy a user request.

### Policy: what the controller proposes

**Producer:** policy engine. **Consumer:** actuator; also watchdog/storage/reporting.

Include `policy_id`, issuer epoch/generation, mode, positive upload/download shaper rates, typed target bindings, per-flow service treatments, input record IDs, reasons and an explicit validity rule. Initial managed modes can be static and adaptive, with safe mode reserved for fallback. The uncontrolled comparison can be owned by the experiment runner without representing disabled shaping as a zero rate.

Use logical target IDs resolved against trusted namespace/interface bindings. A policy contains declarative rates and treatments, never arbitrary shell commands. Validate cross-field relationships and configured bounds in a pure validation function before calling an actuator.

A safe-policy configuration is a reusable template, not a saved runtime policy with a permanently stale timestamp. Managed leases and the fallback's until-replaced behavior need separate, explicit semantics. Temporary grants cannot become indefinite because a mode changed.

### ApplyResult: what actually happened

**Producer:** actuator. **Consumers:** controller, watchdog, event history.

Include requested policy ID/epoch/generation, per-resource application outcomes, read-back verification, structured errors, timing and any restoration policy/result. Track upload, download and marking independently so partial application is visible.

Distinguish rejected, apply-failed/partial, applied-unverified, verified, restored and restoration-failed outcomes. A command returning zero does not prove the installed state matches the policy. Matching installed state does not itself prove improved latency or fairness; that requires subsequent measurements.

Rollback must reference reconstructable declarative policy, not treat raw queue statistics as a command script. A failed restoration must remain a failure even if a previous application command succeeded.

### MetricSample: what was measured

**Producer:** collector/measurement endpoints. **Consumers:** estimator, dashboard, analysis.

Include metric name, value or unavailable, unit, scope/target, source, address family/direction where relevant, measurement interval, sample count and unavailable/quality reason. Fix allowed metric-name/unit combinations.

Separate endpoint goodput from interface traffic rate, endpoint loss from queue-drop counters, RTT from one-way delay, and RTT variation from a different UDP jitter definition. A zero-probe interval yields unavailable loss, not zero loss. Jain's index is unavailable when there is no comparable demand. Process CPU normalized to one logical core can exceed 100% for a process using multiple cores; do not apply one universal 0-100 rule to all metrics.

### Event: why a change happened

**Producer:** any module making a significant transition. **Consumers:** storage, API, evaluator.

Include event ID/type, producer, severity, record/policy/intent references, correlation/causation IDs, timestamp and a typed payload. Initial events should cover intent lifecycle, stale telemetry, policy proposal/application/verification, rollback and safe-state entry.

Match event type to payload shape using a tagged union. An unrestricted `dict[str, Any]` would surrender the validation guarantees this phase establishes. Preserve distinctions such as application attempted, kernel state verified and outcome evaluated. Events should connect the evidence chain from input window to decision to final observed result.

## 7. Configuration responsibilities

Keep the plan's proposed YAML names, with parsing in `config.py`. Use PyYAML's safe loader, then validate the resulting data against the appropriate configuration model. Safe loading restricts YAML object construction; schema validation remains a separate step. See [PyYAML's documentation](https://pyyaml.org/wiki/PyYAMLDocumentation). Reject duplicate keys instead of silently accepting the final occurrence, as well as unknown fields, multiple documents, wrong top-level types and unsupported schema versions.

| File | Owner and permitted information |
| --- | --- |
| `lab.yaml` | Lab runner: topology, namespace/interface bindings, addressing and emulator setup. The controller receives only a sanitized gateway-target subset, not this entire object. |
| `controller.yaml` | Controller: observation/freshness settings, configured rate bounds, cooldowns, step limits, confidence handling, heartbeat budget and maximum intent duration. Nominal starting information must be labeled as configured. |
| `safe-policy.yaml` | Recovery/actuator: a bounded declarative fallback, fair default service and no temporary grants. Its assumptions must match the documented supported envelope. |

Future bandwidth schedules and workload labels belong to runner/evaluator scenario data. File separation by itself does not hide those files from a process sharing the VM filesystem. Keep truth out of runtime model inputs, avoid importing experiment data in controller code and define the later process/file-access boundary explicitly.

Cross-configuration checks must cover `minimum <= initial <= maximum`, fallback rates inside allowed bounds, expiry/cooldown settings, unique known target bindings and heartbeat budgets. PT06's intended budget is a 2-second stale threshold plus up to 1-second watchdog polling plus up to 2 seconds to apply/verify; validate the configured sum against 5 seconds. This checks the budget, not actual scheduling performance.

An initial fallback candidate could be 15 Mbit/s download and 3 Mbit/s upload, below the declared lab minima of 20/5 Mbit/s. These are proposed settings to validate under load, not universally safe rates or achieved results. A configured fallback must not be confused with an estimate of current capacity.

An offline setup validator may inspect all three files and check their compatibility. Runtime controller construction must receive only controller settings, the fallback template and sanitized gateway bindings; never pass it the full lab/scenario object. Record original file hashes and a hash of normalized effective settings, including resolved defaults, for each component's permitted inputs. The evaluator can retain the full experiment configuration separately. Missing/invalid mandatory settings should produce a clear error before activation. At this phase, configuration validation must perform no network mutations.

## 8. A proposed record exchange

The example below is an offline contract exercise, not a working controller or a classifier evaluation.

```text
FlowWindow / MetricSample
        |            |
        v            v
Classification   LinkEstimate
        \            /
         \ + Intent /
          v        v
             Policy
                |
                v
           ApplyResult
                |
                v
              Event
```

For one synthetic fixture, create an observed-flow window and separate upload/download estimates. Add a 30-second narrow intent, then construct a bounded example policy that cites those inputs. Serialize/reload the records, construct an example apply result, and emit a correlated verification event. Advance a fake clock beyond expiry and prove that the intent no longer qualifies as active. Use explicitly simulated apply results; this exercise must not invoke `tc` or manufacture real enforcement evidence.

## 9. Implementation order and meaningful checks

| Order | Deliverable | Evidence needed before moving on |
| --- | --- | --- |
| 1 | Skeleton and `pyproject.toml` | Editable install and imports work in the VM as `qos`. Existing preflight tests remain discoverable. |
| 2 | Shared IDs, enums, units, clock and metadata | Reject invalid types/versions, reversed intervals, non-finite values and clock-domain mistakes. |
| 3 | Eight records and typed nested values | Each has a valid fixture, targeted invalid fixtures and JSON round-trip checks. |
| 4 | Configuration models and loader | Valid configs load; duplicates, unknown keys and incompatible bounds/budgets fail without subprocess/network activity. |
| 5 | Linked fixture replay | IDs and provenance connect correctly; low-demand capacity stays unavailable; correction preserves prediction; expiry works with an injected clock. |
| 6 | Distribution verification | Build a wheel, install it in a fresh environment and import it from outside the repository. Do not rely solely on editable-install success. |

Important negative cases include `true` or a numeric string used as a rate, zero/negative shaping rates, NaN/infinity, missing measurements substituted with zero, stale or wrong-boot observations, expired grants, mismatched flow/device IDs, duplicate policy generations within one epoch, and a partially applied policy falsely reported as fully verified. Some checks require later runtime registries/actuators; phase 3 should define their interfaces and test pure validation without claiming those integrations exist.

Proposed commands, to use only after the package and command handlers have been implemented and synchronized into the Linux working directory:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m adaptive_qos validate-config --config-dir configs
.venv/bin/python -m adaptive_qos check-contracts --examples examples/contracts
.venv/bin/python -m build
```

The application dependency set must be pinned from the successful test environment. Extend ignore rules for local build/test artifacts when implementing packaging. The `.venv`, local keys and VM disks stay outside source control. Version selected example data and configuration; do not commit fabricated benchmark results.

## 10. Phase-3 completion gate

Phase 3 is ready when a normal user can install the package, load valid configurations, exchange all eight record types, reject the defined invalid inputs, replay the fixture chain with deterministic time, and install/import a built wheel outside the source tree. The code must do those things without root or network changes, while the standalone privileged preflight continues to work independently.

The package, contracts, configuration and fixtures have now passed this gate. The checklist records step 03 as complete; the verification record preserves the tested source and environment.
