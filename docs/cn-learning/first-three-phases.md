# Learn Adaptive QoS through its first three phases

Prepared on 5 November 2026 from the implementation through commit `4abac56`. This guide uses **phases 1-3 to mean implementation-checklist steps 01-03**. The longer implementation plan groups some work differently, so its chapter numbers are not interchangeable with the checklist.

[Project README](../../README.md) · [Requirements](../requirements.md) · [Setup](../setup.md) · [Development](../development.md) · [Networking learning map](README.md)

The project currently has a specified goal, a verified Linux development environment, and a tested package of shared contracts. The complete traffic observer, classifier, estimator, policy algorithm, actuator, watchdog, API and dashboard remain later work. Synthetic examples exercise their interfaces. They do not implement those components or establish improved network performance.

## 1. The idea connecting all three phases

Imagine a household with an interactive call and a large download. The eventual system should protect interactive responsiveness while retaining useful download progress. That short description leaves many decisions unresolved: which traffic metadata is permitted, how latency will be measured, how temporary priority expires, how failures recover, and what evidence would justify success.

We addressed those uncertainties in dependency order:

| Phase | Question answered | Concrete result | Confidence established |
| --- | --- | --- | --- |
| 1: requirements | What should the system do, and how will we judge it? | 21 requirements, 16 acceptance procedures and nine initial project targets | The intended result and evaluation rules are explicit. |
| 2: environment | Can the selected Linux environment perform the required operations? | A dedicated Ubuntu VM, installer, active preflight and measured environment manifest | Required kernel/tools can perform isolated capability checks. |
| 3: contracts | How will the future components exchange trustworthy data? | An installable package, eight record types, configuration validation, replay and tests | Defined shapes and selected relationships behave as specified. |

There is a fourth question still to answer through later work: does the running QoS system improve the measured experience under realistic contention and failures? The first three phases prepare that investigation.

## 2. Phase 1: translating a brief into an engineering specification

### What I did

I turned the previously reviewed case-study brief into [requirements.md](../requirements.md), assigning each requirement an ID, source reference, responsible module, acceptance procedure and planned evidence. This guide relies on that recorded traceability; it does not claim a new review of the original PDF.

The requirements cover privacy, classification uncertainty, capacity estimation, queue enforcement, fairness, temporary intent, address families, rollback, recovery, metrics, reproducibility, licenses and secrets. AI comparison is conditional on introducing AI. The initial design uses deterministic logic and does not require an LLM to forward or prioritize packets.

We also chose a bounded first version: one Linux gateway, up to four synthetic household devices, controlled virtual wired links, and independent upload/download bottlenecks. A call-like workload is explicitly synthetic. A broad prediction such as `interactive_like` does not identify an actual application.

### How requirement traceability works

Consider temporary priority:

```text
Need: a user can temporarily protect an interaction.
    -> R06: temporary service intent must preserve fairness.
    -> T06: test selection, activation, cancellation, expiry, restart and failure.
    -> PT09: verify removal within two seconds during healthy control.
    -> Evidence: request, target, expiry, policy and actual enforcement times.
```

This chain prevents a UI acknowledgement from being mistaken for successful enforcement. It also exposes a dependency: an intent feature cannot pass merely by setting a database field; its effect and removal must eventually be observed in the network.

Requirement IDs are stable references for discussion. A failing test can identify the requirement it threatens. A change in implementation can preserve the requirement while replacing a tool.

### Separate requirements, design choices and targets

These categories have different authority:

| Category | Project example | Meaning |
| --- | --- | --- |
| Required behavior | Support temporary service intent and bounded recovery | The final implementation must demonstrate it. |
| Design choice | Python, Linux CAKE, nftables and conntrack | Selected mechanisms; alternatives could satisfy the same requirement. |
| Initial project target | At least 50% median paired reduction in p95 interactive RTT in a declared congested scenario | A chosen evaluation gate, subject to documented versioning; no result has established it yet. |
| Conditional behavior | Compare AI with deterministic logic if AI is introduced | It applies only when its stated condition holds. |

