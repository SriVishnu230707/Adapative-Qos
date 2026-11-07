# Phase 5 analysis: independent ISP bandwidth emulation

Prepared on 5 November 2026 from the current phase-4 implementation. **Phase 5 means checklist step 05. This is a design and verification plan; no phase-5 emulator or scenario runner has been implemented by this analysis.**

This document preserves the design review. The operating guide describes the implemented interface. [ISP emulation guide](isp-emulation.md)

[Project README](../README.md) · [Checklist](implementation-checklist.md#step-05) · [Requirements](requirements.md) · [Routed lab](routed-lab.md)

## 1. Purpose and completion claim

Phase 4 established a functioning packet path. Phase 5 must make that path behave like a provider connection with controlled, independently changing upload and download capacity.

The intended completion claim is: **changing a runner-owned ISP profile produces the corresponding measured capacity constraint, while routing, reset and ownership protections remain correct and the future controller is excluded from live ground-truth inputs.**

This prepares later estimation and QoS experiments. It does not establish classifier accuracy, adaptive control, latency improvement, fairness, or the full T03 capacity-estimator acceptance result. The basic phase-5 traffic generator is a validation instrument; the application-like workloads and uncontrolled baseline belong to step 06.

## 2. What exists and what is missing

| Existing component | Current behavior | Phase-5 work |
| --- | --- | --- |
| `configs/lab.yaml` and `lab-multi.yaml` | Declare initial 100/20 Mbit/s rates, minimum supported 20/5 Mbit/s capacities and a 1000-packet queue setting | Turn these declarations into verified ISP queue configuration. |
| `EmulatorProfile` in `config.py` | Checks positive values and the initial/minimum envelope | Add explicit ISP bindings, bounded scenario events and any required queue/burst parameters. |
| `Lab.up()` | Creates interfaces, addresses, bridges, routes and forwarding | Keep topology setup separate from owned ISP-profile installation. |
| `Lab.inspect()` | Records qdisc state | Add class/leaf detail and comparisons with the requested ISP settings. |
| `Lab.verify()` | Completes short 1 MiB transfers and checks routing/counters | Add sustained, interval-based capacity validation rather than using short delivery tests as rate measurements. |
| `StateStore` and ownership pins | Protect namespace identity across commands | Reuse those checks before every ISP mutation and during scenario cancellation/reset. |
| `load_runtime_config()` | Reads controller and fallback files only | Preserve that API boundary and prepare enforceable access restrictions around live scenario truth. |

At the time of this review, the lab lacked the ISP profile command, applied emulator queues and timed capacity worker.

## 3. Put each bottleneck on the correct interface

Traffic directions are defined from the household's perspective. An egress queue acts when traffic leaves an interface.

```text
Upload:   client -> gateway -> qos-isp:gw0 -> qos-isp:srv0 -> server
                                                    ^
                                         upload limit on srv0 egress

Download: server -> qos-isp:srv0 -> qos-isp:gw0 -> gateway -> client
                                           ^
                                download limit on gw0 egress
```

| Function | Namespace | Egress interface | Owner |
| --- | --- | --- | --- |
| Emulated download capacity | `qos-isp` | `gw0` | Scenario runner |
| Emulated upload capacity | `qos-isp` | `srv0` | Scenario runner |
| Future chosen download shaping | `qos-gw` | `lan0` | QoS controller/actuator |
| Future chosen upload shaping | `qos-gw` | `wan0` | QoS controller/actuator |

The ISP establishes the experimental constraint. The gateway will later choose how to respond. For example, an ISP download limit of 20 Mbit/s can coexist with a stale gateway setting of 90 Mbit/s. That mismatch is a condition the future estimator must detect from observations.

Changing the gateway to 20 Mbit/s as the experiment's capacity change would collapse these two responsibilities into one. The controller would be controlling the condition it is supposed to infer.

Bindings should be explicit runner configuration, validated against the saved topology and ownership journal. Do not infer the ISP from a namespace's position in a list or allow an arbitrary CLI interface name to become a root command target. The gateway and the VM's initial network namespace must remain outside the ISP mutation allowlist.

## 4. A suitable first queue model

The existing plan proposes one HTB class with an explicit FIFO leaf on each ISP egress. HTB provides outbound shaping and configurable rate, ceiling and burst behavior. A single default class makes all unclassified traffic use the same declared capacity constraint. See the upstream [HTB manual](https://man7.org/linux/man-pages/man8/tc-htb.8.html).

Conceptually, each direction has:

```text
ISP interface
  -> owned root HTB queue
      -> one default class, rate = ceiling = desired capacity
          -> explicit FIFO leaf with a documented limit
```

The chosen FIFO models provider buffering without adding an experimental intelligent queue policy at the bottleneck. `pfifo` has a packet-count limit; `bfifo` has a byte-count limit. Their size units must remain explicit. See the [FIFO manual](https://man7.org/linux/man-pages/man8/tc-pfifo.8.html).

Install the hierarchy once, verify it, then prefer changing the owned class's rate/ceiling during events. Rebuilding the root queue on every transition can discard queued traffic, reset observations and create a different experiment. If a replacement is necessary, record it as a reset with its consequences.

Persist the handles, class IDs, leaf type, queue limit, burst settings and actual read-back. Check that existing queue state is expected before taking ownership; refuse an unexplained foreign hierarchy. Normalize the installed version's `tc` JSON units into the project's integer bit/s convention rather than assuming a formatted rate is already in those units.

### Why the queue limit is part of the experiment

The current 1000-packet setting can represent substantial delay. Assuming 1500-byte packets and a full queue:

```text
Approximate queued serialization time = packets * bytes_per_packet * 8 / bit_per_second

At 100 Mbit/s: 1000 * 1500 * 8 / 100000000 = 0.12 seconds
At  20 Mbit/s: 1000 * 1500 * 8 /  20000000 = 0.60 seconds
At   5 Mbit/s: 1000 * 1500 * 8 /   5000000 = 2.40 seconds
```

These are explanatory estimates, not measured RTT predictions. Packet sizes, headers, aggregation/offloads, bursts, actual occupancy and queues elsewhere affect the result. A constant packet-count limit produces a changing delay budget as capacity changes.

Choose whether the experiment intends a fixed packet count, fixed byte count or approximately fixed buffering time. Record that choice before comparing runs. Also record MTU and offload settings; do not claim that a software queue item always represents one fixed-size wire packet. Any offload changes must stay on owned interfaces and their CPU consequences must be measured.

## 5. Scenario configuration and timing

Use typed, versioned runner-only scenario records. Each scenario needs:

- A scenario ID, version, duration and declared topology/profile compatibility.
- An initial directional state and strictly increasing event offsets in milliseconds.
- Positive, bounded upload/download rates and explicit queue parameters.
- A deterministic generator definition or recorded seed when using variable traces.
- A cancellation/failure policy and the state to restore.

Represent a desired event as a complete directional state, then apply only the fields that changed. Reject duplicate event times, events outside the run duration, unknown keys, invalid units and values outside the declared supported envelope before changing Linux state.

Start with independent experiments:

| Experiment | Download capacity | Upload capacity | Reason |
| --- | --- | --- | --- |
| Static reference | 100 Mbit/s | 20 Mbit/s | Verify the configured starting condition. |
| Download drop/recovery | 100 -> 20 -> 100 Mbit/s | Remains 20 Mbit/s | Isolate a download-capacity change. |
| Upload drop/recovery | Remains 100 Mbit/s | 20 -> 5 -> 20 Mbit/s | Isolate an upload-capacity change. |

Changing both directions together should follow those simpler experiments. TCP acknowledgements couple the directions: unchanged upload configuration does not guarantee identical upload goodput during arbitrary competing traffic. Inspect unchanged configuration separately from measuring performance under a declared load.

### An illustrative long profile

This proposed example leaves room for later acceptance measurements:

| Time from run start | Download-drop experiment | Upload-drop experiment |
| --- | --- | --- |
| 0 s | 100/20 Mbit/s down/up | 100/20 Mbit/s down/up |
| 60 s | 20/20 Mbit/s | 100/5 Mbit/s |
| 120 s | 100/20 Mbit/s | 100/20 Mbit/s |
| 270 s | End | End |

The reduced plateau lasts 60 seconds. The restored period lasts 150 seconds. That supports future 15-second settlement exclusions, at least 30 loaded scored windows, and a 120-second recovery deadline followed by ten confirming windows. These durations are proposed scenario design choices; they are not implemented schedules or observed timings.

Phase-5 smoke tests can use shorter traces to prove application, direction mapping and cancellation. Such traces must not be reported as satisfying the later long-window acceptance procedures.

### Schedule by absolute monotonic deadlines

Calculate each deadline from a single run origin. Repeatedly sleeping for relative intervals after executing commands accumulates command latency and drifts the schedule.

For every event, save the requested offset, actual operation start, per-direction application/read-back completion, scheduling lateness, requested state, observed state and result. Keep boot identity and UTC timestamps for interpretation. Two `tc` operations are not an atomic bidirectional transaction, so record separate completion times where both directions change.

Offline analysis must use actual verified transition times. A requested drop at t=60s that is verified at t=60.4s should not be scored as though the new state existed throughout those 400 ms.

## 6. Keep ground truth out of controller inputs

There are three kinds of information:

| Information | Intended consumer |
| --- | --- |
| Actual ISP rates and future event schedule | Privileged scenario runner and offline evaluator |
| Observed throughput, latency, counters and their freshness | Runtime observer/estimator |
| Operator nominal rates, bounds and recovery assumptions | Runtime configuration, with their configured provenance retained |

The controller must infer conditions from observations. A `LinkEstimate` cannot be populated by copying the runner's true current capacity.

The existing runtime loader helps establish the intended API boundary, but it is not filesystem isolation. Public example templates and source files are also not secret. A resolved live trace should be instantiated into a private runner-owned location, with a separate restricted runtime identity/view that cannot read it. Avoid leaking the live trace through public logs, API responses, inherited file descriptors, environment variables or process information exposed to the runtime.

Keep actual transition events separate from the controller's event stream. Make the trace available for offline evaluation after the run under an explicit workflow. Fixed public example timings alone are insufficient evidence of a blind estimator evaluation; later evaluations should vary the hidden realization and check the actual runtime input boundary.

The controller does not exist yet. Phase 5 can prepare and test access restrictions using a runtime-equivalent identity; the real controller integration must be checked again when that service is built. A developer account with unrestricted sudo is not an appropriate model of a restricted runtime service.

## 7. Changes needed in the phase-4 lifecycle code

This is more than adding two `tc` commands.

### Short transactions instead of one long operation lock

`lab.py:477` currently holds `StateStore.locked()` around a complete command. That is appropriate for the existing serialized setup/verification workflow. A capacity scheduler holding that same lock for minutes would prevent inspection, cancellation and other commands. Conversely, the current verifier would block a scheduler from applying changes during transfers.

Refactor toward short state/mutation transactions plus a scenario lease identifying the active worker. Release the operation lock while waiting for deadlines or collecting traffic. Recheck boot, lab run ID, namespace pin and interface ownership before every mutation. A worker for a destroyed lab must never modify a newly created lab with reused names.

### A process registry that supports concurrent roles

`Lab.transfer()` currently writes `processes=(server,)` and later clears the tuple. That is safe within its serialized phase-4 workflow, but it would erase another registered worker if reused unchanged for concurrent scenarios.

Use role-aware registration/deregistration, retaining other entries and checking PID start time. Define worker placement explicitly: the current `OwnedProcess` expects a process in an owned network namespace, so a scheduler in the initial namespace cannot simply be inserted into that record without changing the design.

### Coordinated cleanup and cancellation

Reset must cancel the worker and prevent future events before changing/removing its queues or namespaces. Define whether ordinary scenario completion restores initial ISP settings or leaves its final verified state; expose that choice and preserve failed-run evidence.

An interrupted one-direction update must be recorded as partial. Restore the last verified ISP state if possible, or mark the run failed with the precise residual state. The gateway's safe QoS template and the ISP's experimental restore profile serve different roles and should not be conflated.

Keep the phase-4 replacement protection: namespace names alone do not establish ownership. Also verify the expected interface identity/alias and queue handles before mutating them. Refuse unexpected replacement interfaces or queue trees rather than forcing a root replacement.

## 8. Prove the emulator using sustained measurements

First measure unshaped directional headroom with enough offered demand. The current reference VM has one vCPU and 3 GiB RAM. If its unshaped path cannot exceed a proposed 100 Mbit/s setting, low delivery cannot establish that the ISP shaper is the limiting resource.

Keep CPU/memory pressure and generator capacity visible. If the reference environment cannot support the declared experiment, record the limitation and revise resources or the versioned experiment envelope deliberately. Reducing rates can require corresponding changes to nominal/bounded controller settings and fallback assumptions; do not silently keep incompatible promises.

A successful validation needs complementary evidence:

1. **Requested state:** which profile/event the runner asked to apply.
2. **Kernel read-back:** correct namespace, interface, hierarchy, class rate/ceiling and leaf limit.
3. **Queue/interface observations:** bytes, packets, drops, backlog and rate-limiter statistics.
4. **Endpoint delivery:** sustained receiver goodput over declared intervals under sufficient offered load.

No one item substitutes for the others. An HTB limit can be installed while traffic bypasses it; delivery can be low because demand or CPU is low. Endpoint goodput and qdisc-accounted traffic rate also have different byte accounting.

Use steady intervals long enough to account for bursts and transition effects. Define tolerance, window lengths and exclusions before scoring. Validate both a reasonable upper bound and achievable service under adequate load; accepting any rate below the configured ceiling would allow a completely broken link to pass.

Under the multi-device topology, a single ISP limit constrains aggregate traffic. Four devices should share the configured directional bottleneck rather than each receiving a separate full-capacity link. Include mixed IPv4/IPv6 traffic so both families are shown to cross the same intended queue.

## 9. Delay/loss is an explicit extension

The checklist says to validate any added impairment stage. A first implementation can complete bandwidth emulation with zero added delay/loss and explicitly unsupported nonzero impairment requests.

If netem is introduced, document its placement and verify composition. Installing another root qdisc can replace the rate-limiting hierarchy. Netem's manual also identifies timer limitations and receiver-ingress considerations for realistic TCP tests, so a plausible command is not enough to establish the intended model. See the [netem manual](https://man7.org/linux/man-pages/man8/tc-netem.8.html).

Measure quiet baseline delay, added one-way stages, resulting RTT and endpoint loss with defined probe timeouts. Tail drops from the provider queue must remain distinguishable from intentionally injected random loss. Confirm supported options against the installed iproute2/kernel versions instead of assuming all options from current online manuals exist in the reference VM.

## 10. Proposed implementation outputs

Only `lab/isp_profile.sh` and scenario configurations are mandated filenames by the checklist. The following is a concrete proposed organization, not a list of existing files:

| Proposed artifact | Responsibility |
| --- | --- |
| `lab/isp_profile.sh` | Thin Linux/root entry point for install, inspect, apply, scenario run/cancel and restore operations. |
| `src/adaptive_qos/isp.py` | Validated ISP bindings, owned queue operations, read-back, profile state and bounded failure behavior. |
| Typed runner-only scenario models | Version, rates, event offsets, limits, duration and restore policy. |
| `experiments/scenarios/` | Public template/generator definitions, with private resolved live traces kept separately. |
| `tests/unit/test_isp.py` | Validation, mapping, command planning, timing and ownership/failure cases without root. |
| `tests/integration/test_isp_profiles.py` | Real rates, direction changes, reset/cancellation and packet-path checks in the VM. |
| `docs/isp-emulation.md` | Queue model, command usage, calibration method and limitations. |
| `docs/environments/phase-05-verification.json` | Actual tested source/environment, results, trace/evidence identity and scope. |

Reuse shared strict-validation conventions and the existing controlled command wrapper. Do not let scenario values become shell fragments. Preserve the standalone preflight and the ability to reset the phase-4 topology.

## 11. Required test matrix

| Case | Evidence needed |
| --- | --- |
| Static 100/20 profile | Read-back, enough unshaped headroom and sustained per-direction delivery near the declared model. |
| Download-only drop/recovery | Correct `gw0` changes; upload configuration unchanged; measured transition and recovery under offered demand. |
| Upload-only drop/recovery | Correct `srv0` changes; download configuration unchanged; independent actual event times. |
| Low demand | Measured delivery follows demand; unused capacity is not claimed from throughput. |
| Four active devices | Aggregate shared bottleneck and separate device measurements. |
| IPv4, IPv6 and mixed traffic | Correct traversal and accounting without family bypass. |
| Queue bounds | Requested FIFO type/limit present, with controlled overload observations. |
| Invalid profile | Rejected before any mutation. |
| Foreign queue/interface or replaced namespace | Refusal with those resources preserved. |
| Second direction fails | Explicit partial outcome, bounded restore attempt and residual-state evidence. |
| Cancel/kill worker, then reset | No later events, stale PID/run protection and repeatable cleanup. |
| Concurrent inspection and profile activity | Useful inspection without a minutes-long global lock; no lost process/state registrations. |
| Runtime-equivalent identity | Cannot obtain private live trace through the declared supported input paths. |
| Repeat same scenario from reset | Equivalent intended conditions and recorded timing variation, without requiring identical packet traces. |

Retain both the original failures and final passing evidence, as phase 4 did for namespace-inode reuse. A shell success message and two screenshots are insufficient proof of a bandwidth model.

## 12. Recommended build order

1. Define explicit ISP bindings and strict bounded scenario/queue models.
2. Calibrate unshaped headroom and lock the initial capacity/measurement envelope.
3. Implement static installation, inspection and owned restoration for both directions.
4. Validate static rate enforcement under sustained load in both address families.
5. Refactor the operation lock/process registry, then add one timed directional scenario with actual event timing.
6. Add cancellation, partial-update recovery and reset/replacement tests.
7. Validate the second direction, multi-device aggregate behavior and the live-truth access boundary.
8. Save the actual evidence and documentation. Add optional impairment modeling only with its own validation.

Phase 5 is complete when controlled ISP conditions are real, measurable, repeatable and independent of the future QoS decision path. Later estimator tests can then ask whether the controller discovers those conditions, and later QoS experiments can ask whether its response improves the measured experience.
