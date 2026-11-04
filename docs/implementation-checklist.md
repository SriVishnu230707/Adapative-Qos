**Adaptive QoS — implementation checklist**

[Project README](../README.md) · [Detailed implementation plan](implementation-plan.md) · [CN learning map](cn-learning/README.md)

Implement these steps in order. Each step produces something you can inspect or run. Advance after its completion check passes.

This is a task breakdown for the proposed implementation. Steps 01-03 are complete: requirements, the Linux environment and the shared contract package are implemented and verified. Most networking/algorithm modules named below still need to be created, and QoS runtime acceptance checks remain unrun. Linux CAKE, Python, nftables and conntrack are project choices; they are not additional requirements imposed by the problem statement.

<a name="step-01"></a>

**01. Write the requirements and acceptance checklist**

- [x] List every required feature from Case Study 3 in `docs/requirements.md`.
- [x] Give each requirement an ID, responsible module and verification method.
- [x] Define the initial supported scope: synthetic traffic in an isolated Linux lab.
- [x] Separate required behavior from optional extensions and proposed numerical targets.

**Output:** [Requirements and acceptance checks](requirements.md).

**Status:** documentation complete on 4 November 2026; runtime checks are all **Not run**. The requirements document defines the current acceptance criteria and formalizes the detailed plan's initial targets.

**Complete when:** classification, capacity estimation, enforcement, fairness, temporary intent, IPv4/IPv6, recovery, dashboard and reproducible evidence all have explicit checks.