Do not write a report that presents a chosen threshold as a measured result. Also preserve failed runs if targets change; moving a threshold after seeing results weakens the evaluation.

### Why metric definitions matter

An informal word such as "jitter" can hide several measurements. The initial echo workload defines it as mean absolute difference between consecutive successful RTT samples. That is RTT variation; it is not automatically a receiver's packet-arrival variation.

Likewise, interface bytes, endpoint useful-payload delivery and inferred capacity answer different questions. A high byte rate does not establish that the receiver received useful application data. Fairness also needs a declared scope: equal-service, backlogged devices are a meaningful comparison; a demand-limited call is not necessarily treated unfairly because it transfers fewer bytes than a download.

For two equally backlogged devices, the requirements use Jain's index:

```text
J = (x1 + x2)^2 / (2 * (x1^2 + x2^2))

If delivery is 50 and 50: J = 1.
If delivery is 90 and 10: J = 10000 / 16400, approximately 0.610.
```

That number still needs context. The planned one-flow-versus-eight-flow experiment asks whether opening extra flows gives a device an unfair advantage. Comparing only individual flows would miss the device-level question.

### What to learn from phase 1

Start with a measurable claim, define the conditions in which it applies, and name the evidence that could disprove it. This habit is useful in API projects, machine learning, operating systems and product experiments as well as networking.

Phase 1 produced an evaluation specification. The complete T01-T16 QoS acceptance procedures remain unrun; completing the document did not pass them.

## 3. Phase 2: turning an operating system into a verified laboratory

### What I built

The reference environment is a dedicated VirtualBox VM running Ubuntu 24.04.5, kernel `6.8.0-147-generic` and Python 3.12.3. A cloud image and a NoCloud seed provisioned the `qos` account, SSH-key access and bootstrap files. [create-seed.py](../../lab/create-seed.py) generates the seed from a public key and refuses to overwrite an existing output.

The [installer](../../lab/install-tools.sh) installs the Linux tools. The [preflight](../../lab/preflight.py), reached through [preflight.sh](../../lab/preflight.sh), performs actual operations and records a JSON report. The [environment manifest](../environments/ubuntu-24.04-virtualbox.json) preserves the measured versions and outcomes.

Windows remains the authoritative source checkout. Linux has its own source snapshot and virtual environment. The exporter transfers allowed source files with SHA256 hashes, leaving local VM disks, keys, virtual environments and Git metadata out of the snapshot.

### Why there are two kinds of isolation