[Detailed guidance](implementation-plan.md#step-1)

<a name="step-02"></a>

**02. Set up the Linux development environment**

- [x] Create the Ubuntu reference VM and install the tools listed in the detailed plan.
- [x] Verify network namespaces, CAKE, conntrack, nftables and required privileges.
- [x] Record actual OS, kernel, tool and Python versions.
- [x] Write a preflight script that reports missing capabilities clearly.

**Output:** [Preflight entry point](../lab/preflight.sh), [measured environment manifest](environments/ubuntu-24.04-virtualbox.json) and [setup guide](setup.md).

**Status:** complete on 4 November 2026. Ubuntu 24.04.5 / kernel `6.8.0-147-generic` passed 34 capability checks, with one warning for 3 GiB RAM versus the plan's adjustable allocation. Five preflight safety tests passed. The requirements' QoS acceptance procedures T01-T16 remain **Not run**.

**Complete when:** preflight passes and the environment can create an isolated namespace and inspect its interfaces/routes.

[Detailed guidance](implementation-plan.md#step-2)

<a name="step-03"></a>

**03. Create the project skeleton and shared data contracts**

- [x] Create `src/adaptive_qos/`, `configs/`, `lab/`, `workloads/`, `experiments/`, `tests/` and `ui/`.
- [x] Set up a Python virtual environment and package configuration.
- [x] Define `FlowWindow`, `Classification`, `LinkEstimate`, `Intent`, `Policy`, `ApplyResult`, `MetricSample` and `Event` records.
- [x] Include timestamps, units, freshness, schema versions and explicit unavailable values.
- [x] Separate lab configuration, controller bounds and the safe policy.

**Output:** [Package configuration](../pyproject.toml), [shared models](../src/adaptive_qos/models.py), [configuration loader](../src/adaptive_qos/config.py), [development guide](development.md) and [verification record](environments/phase-03-verification.json).

**Status:** complete on 4 November 2026. Nine labeled synthetic records cover eight types; 16 invalid examples are rejected. Ubuntu passed 119 tests, Windows passed 118 with one symlink-permission skip, and the built wheel passed a fresh-environment check outside the source tree. The standalone preflight remains independent of the application dependencies. These are phase-3 contract checks, not the full QoS acceptance experiments.

**Complete when:** modules can exchange validated example records without relying on ad hoc dictionaries or ambiguous units.

[Detailed guidance](implementation-plan.md#step-2)

<a name="step-04"></a>

**04. Build the routed network lab**

- [ ] Create client, gateway, ISP and server namespaces connected by veth pairs.
- [ ] Configure addresses, forward routes, return routes and forwarding inside the routers.
- [ ] Verify IPv4, then equivalent IPv6 connectivity in the isolated lab.
- [ ] Implement inspection and cleanup for resources owned by this project.
- [ ] Expand to a LAN bridge and separate device namespaces after the single-client path works.

**Output:** `lab/up.sh`, `lab/down.sh`, `lab/inspect.sh` and a topology diagram.

**Complete when:** clients can upload/download through both routers, and reset recreates the same working topology.

[Detailed guidance](implementation-plan.md#step-3)

<a name="step-05"></a>

**05. Add independent ISP bandwidth emulation**

- [ ] Place upload and download bottlenecks in the ISP namespace, separate from gateway QoS.
- [ ] Configure an initial profile, such as 100 Mbit/s download and 20 Mbit/s upload.
- [ ] Add timed capacity drop/recovery profiles and documented queue limits.
- [ ] Keep the actual capacity schedule available only to the runner and offline evaluator.
- [ ] Validate any added delay/loss stage without replacing other queues accidentally.

**Output:** `lab/isp_profile.sh` and scenario configurations.

**Complete when:** throughput follows emulator changes while the controller has no access to future change times or hidden capacity values.

[Detailed guidance](implementation-plan.md#step-3)

<a name="step-06"></a>

**06. Generate traffic and save the uncontrolled baseline**

- [ ] Run TCP upload/download and simultaneous latency measurements.
- [ ] Add call-like UDP, game-like echo, TV-like segment downloads and bulk workloads.
- [ ] Put household devices in separate namespaces for device-fairness tests.
- [ ] Save endpoint measurements, interface counters, queue observations and actual event times.
- [ ] Label synthetic workloads and keep their ground-truth labels out of classifier inputs.

**Output:** workload scripts and the first raw uncontrolled run.

**Complete when:** saved measurements show what contention does to interactive traffic and bulk delivery, with definitions for every reported metric.

[Detailed guidance](implementation-plan.md#step-4)

<a name="step-07"></a>

**07. Implement static CAKE enforcement**

- [ ] Apply fixed upload and download shaper rates on the gateway.
- [ ] Verify queue traversal and collect actual `tc` statistics.
- [ ] Introduce service classes and verified IPv4/IPv6 marking.
- [ ] Implement policy validation, application, read-back verification and reconstructable restoration.
- [ ] Confirm that lower-priority traffic still progresses.

**Output:** `src/adaptive_qos/actuator.py`, marking configuration and static-QoS evidence.

**Complete when:** fixed QoS changes measured network behavior, the intended service counters increase, and invalid configurations are rejected before mutation.

[Detailed guidance](implementation-plan.md#step-5)

<a name="step-08"></a>

**08. Implement metadata and metric collection**

- [ ] Enable conntrack accounting before starting test connections.
- [ ] Collect per-direction byte/packet deltas, duration and coarse traffic-window features.
- [ ] Collect queue backlog, rates, probe measurements and available endpoint quality measurements.
- [ ] Handle counter resets, expired connections, restarts and stale samples.
- [ ] Store raw and normalized observations without inspecting application payloads.

**Output:** `src/adaptive_qos/observer.py`, `storage.py` and timestamped records.

**Complete when:** records match known traffic direction/rate, distinguish unavailable from zero, and support replay. Do not claim precise inter-arrival features from coarse counter snapshots.

[Detailed guidance](implementation-plan.md#step-6)

<a name="step-09"></a>

**09. Build the deterministic traffic classifier**

- [ ] Implement `interactive_like`, `streaming_like`, `bulk_like` and `unknown` decisions from collected features.
- [ ] Return a confidence grade and supporting reasons.
- [ ] Keep ambiguous traffic unknown and separate predicted from effective policy categories.
- [ ] Evaluate held-out runs with changed ports, rates and packet sizes.
- [ ] Report confusion, per-class precision/recall and unknown coverage.

**Output:** `src/adaptive_qos/classifier.py` and a classifier evaluation report.

**Complete when:** runtime classification uses metadata rather than workload labels, handles ambiguity visibly and can accept later correction.

[Detailed guidance](implementation-plan.md#step-6)

<a name="step-10"></a>

**10. Implement the link estimator**

- [ ] Maintain separate upload and download observations and estimates.
- [ ] Combine traffic/load evidence, delay probes, queue observations and freshness.
- [ ] Expose uncertainty and demand-limited periods instead of equating throughput with capacity.
- [ ] Implement bounded reduction and cautious recovery trials with smoothing and cooldowns.
- [ ] Compare estimates with hidden emulator ground truth in the offline evaluator.

**Output:** `src/adaptive_qos/estimator.py` and bandwidth-change traces.

**Complete when:** an unannounced capacity drop/recovery produces justified responses under load; low traffic demand does not become a false low-capacity claim.

[Detailed guidance](implementation-plan.md#step-7)

<a name="step-11"></a>

**11. Connect the adaptive policy loop**

- [ ] Implement a decision function that combines observations, classifications, estimates and previous state.
- [ ] Validate rate bounds, permitted selectors and policy validity.
- [ ] Apply only material changes, then read back the installed state.
- [ ] Add hysteresis, cooldowns, post-change observation and explicit state transitions.
- [ ] Monitor backlogged bulk progress and invoke the fair fallback when necessary.

**Output:** `src/adaptive_qos/policy.py`, `controller.py` and a decision/event history.

**Complete when:** replay gives explainable decisions and live repeated runs show bounded behavior without excessive oscillation or starvation.

[Detailed guidance](implementation-plan.md#step-8)

<a name="step-12"></a>

**12. Add temporary intent and classification correction**

- [ ] Implement create/cancel intent operations and flow-category correction.
- [ ] Validate observed device/flow ownership, scope, duration and conflicts.
- [ ] Enforce automatic expiry independently of the browser.
- [ ] Keep original predictions and correction provenance.
- [ ] Confirm actual service changes and impact on competing traffic.

**Output:** `src/adaptive_qos/intents.py` and intent/correction API operations.

**Complete when:** a narrow priority takes effect, can be canceled, expires correctly and does not turn every transfer from a device into top-priority traffic.

[Detailed guidance](implementation-plan.md#step-9)

<a name="step-13"></a>

**13. Implement watchdog recovery and rollback**

- [ ] Run the watchdog separately from the main controller.
- [ ] Monitor heartbeat, telemetry freshness, policy expiry and actuator health.
- [ ] Restore and verify last-known-good or predefined safe configuration after failures.
- [ ] Test partial application, invalid commands, controller termination and stale persisted grants.
- [ ] Record failure, recovery and final verification timestamps.

**Output:** `src/adaptive_qos/watchdog.py`, safe-policy configuration and failure-test evidence.

**Complete when:** recovery works while the main controller is stopped, temporary grants are removed as specified and forwarding remains available within the declared operating conditions.

[Detailed guidance](implementation-plan.md#step-10)

<a name="step-14"></a>

**14. Build the API and dashboard**

- [ ] Add health, metrics/history, flows, policy and event read endpoints.
- [ ] Connect intent, correction, cancellation and rollback actions through validated operations.
- [ ] Show latency, defined jitter, application loss, throughput, queue backlog and fairness.
- [ ] Show confidence, estimate uncertainty, active intent expiry, freshness and rollback history.
- [ ] Separate privileged enforcement from the API/UI and protect mutation operations.

**Output:** `src/adaptive_qos/api.py` and `ui/`.

**Complete when:** displayed values trace to raw observations, missing/stale data is visible, and UI actions cause verified queue behavior.

[Detailed guidance](implementation-plan.md#step-11)

<a name="step-15"></a>

**15. Automate comparisons and generate reports**

- [ ] Implement setup/reset, workload start/stop, timed condition changes and evidence collection.
- [ ] Compare uncontrolled, competent static-QoS and adaptive modes under matched conditions.
- [ ] Test intent off/on, equal-service device fairness, IPv4/IPv6 and recovery failures.
- [ ] Repeat important scenarios; save seeds, versions, configuration and raw observations.
- [ ] Generate plots and reports covering quality, goodput, fairness, estimation, adaptation, stability and overhead.

**Output:** `experiments/run.py`, `analyze.py`, `report.py` and reproducible run artifacts.

**Complete when:** a scripted run creates a fresh report, isolates each feature's contribution and retains negative results and uncertainty.

[Detailed guidance](implementation-plan.md#step-12)

<a name="step-16"></a>

**16. Package the completed project**

- [ ] Finish exact setup, verification, reset and troubleshooting instructions.
- [ ] Map every requirement to implemented files and saved acceptance evidence.
- [ ] Document architecture, emulation limits, production gaps and third-party licenses.
- [ ] Include generated workload/impairment settings and suitable example configuration.
- [ ] Prepare a demonstration of contention, adaptation, intent expiry, failure recovery and the generated report.

**Output:** a reproducible repository, documentation, evidence and demonstration.

**Complete when:** another person can reproduce the important results on the declared environment and distinguish implemented behavior from proposed extensions.

[Detailed guidance](implementation-plan.md#step-13)

**Milestones to track**

| Milestone | Steps | Visible result |
| --- | --- | --- |
| M1: Working lab | 01–06 | Routed devices, controllable ISP and saved uncontrolled measurements |
| M2: Working static QoS | 07 | Real enforced queues and a baseline-versus-static comparison |
| M3: Working adaptive controller | 08–11 | Metadata-based decisions and response to hidden bandwidth changes |
| M4: Intent and recovery | 12–13 | Expiring priorities, correction and independently verified rollback |
| M5: Complete demonstration | 14–16 | Dashboard, automated reports, reproducible setup and acceptance evidence |

The first implementation target is **M1**, followed by **M2**. Add classifier sophistication, adaptation and the dashboard after those packet-path and measurement foundations work. Optional ML or real-router integration comes after the required deterministic system is demonstrated.