A VM gives the lab a separate guest operating system. Inside Linux, network namespaces give different simulated network nodes separate networking contexts. Namespaces isolate devices, protocol stacks, routing tables and firewall rules; veth pairs connect them. They share the guest kernel and host resources, so they do not reproduce independent physical-router CPU costs. See the [Linux namespace manual](https://man7.org/linux/man-pages/man7/network_namespaces.7.html).

The planned routed topology is:

```text
client <-> QoS gateway <-> ISP emulator <-> server
```

That four-node path is declared in configuration, but its persistent setup scripts are step 04. Phase 2 created two temporary namespaces for capability checks. Distinguishing those facts prevents a setup test from being described as a completed experiment lab.

### Why each tool exists

| Tool or facility | Responsibility in the project |
| --- | --- |
| `ip`, namespaces and veth | Build and inspect isolated network nodes and their connections. |
| `tc` and queue disciplines | Configure and inspect packet scheduling, shaping and emulation queues. |
| CAKE | The chosen queueing/shaping mechanism for later QoS enforcement. |
| nftables | Install inspectable rules and stateful packet accounting/marking mechanisms. |
| conntrack | Obtain connection-related state and directional packet/byte counters. |
| `ping`, iperf3 | Reachability/latency probes and later controlled traffic workloads. |
| Python venv/pip | Keep application dependencies separate from system Python. |
| `jq`, ethtool and sysstat | Inspect structured results, interfaces and resource use. |

Installing `tc` alone does not establish that the running kernel supports a requested queue. The preflight created and read back CAKE, FQ-CoDel, HTB and netem queues on a temporary interface, created an IFB device, checked IPv4/IPv6 connectivity and exercised namespace-local forwarding settings.

For conntrack, accounting was enabled before generating a new ICMP tuple. Inspecting an old connection could otherwise provide misleading evidence about whether newly enabled accounting works. The check required real packet and byte counters on the newly created tuple.

### Ownership is part of cleanup correctness

The probe generates unique namespace names. It adds a name to its owned list only after creation succeeds. Cleanup then removes only that list, in reverse order, and checks that the names disappeared.

Suppose namespace A is created but namespace B fails. A must be removed, while a pre-existing B must remain untouched. If removing one owned resource fails, cleanup still attempts the remaining owned resources. The five original safety tests cover these ownership/failure cases, privilege gating and command timeouts.

This is a general resource-management pattern: acquire, record ownership, perform work, release in `finally`, and verify release. A broad wildcard cleanup would lose the ownership guarantee.

### What actually passed, and what changed

The recorded reference preflight passed 34 capability checks and reported one resource-planning warning. The VM has 3 GiB RAM. The original phase-2 run used four vCPUs; a later boot stalled with clock-source delays, and startup recovered after changing to one vCPU. The phase-3 manifest records that actual configuration. This observation does not prove the precise underlying cause of the VirtualBox stall.

We preserved the original manifest rather than rewriting history. A resource warning is not a throughput benchmark, and a passing capability check does not establish performance under a loaded four-device experiment.

### What to learn from phase 2

Verify the operation you depend on. Preserve its environment and failure evidence. The useful claim is specific: this kernel, with these tools and privileges, successfully performed these operations in owned test namespaces.

## 4. Phase 3: making component boundaries executable

### The problem with passing arbitrary dictionaries

Imagine two modules exchanging `{"bandwidth": 20}`. One might mean measured upload Mbit/s; another might interpret it as a download limit in bytes/s. Both modules could run without a type error while producing an incorrect policy.

Phase 3 replaces that ambiguity with explicit, validated records. The package lives in [src/adaptive_qos](../../src/adaptive_qos/) and uses a `src` layout, pinned dependencies, CLI entry points and independent Windows/Linux virtual environments.

### The eight record types

| Record | Question it answers | What its existence does not establish |
| --- | --- | --- |
| `FlowWindow` | Which TCP/UDP flow and directional counters were observed over what interval? | A real observer has collected those counters. |
| `Classification` | What category was predicted, from which window, with what confidence and reasons? | A classifier has achieved a particular accuracy. |
| `LinkEstimate` | What throughput was observed, what capacity is estimated/unknown, and what rate is recommended? | The recommendation equals measured capacity. |
| `Intent` | Which observed flow/device should receive temporary protection, for what lifetime? | Kernel treatment has changed. |
| `Policy` | What bounded directional rates and treatments are proposed, with which inputs and lease? | An actuator has applied the proposal. |
| `ApplyResult` | Which resources were attempted, applied, verified, failed or restored? | A synthetic result establishes real enforcement or improved user experience. |
| `MetricSample` | Which measured quantity, unit, target, window and sample count are reported? | Missing measurements can be substituted with healthy-looking zeroes. |
| `Event` | Which typed lifecycle fact occurred, and what caused it? | A record link alone establishes valid chronology. |

Nine valid examples cover these eight types because there is a separate link estimate for upload and download. Sixteen indexed invalid examples exercise declared rejection reasons.

### Validation has several layers

The shared base model uses strict types, frozen values, forbidden extra fields and finite numbers. Configured rates are positive integers; counters have unsigned 64-bit bounds. JSON parsing also rejects duplicate keys and non-finite constants. UTC timestamps must have timezone information. Unknown predictions must carry unavailable confidence.

Pydantic strict mode reduces implicit type conversion; JSON representations still have documented exceptions such as timestamp strings. The project therefore adds field-specific and relationship checks instead of assuming one flag proves correctness. See [Pydantic strict-mode documentation](https://docs.pydantic.dev/latest/concepts/strict_mode/).

There are four useful levels to recognize:

1. **Field validity:** is a rate a positive integer?
2. **Record validity:** does an observation end after it starts, and does intent expiry match duration?
3. **Context validity:** is this flow registered, are these inputs fresh, and is this issuer epoch authorized?
4. **Operational validity:** did actual kernel state change as requested, and did measured outcomes improve?

The first three levels have implemented checks. The full operational loop belongs to later phases. Shape-valid data can still be contextually false.

Frozen values prevent accidental in-place edits. They do not make data cryptographically authentic. An unchecked `model_copy` can also bypass normal constructor checks, so the JSON export boundary revalidates instances.

### Worked example: bytes, throughput, capacity and shaping

The synthetic flow covers monotonic t=1s to t=2s:

```text
Upload bytes = 250,000
Download bytes = 1,000,000
Elapsed seconds = 1

Upload bit/s = 250,000 * 8 / 1 = 2,000,000
Download bit/s = 1,000,000 * 8 / 1 = 8,000,000
```

The download link example simultaneously has:

```text
observed_rate_bps            = 8,000,000
estimated_capacity_bps       = null
recommended_shaper_rate_bps  = 90,000,000
estimate_source             = unavailable
load_state                  = demand_limited
```

There is no contradiction. The counters describe traffic during the interval. The recommendation comes from configured starting information. Demand-limited traffic does not establish the unused capacity of the path. Keeping capacity unknown is more honest than reporting the observed eight Mbit/s as the maximum possible rate.

The formula uses actual elapsed time. If the same upload bytes span two seconds, the observed upload rate is one Mbit/s. A delayed collection loop must not assume that every sample covers exactly one second.

### Why null and zero have different meanings

Zero measured losses from ten sent probes is a measurement. No probes sent means loss is unavailable. Similarly, zero observed backlog differs from a failed queue read.

The contracts keep unavailable values as `null` with a reason. A present metric value requires a positive sample count. Count metrics require integers; fractions stay within zero and one; whole-VM CPU and one-core-normalized process CPU have different constraints.

### Why we use UTC, monotonic time, boot identity and epochs

UTC timestamps help people correlate logs. Runtime deadlines use monotonic nanoseconds. Python documents that its monotonic clock is unaffected by system-clock updates and that elapsed differences are meaningful; the absolute origin is unspecified. See [Python's time documentation](https://docs.python.org/3.12/library/time.html#time.monotonic).

The project adds `boot_id` to reject cross-boot comparisons. It adds an intent owner/controller epoch so a restart does not silently authorize saved temporary grants. Policy generations increase within an issuer epoch; an integer generation alone is insufficient across restarts.

The fake clock makes exact boundaries deterministic:

| Monotonic instant | Observation from t=2s, max age 2.5s | Policy leased until t=7s | Intent expiring at t=32s |
| --- | --- | --- | --- |
| t=2s | Fresh | Lease valid | Active under its owner epoch |
| t=4.5s | Stale at the exact age threshold | Lease still valid, but stale required input prevents a new accepted proposal | Still active |
| t=7s | Stale | Expired at its exact deadline | Still active |
| t=32s | Stale | Expired | Inactive at its exact deadline |

An immutable saved intent can still contain `state="active"` at t=32s. The consumer recomputes validity using the clock and deadline. Reading the old label alone would incorrectly revive the grant.

These functions do not run a background timer or remove a kernel rule. Later actuator/watchdog work must turn expiry into independently verified enforcement.

### Prediction, correction and intent are different facts

The linked fixture keeps three values:

```text
Original classifier prediction: bulk_like
User correction:                interactive_like
Chosen service treatment:       interactive
```

The correction does not erase the original prediction. Otherwise a classifier evaluation could appear to improve merely because user feedback rewrote its past answers. Intent changes requested treatment; it does not prove which application generated packets. The eventual service must reconcile these inputs while preserving their separate provenance.

### Configuration is a boundary between actors

| File | Permitted consumer | Contents |
| --- | --- | --- |
| `lab.yaml` | Lab runner and offline evaluator | Topology and emulator settings. |
| `controller.yaml` | Runtime controller | Trusted gateway bindings, rate bounds, nominal starting information, timing and heartbeat budgets. |
| `safe-policy.yaml` | Runtime recovery/actuation path | A declared fallback template, fair default and no temporary grants. |

`load_runtime_config()` reads only the latter two files. `load_config_bundle()` is an offline setup validator that also reads the lab. This separation helps keep hidden emulator truth out of the future estimator. It does not prevent a process with filesystem access from opening `lab.yaml`; process/filesystem isolation remains later work.

The current fallback proposes 15 Mbit/s download and three Mbit/s upload inside a declared minimum capacity envelope of 20/5 Mbit/s. Its `safe` label expresses intended fallback behavior. These settings have not been benchmarked as guaranteed safe, fair or optimal on a real connection.

The YAML loader rejects duplicates, aliases, unsupported tags, multiple documents, unknown fields and oversize inputs. The runtime object carries both original-file hashes and a normalized effective-settings hash. A comment can change file identity without changing effective settings. A hash identifies content; it does not validate the truth of that content.

The heartbeat budget also makes a constraint executable:

```text
2 seconds stale threshold
+ 1 second watchdog polling budget
+ 2 seconds application/read-back budget
= 5 seconds recovery target
```

Checking this arithmetic identifies an impossible configuration early. Actual scheduling and recovery still need to meet the budget in a crash experiment.

### Packaging checks answer another question

An editable install uses the source tree, so it may hide missing distribution files. Phase 3 also built a wheel, installed it in a fresh environment, changed to a directory outside the repository and checked that imports came from that environment's `site-packages`.

The original [verification record](../environments/phase-03-verification.json) preserves that wheel's identity and source hashes. It records the original 119-test Ubuntu run, not a rebuilt wheel for the later debugging commit.

## 5. What debugging taught us beyond the original passing suite

The original suite passed 119 tests in Ubuntu and 118 with one skip in Windows. Passing those tests did not establish that every important relationship had a test. In the follow-up debugging work, 14 new negative cases initially failed because invalid inputs were accepted. We fixed those cases and added further graph and ordering coverage, for 18 new cases overall.

| Reproduced issue | Why individual field validation missed it | Fix and general lesson |
| --- | --- | --- |
| A verification event could precede its policy/result; declared references could be missing. | Every record could have valid types and timestamps in isolation. | Resolve the evidence graph and require each cause to exist and occur no later than its effect. Validate relationships. |
| Equal-time references could form cycles, including a policy citing its own application result. | Sorting by timestamp cannot expose cycles when times are equal. | Traverse dependencies with visiting/visited sets; reject a node reached again on the active path. A successful application depends on the policy it applies. |
| A policy could cite an active and a cancelled snapshot of the same intent and select the active one. | Both snapshots were individually legitimate historical records. | Reject multiple snapshots of one intent within a proposal. The caller must reconcile state; this is not a completed live conflict-resolution service. |
| A registry key could identify flow A while its stored object identified flow B. | Lookup success was being treated as identity proof. | Compare the retrieved object's flow ID, device ID and namespace with the requested target. |
| Source paths could use Windows separators, stream syntax or noncanonical spelling. | POSIX parsing and Windows filesystem interpretation differ. | Require canonical relative POSIX spelling before applying the source allowlist and containment checks. |

The replay graph permits equal timestamps when dependencies are acyclic; clock resolution may put multiple events at one recorded instant. It also accepts a different input order. File order and causality are separate concepts.

The first debugging run also hit Windows temporary-directory permission errors. Rerunning with a dedicated workspace `--basetemp` made the existing suite pass. That diagnosis separated an execution-environment problem from an application defect; changing the application would not repair pytest's inaccessible scratch directory.

The final debugging results were **137 passed on Ubuntu** and **136 passed, one skipped on Windows**. The Windows skip is the symlink-creation case, which passed on Linux. The fixes were published in commit `4abac56`. These are the previously completed verification results; this guide does not claim that the full suite was rerun while writing the explanation.

## 6. Run a six-part lesson against the actual code

The annotated [walkthrough](../../examples/learning/phase123_walkthrough.py) uses the real package and synthetic fixtures. Its expected rejections must match specific reasons, so an unrelated exception cannot count as a successful lesson.

From the repository root in PowerShell, using the installed project environment:

```powershell
.venv/Scripts/python.exe examples/learning/phase123_walkthrough.py
```

Inside Ubuntu after synchronizing this new file:

```bash
.venv/bin/python examples/learning/phase123_walkthrough.py
```

The six parts demonstrate configuration separation, rate arithmetic, strict rate types, unavailable measurements, independent deadlines, and a shape-valid but chronologically impossible event. They read examples and advance a fake clock. No privileged preflight, packet generator or network mutation is invoked.

To investigate the implementation yourself, use this reading order:

1. `FlowWindow.rate_bps` in `models.py`: connect a formula to a typed observation window.
2. `intent_is_active` and `is_fresh` in `clock.py`: list every condition for validity.
3. `validate_policy_context` in `validation.py`: separate shape checks from trusted runtime context.
4. `replay_records` in `contracts.py`: draw dependencies before following the traversal.
5. `load_runtime_config` in `config.py`: identify which actor can learn which settings.
6. `Probe.cleanup` and `network_checks` in `lab/preflight.py`: follow ownership through a partial failure.

## 7. Exercises with answer checks

Try answering these before reading each expected result.

### Exercise A: write one falsifiable requirement

Write a requirement for temporary priority that names the target, duration, failure behavior and evidence. A satisfactory answer includes actual enforcement/removal timing and a competing bulk workload. "The button works" does not cover those responsibilities.

### Exercise B: calculate a delayed observation

You observe 750,000 upload bytes over 1.5 seconds. What is the rate, and does it reveal maximum link capacity?

**Answer:** `750000 * 8 / 1.5 = 4,000,000 bit/s`. It describes that observation, without establishing the unused capacity of a demand-limited path.

### Exercise C: predict a validation failure

In a copied policy example, replace integer `18000000` with string `"18000000"`. Which layer should reject it?

**Answer:** field/type validation when parsing the record. It should fail before a policy is considered for application.

### Exercise D: reason about two deadlines

At t=4.5s, can the example policy be accepted as a new proposal because its lease runs until t=7s?

**Answer:** no. Its required observation from t=2s has reached the 2.5-second freshness threshold. A valid lease does not refresh evidence.

### Exercise E: distinguish a failed measurement from a good result

Your endpoint-loss measurement sent zero probes. Can it report zero loss?

**Answer:** report unavailable with an explicit reason. There is no observed denominator from which to claim a loss fraction.

### Exercise F: draw the forbidden cycle

Suppose policy P cites apply-result A as its evidence, and A claims to apply P. Both carry the same monotonic timestamp. Is timestamp comparison sufficient?

**Answer:** no. The dependency loop P -> A -> P is invalid even when neither timestamp is later. Cycle detection addresses the relationship that timestamp ordering misses.

### Exercise G: inspect information boundaries

Find which function reads `lab.yaml`. Then explain what else is needed to stop a future runtime process reading it directly.

**Answer:** the offline `load_config_bundle()` reads it; `load_runtime_config()` does not. Deployment permissions and process/filesystem separation must enforce the intended boundary in the running system.

### Exercise H: identify the evidence still missing

Someone says, "137 tests passed, so the QoS engine reduces call delay by 50%." What evidence must be added?

**Answer:** a real forwarding/enforcement path, controlled concurrent workloads, a measured baseline, repeated comparable runs, frozen definitions/configuration, and recorded latency/goodput/fairness/recovery outcomes. Contract tests establish different claims.

## 8. Explain the project back in your own words

A useful oral explanation is:

> In phase 1, we translated the case study into traceable requirements and measurable acceptance procedures. In phase 2, we provisioned an isolated Linux environment and verified the needed networking capabilities with owned temporary resources. In phase 3, we implemented strict shared records, configuration boundaries, deterministic time checks, packaging and synthetic replay. Debugging then exposed missing relationship checks that the original passing tests had not covered. The next work must connect these contracts to real collection and enforcement and evaluate the resulting behavior.

You understand these phases when you can explain why a valid record may still be stale, why low throughput need not mean low capacity, why a kernel capability pass differs from a QoS result, and why every claimed improvement needs evidence tied to a declared experiment.
