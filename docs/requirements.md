# Adaptive QoS requirements and acceptance checks

[Project README](../README.md) · [Implementation checklist](implementation-checklist.md#step-01) · [Detailed implementation plan](implementation-plan.md#step-1)

Prepared on 4 November 2026. This document completes implementation step 01: deciding what to build and what evidence will establish completion. The QoS software, lab and experiment results do not exist yet. Every implementation acceptance check below is **Not run**.

## 1. Sources and interpretation

The primary source is the supplied **Case Study 3 - Adaptive QoS Engine for Mixed Home Broadband Traffic**, a three-page PDF. Physical PDF pages 2 and 3 were checked against both extracted text and rendered pages. The original supplied filename is `Case Study 3_MCV_Adaptive QoS Engine for Mixed Home Broadband Traffic.pdf`; the PDF is not redistributed here.

The following source codes make the requirement table traceable. Bullet numbers refer to their order within the named PDF section, starting at 1.

| Code | Location | Meaning |
| --- | --- | --- |
| S1 | Page 2, Background | Protect interactive traffic, preserve useful bulk progress and explain the prototype's production boundary. |
| S2 | Page 2, Challenge, paragraphs 1-2 | Metadata-based classification, capacity estimation, queueing/shaping, outcome verification, temporary intent, independently testable modules and conditional AI comparison. |
| S3 | Page 3, Constraints and Design Boundaries, bullets 1-10 | Privacy, inspectable enforcement, IP families, rollback/starvation, confidence/correction, licenses, reproducibility, emulation disclosure, secrets and bounded recovery. |
| S4 | Page 3, Expected Output and Acceptance Evidence, bullets 1-9 | Required modules, dashboard, experiments, intent interface, architecture, setup, scripted demonstration and limitations. |
| S5 | Page 3, Evaluation Criteria, bullets 1-5 | Accuracy, interactive quality, fairness, adaptation/stability, CPU overhead and reproducibility. |
| S6 | Page 2, Illustrative Use Cases and Test Scenarios | Call plus download, a bandwidth drop, and TVs plus gaming; examples rather than an exhaustive mandatory test list. |

**Required** means behavior or evidence requested by the brief. **Project choice** means how this repository will implement or test it. **Project target** means a numerical acceptance threshold chosen here; it is not an organizer-supplied threshold. **Conditional** means the requirement applies when its stated condition holds. None of these labels means that a check has passed.

The existing implementation plan supplies engineering guidance. If it conflicts with an explicit requirement in the PDF, the PDF controls requirement meaning. Change this document and the plan together when the project design changes.

## 2. Initial scope and boundaries

The first complete version will run in an isolated Linux lab with this routed path:

```text
Synthetic household devices <-> QoS gateway <-> Independent ISP emulator <-> Test server
```

The gateway will observe traffic metadata, predict broad behavior categories, estimate link conditions separately for upload/download, apply bounded policies, accept expiring user intent and record outcomes. The Linux network stack will forward packets. Python, CAKE, nftables and conntrack are project choices from the plan; the brief permits other open, inspectable mechanisms.

The lab will include synthetic interactive-like, streaming-like, bulk-like and deliberately ambiguous workloads, with separate device namespaces for fairness checks. IPv4 is required. The chosen reference Linux lab is expected to support IPv6 and will test it; if unavailable, record the precise failed capability check and limitation rather than reporting an IPv6 pass.

All workload names and screens must say when traffic is synthetic. A paced UDP workload is a call-like traffic model, not proof of real video-call quality. Predicted categories are `interactive_like`, `streaming_like`, `bulk_like` and `unknown`; they are not claims of recognizing Zoom, Netflix or a particular game. A correction changes the effective policy category while preserving the original prediction.

The initial declared experiment envelope is one gateway, up to four synthetic household devices, controlled wired virtual links, and separately tested download/upload bottlenecks. Initial profiles are 100/20 Mbit/s download/upload, a download-only drop to 20 Mbit/s and recovery, and a separate upload-only drop to 5 Mbit/s and recovery. These are chosen lab settings, not minimum hardware specifications or Internet service guarantees. Record workload rates, queue limits and actual timing in each run.

Later extensions include genuine WebRTC applications, physical routers, Wi-Fi airtime control, production deployment, QUIC-specific features and ML. They are outside the first-version scope. If AI is introduced, R21 becomes applicable. Production authentication, privilege isolation, hardware throughput, upgrade procedures, certification and proprietary integrations must be discussed as limitations before claiming production readiness.

## 3. Requirement-to-evidence matrix

Each row is **Required**, except explicitly conditional rows. Owners are module responsibilities, not people. Module names and artifact paths are planned interfaces; they do not imply those files already exist. Evidence paths are relative to the run directory defined in section 7 unless prefixed with `docs/` or `tests/`.

| ID | Required behavior or deliverable | Source | Responsible module | Acceptance check | Planned evidence |
| --- | --- | --- | --- | --- | --- |
| R01 | Observe/classify without decrypting traffic or reading private application payloads. | S2 paragraph 1; S3 bullet 1 | Observer, classifier, storage | T01 | `T01/privacy-review.md`, `T01/sample-records.jsonl` |
| R02 | Classify broad categories; expose confidence and support correction. | S2 paragraph 1; S3 bullet 5; S4 bullet 1; S5 bullet 1 | Observer, classifier, intent service | T02 | `T02/classifications.jsonl`, `T02/ground-truth.jsonl`, `T02/classifier-evaluation.json`, `T02/correction-events.jsonl` |
| R03 | Estimate link capacity and report estimation accuracy. | S2 paragraph 1; S4 bullet 1; S5 bullet 1 | Link estimator | T03 | `T03/estimates.jsonl`, `T03/estimator-evaluation.json` |
| R04 | Change queueing/shaping through an open, inspectable policy engine and enforcement module. | S2 paragraph 1; S3 bullet 2; S4 bullet 2 | Policy engine, actuator | T04 | `T04/policy-events.jsonl`, `T04/tc-before-after.json`, `T04/enforcement-metrics.jsonl` |
| R05 | Retain fair service and prevent low-priority starvation. | S1; S2 paragraph 1; S3 bullet 4; S5 bullet 3 | Policy engine, actuator | T05 | `T05/device-goodput.jsonl`, `T05/fairness-summary.json` |
| R06 | Expose an API or UI for temporary service intent while retaining fairness. | S2 paragraph 1; S4 bullet 5 | Intent service, API, policy engine | T06 | `T06/intent-events.jsonl`, `T06/intent-service-metrics.jsonl` |
| R07 | Support IPv4 and, where available, IPv6 throughout the traffic path. **IPv6 is conditional on availability.** | S3 bullet 3 | Lab, observer, classifier, actuator | T07 | `T07/address-family-checks.json`, `T07/routes-and-counters.json` |
| R08 | Provide policy rollback. | S3 bullet 4 | Actuator, persisted policy state | T08 | `T08/fault-events.jsonl`, `T08/restored-state.json` |
| R09 | Bound, expose and reverse automated changes; failures return to a known safe state. | S3 bullet 10 | Policy engine, watchdog, actuator | T09 | `T09/recovery-events.jsonl`, `T09/external-verification.json` |
| R10 | Define independently testable module inputs, outputs, states, failures and success conditions. | S2 paragraph 2 | All module owners; shared models | T12 | `docs/architecture.md`, `tests/module-contract-results.json` |
| R11 | Display latency, jitter, loss, throughput, queue depth and fairness from recorded measurements. | S4 bullet 3 | Collector, storage, API, dashboard | T10 | `T10/dashboard-reconciliation.json`, `T10/dashboard-screenshots/` |
| R12 | Automate baseline-versus-optimized experiments and verify user-experience outcomes. | S2 paragraph 1; S4 bullet 4; S5 bullet 2 | Experiment runner, analyzer, report generator | T11 | `T11/comparison.json`, `T11/report.md` |
| R13 | Evaluate adaptation speed, stability and CPU overhead with reproducible, clear results. | S5 bullets 4-5 | Collector, experiment analyzer | T03, T11 | `T11/overhead.json`, `T11/stability.json`, `T11/report.md` |
| R14 | Explain device, edge, network, data, analytics and UI components in an architecture diagram. | S4 bullet 6 | Documentation, module owners | T14 | `docs/architecture.md` |
| R15 | Provide setup prerequisites, exact tested versions, commands, configuration and verification. | S4 bullet 7 | Lab, packaging, documentation | T14 | `docs/setup.md`, `T14/fresh-setup-log.txt` |
| R16 | Script environment reset, condition introduction, evidence collection and report generation. | S4 bullet 8 | Lab scripts, experiment runner | T13 | `T13/demo-log.txt`, `T13/reset-verification.json`, `T13/report.md` |
| R17 | Document third-party licenses and comply with redistribution restrictions. | S3 bullet 6 | Packaging, documentation | T14 | `docs/third-party-licenses.md`, `T14/license-review.md` |
| R18 | Include generated evaluation data, generator definitions and impairment settings sufficient to reproduce results. | S3 bullet 7; S5 bullet 5 | Workload generators, ISP emulator, runner | T13 | `datasets/`, `scenario.json`, `manifest.json`, `T13/reproduction-comparison.json` |
| R19 | Label emulation, explain its model/limits and identify gaps to production hardware, certification, scale or proprietary integration. | S1; S3 bullet 8; S4 bullet 9 | Documentation, dashboard, reports | T14 | `docs/limitations.md`, `T14/disclosure-review.md` |
| R20 | Exclude credentials, private keys and tokens from source control; make example secrets clearly fictitious. | S3 bullet 9 | Configuration, packaging | T15 | `T15/secret-review.md` |
| R21 | If AI is used, compare against deterministic logic and demonstrate measurable benefit. **Conditional on AI use.** | S2 paragraph 2 | Optional model, experiment analyzer | T16 | `T16/ai-comparison.json`, `T16/ai-decision.md` |

## 4. Module responsibilities and contracts

These are design contracts for step 03 and later implementation. Records must include schema version, timestamp, units and freshness; unknown/unavailable values must not silently become zero. Pure decision modules must be callable with saved records without privileged Linux access. The forwarding data path must remain independent of the Python controller's process lifetime.

| Module | Input -> output | State and failure behavior | Success condition |
| --- | --- | --- | --- |
| Lab and workloads | Scenario configuration -> routed topology, synthetic traffic and evaluator-only labels | Down -> ready -> running -> cleaned; startup failure cleans only project-owned resources | Reset recreates the declared path and yields repeatable workloads (T07, T13). |
| Observer | Metadata counters, queue counters, probes -> `FlowWindow`, `MetricSample` | Fresh -> stale/unavailable; detect resets and direction changes without negative rates | Records reconcile with controlled endpoint/interface measurements (T01, T10). |
| Classifier | `FlowWindow` -> `Classification` with category, confidence grade and reasons | Uncertain -> unknown; low-confidence inputs retain fair default treatment | Held-out evaluation is recorded; prediction and corrected treatment remain distinguishable (T02). |
| Estimator | Fresh samples, prior estimate -> per-direction `LinkEstimate` and uncertainty | Learning -> loaded/trial -> settled; idle/stale observations do not establish maximum capacity | Detects hidden changes and reports accuracy/coverage without reading the emulator trace (T03). |
| Policy engine | Observations, estimates, classifications, intents, previous policy -> bounded `Policy` with reasons | Normal/congested/degraded/fallback; reject stale or out-of-bounds candidates | Replay respects rate bounds, cooldown, fairness and intent expiry (T04-T06, T09). |
| Actuator | Validated `Policy` -> `ApplyResult`, inspected kernel state | Validate -> apply -> verify; partial failure triggers restoration/fallback | Both directions and markings match read-back state; rollback is externally verified (T04, T08). |
| Intent service | Target, goal, duration, correction/cancellation -> `Intent`, effective category, events | Pending -> active -> expired/cancelled; restart cannot revive expired intent | Narrow selection affects intended service and expires without a browser (T02, T06). |
| Watchdog | Heartbeat, policy/telemetry freshness, persisted safe configuration -> fallback request and events | Healthy -> suspect -> fallback -> verified; independent process survives controller exit | Known safe policy is verified while the controller is stopped (T09). |
| Storage | Samples, classifications, estimates, events -> replayable records | Available -> failed; preserve event ordering, report write failure and restrict unverified policy changes | Raw observations and policy generations can be reconstructed (T10-T13). |
| API and dashboard | Recorded state and validated requests -> views, acknowledgements, intent commands | Fresh/stale/degraded; unavailable data is visibly unavailable | Values trace to records and accepted commands trace to verified treatment (T06, T10). |
| Experiments and reports | Locked configurations, seeds, mode -> observations, comparisons and report | Prepare -> run -> collect -> analyze -> cleaned; preserve failed/incomplete status | Repeats comparable modes, records actual changes and produces traceable findings (T11, T13). |

## 5. Acceptance procedures

**Shared execution rule:** freeze configurations, metric definitions, workload labels and target version before a scored run. Use a monotonic run timeline and record wall-clock timestamps for interpretation. Store actual event times, not only requested times. Mark warm-up and transient periods. A missing artifact, stale measurement or failed prerequisite is not a pass. All T01-T16 checks currently have status **Not run**; T16 is additionally conditional.

### T01 - Privacy and permitted metadata (R01)

- **Given/when:** run controlled synthetic traffic, including encrypted traffic, and inspect observer/classifier code, inputs and stored records.
- **Pass:** decisions use documented headers, counters, timing or other permitted metadata; no decryption, private application-content inspection or payload retention is required. Workload ground-truth labels remain evaluator-only. Logs and sample records contain only the declared fields.
- **Evidence:** the R01 artifacts list permitted features, inspected collection points and representative records. A payload capture is not a required evidence artifact.

### T02 - Classification, uncertainty and correction (R02)

- **Given/when:** evaluate frozen rules against held-out sessions whose ports, rates and packet sizes differ from tuning sessions; include ambiguous patterns. Apply and remove a correction to an observed flow under contention.
- **Pass:** outputs include prediction, confidence grade, reasons and window; ambiguous cases can abstain as `unknown`. Report the confusion matrix, per-class precision/recall/F1, classification coverage and unknown rate, including abstentions as missed known labels. Preserve the original prediction while the effective category changes. Queue/marking evidence confirms the correction affects treatment and removal restores normal resolution. Meet PT07 in the declared known-workload evaluation.
- **Evidence:** the R02 artifacts include held-out workload definitions and the exact frozen rule version. A heuristic confidence grade must not be described as a calibrated probability.

### T03 - Capacity estimation and bounded adaptation (R03, R13)

- **Given/when:** independently schedule download 100 -> 20 -> 100 Mbit/s and, in another run, upload 20 -> 5 -> 20 Mbit/s under sufficient offered demand. Include quiet periods and failed probes. The controller cannot read evaluator labels, true rates or future timings.
- **Pass:** upload/download estimates are separate from measured throughput and chosen shaper rate. Record uncertainty, freshness and unavailable periods. Compare against emulator truth offline; report error, estimate coverage, detection time, settlement, recovery and direction reversals. Low-demand observations must not imply exact idle-link capacity. Meet PT02, PT03 and PT08 in their stated windows.
- **Evidence:** the R03 artifacts plus independently recorded actual capacity changes, rate actions and probe health. Demonstrate the controller's input boundary, not just a claim that the trace was ignored.

### T04 - Real queue enforcement and policy bounds (R04)

- **Given/when:** send upload/download traffic, install a valid policy and then propose negative, excessive, stale and too-frequent changes.
- **Pass:** read-back shows the intended rates, interfaces and service treatments; matching counters increase and endpoint delivery responds. Invalid candidates are rejected before mutation. Actions have bounded rates/steps, cooldown and recorded reasons; enforcement is not established by a successful command alone.
- **Evidence:** the R04 artifacts identify both interfaces, address families, policy generations, validation outcomes and actual kernel state.

### T05 - Device fairness and low-priority progress (R05)

- **Given/when:** two equally backlogged devices in the same service class compete, one with one TCP flow and the other with eight. Separately, run synthetic interactive/streaming traffic with one or two continuously backlogged bulk devices; repeat with intent enabled. Stay inside the four-device envelope.
- **Pass:** evaluate per-device service rather than only per-flow service; meet PT04 and PT05. Report delivery windows, demand and the longest zero-delivery interval. Unknown traffic receives documented fair default treatment. Do not infer unfairness merely from a low-demand call receiving fewer bytes than a download.
- **Evidence:** the R05 artifacts include each device's demand, flow count, intended service, address-family mapping and measured goodput. These are empirical criteria, not claims of hard CAKE bandwidth reservations.

### T06 - Temporary intent lifecycle (R06)

- **Given/when:** create a 30-second priority for a selected observed flow while competing traffic runs; test cancellation, duplicate/conflicting requests, invalid selectors, restart and controller failure.
- **Pass:** a valid intent changes effective service and matching counters; unrelated traffic is not silently promoted. Invalid requests have no policy effect and conflicts have deterministic, documented resolution. Cancellation/expiry meet PT09, including with the UI closed. Restart does not revive expired intent. Controller failure removes temporary grants through fallback. T05 progress checks still pass.
- **Evidence:** the R06 artifacts record request, target, start, expiry, effective policy, cancellation and actual enforcement times. Thirty seconds is a chosen test duration, not a fixed product limit.

### T07 - Address-family coverage (R07)

- **Given/when:** verify IPv4 routing and then equivalent IPv6 routing, upload/download observation, classification, marking, queue traversal and intent treatment in the reference lab.
- **Pass:** both supported families traverse the full client-gateway-ISP-server path and appear in records/counters; representative contention and failure checks include IPv6. An unavailable IPv6 capability is recorded as a scoped exception with the failed preflight evidence, not as a passing IPv6 test. IPv4 cannot be waived.
- **Evidence:** the R07 artifacts include addressing, forward/return routes and separate family-specific results. An IPv6 ping alone does not establish enforcement support.

### T08 - Invalid and partially applied policy rollback (R08)

- **Given/when:** preserve reconstructable configuration, reject an invalid candidate, inject a failed command and fail the second direction after the first was changed.
- **Pass:** rejection leaves installed state unchanged. Partial application is detected, followed by read-back-verified restoration to a valid previous policy or the documented fallback if the previous policy is unsuitable for current conditions. Connectivity and bulk progress remain observable. Do not assume queue changes and nftables changes form one atomic transaction.
- **Evidence:** the R08 artifacts include before, attempted and final rates/markings plus endpoint checks; a rollback log without state inspection is insufficient.

### T09 - Stale data, controller crash and safe state (R09)

- **Given/when:** stop the collector, fail probes, kill the controller and restart it with expired intent state; observe from a separate process.
- **Pass:** stale data becomes visible and exploration stops; stale/probe failure triggers the documented degraded/fallback transition. An independent watchdog verifies the safe policy while the controller remains stopped, meeting PT06 for the crash case. The safe policy preserves forwarding, enforces configured bounded shaping, removes temporary grants and provides fair default service. Recovery records what failed and what state was installed.
- **Evidence:** the R09 artifacts include external timing, policy read-back and test traffic. The safe rates must be set and validated against the minimum supported lab profile before execution; unknown capacity outside that profile is a documented limitation.

### T10 - Dashboard and measurement provenance (R11)

- **Given/when:** display saved/live records for known traffic, counter resets, unavailable metrics and stale collection. Independently recompute a selected interval from raw observations.
- **Pass:** all six requested metric groups are present with units, timestamps and measurement scope. Rates reconcile within explained sampling/rounding differences; the differences and tolerance are declared before the check. Unavailable, no-demand and stale values are distinct from zero. Synthetic endpoint loss/jitter are labeled as instrumented lab measurements, not universal knowledge of encrypted applications. Policy and intent views reference actual events.
- **Evidence:** the R11 artifacts cross-reference record IDs and measurement definitions. Screenshots supplement the reconciliation rather than replacing it.

### T11 - Controlled comparisons, quality, stability and overhead (R12, R13)

- **Given/when:** run uncontrolled, competent static QoS and adaptive QoS modes in five paired repetitions per scored scenario, with counterbalanced mode order. Hold workload, ISP schedule, marking/classification and intent state constant when isolating adaptation; separately compare intent off/on.
- **Pass:** save raw data and per-run results, report across-run variation and meet PT01 for the chosen congested call-like/bulk scenario. Measure jitter and loss alongside latency, useful throughput and fairness; report ties at zero loss rather than inventing improvement. Report controller CPU/RSS, whole gateway/VM CPU with control off/on, action count, reversals and rate variability. Static QoS remains a comparison even when it equals or outperforms adaptation. Failed targets remain visible.
- **Evidence:** the R12/R13 artifacts include baseline configuration/rationale, quiet and loaded measurements, paired comparisons and overhead scope. A packet count is not a count of independent repeated experiments.

### T12 - Independent module contracts (R10)

- **Given/when:** exercise each section 4 contract with saved valid, boundary, stale and failed inputs; use an isolated Linux integration check for privileged effects.
- **Pass:** shared records validate units, timestamps, schema and unavailable states; each module's transitions, failures and success checks are exercised independently. Policy replay respects bounds/expiry without shell access. Actuator tests inspect installed state and observer tests cover counter resets. Module APIs and tested transitions agree with the architecture document.
- **Evidence:** the R10 artifacts identify fixtures and passed/failed contracts. A pure Windows test result cannot establish Linux packet-path enforcement.

### T13 - Scripted reset, demonstration and reproduction (R16, R18)

- **Given/when:** from the declared environment, run the scripted demo twice from reset; introduce selected conditions, collect data and generate reports. Interrupt a run and invoke cleanup/reset. Have another person or a fresh environment follow the procedure.
- **Pass:** the script recreates connectivity, preserves failed-run evidence, removes only project-owned resources and generates a report from actual records. Include generated evaluation feature data and separately held labels, plus workload-generator definitions/settings, seeds where supported, ISP queue/impairment settings and version manifests. Keep evaluator datasets unavailable to the runtime controller. Compare distributions and timing variation rather than requiring identical packet traces. The report traces every applicable requirement to artifacts and outcomes.
- **Evidence:** the R16/R18 artifacts include both run IDs, exact commands/configurations and actual condition timings. All advertised demo commands must actually exist by this gate.

### T14 - Documentation, licenses and production boundary (R14, R15, R17, R19)

- **Given/when:** review the final repository, distribution and a fresh setup execution against the document set.
- **Pass:** the architecture covers all six requested component groups and contracts. Setup records actual OS/kernel/tool/dependency versions, privileges, configuration, verification, reset and troubleshooting. A license inventory identifies dependencies and redistribution obligations and contains no unexplained third-party assets. Reports/UI identify emulation; limitations explain the model, synthetic workloads, hardware/security/scale/operational gaps, certification and proprietary integration needs.
- **Evidence:** the R14/R15/R17/R19 documents and review artifacts. Do not copy planned versions into a manifest as if tested.

### T15 - Repository and example-secret review (R20)

- **Given/when:** review tracked files and reachable repository history with a secret scanner plus manual review of configuration/examples before distribution.
- **Pass:** no unresolved credential/private-key/token finding remains; examples are explicitly fictitious. Record scanner scope, version, findings and disposition without copying secret values into evidence. If a real credential is discovered, treat rotation and history remediation as outstanding work before distribution.
- **Evidence:** the R20 review artifact. Ignore rules alone do not prove that a secret has never been committed.

### T16 - Conditional AI evaluation (R21)

- **Given/when:** only if AI is introduced, freeze held-out data and compare it with deterministic logic under matched enforcement, workloads and link conditions.
- **Pass when applicable:** show the chosen model improves a predeclared measurable outcome, with accuracy, unknown coverage, latency/CPU cost and failure behavior reported. Include model/data licenses. A conversational interface alone does not satisfy this requirement. If benefit is absent, retain the deterministic default and report the finding.
- **Evidence:** the R21 artifacts. When no AI is included, the final report may mark this check **Not applicable - deterministic implementation**, with rationale; it must not mark an unrun AI comparison as passed.

## 6. Metric definitions and initial project targets

These definitions make checks comparable. Use per-run statistics and paired-run summaries. Interval boundaries, sampling rates, warm-up/transient exclusions and rounding rules belong in the locked run configuration. Report unsupported measurements as unavailable with a reason.

| Metric | Definition and scope |
| --- | --- |
| Latency | Endpoint round-trip time in ms, with quiet baseline, median and p95 for the same interactive workload. Do not present RTT as one-way delay. |
| Jitter | For the initial echo workload, mean absolute difference between consecutive successful RTT samples, in ms. Label this explicitly as RTT variation; paced-UDP receiver inter-arrival variation is a separate metric with its own definition. |
| Loss | Missing unique numbered probe/datagram responses after the configured timeout divided by sent probes/datagrams, excluding duplicates. Record timeout/window treatment; this is endpoint test loss, not loss inferred from interface drops. |
| Throughput/goodput | Counter-derived traffic rate and endpoint useful-payload rate in bit/s, separately for each direction/device; state which is plotted. Never equate transmitted interface bytes with confirmed application delivery. |
| Queue depth | Per-interface/per-service queue backlog in bytes and packets from inspectable queue counters, with timestamps. Record drops and ECN marks separately. |
| Fairness | Jain's index `J = (sum(x_i))^2 / (n * sum(x_i^2))` for comparable, continuously backlogged devices in the same intended class. No-demand cases are unavailable; report per-device rates and longest zero-delivery interval too. |
| Classification accuracy | Per-class precision/recall/F1 and confusion matrix against evaluator-only labels. Macro-F1 averages the three known-category F1 values; predictions of unknown for a known label count as false negatives. Coverage is non-unknown predictions divided by known-labeled test windows. Evaluate deliberately ambiguous traffic separately. |
| Estimation accuracy | Relative absolute error `abs(estimate - true_capacity) / true_capacity` for known, positive emulated rates, scored per direction in declared loaded windows. Report mean error, coverage and unavailable periods separately; do not score a shaper setting as a capacity estimate. |
| Adaptation/stability | Detection is time to first justified post-change action; settlement is time to the start of five consecutive 1-second samples meeting PT02. Count actions, direction reversals and rate variance. Use independently recorded actual change time. |
| CPU/overhead | Controller process CPU time divided by elapsed time, as percentage of one logical core; RSS in bytes. Report whole-VM CPU separately, its normalization/vCPU count, and paired control-off/on differences. Shared namespaces do not provide hardware-specific per-router cost. |

The following **initial project targets** are design decisions for the declared synthetic lab. PT01-PT06 formalize the existing plan's proposed targets; PT07-PT09 add explicit classification, estimation and expiry criteria. They are not statements of achieved performance. Change a target only with a documented reason and a new configuration/target version before rerunning; retain the original failed result.

| ID | Initial target | Applicable check and interpretation |
| --- | --- | --- |
| PT01 | Median paired reduction in p95 interactive RTT of at least 50% versus uncontrolled QoS in the declared congested call-like/bulk scenario. | T11: five paired repetitions; report absolute RTT, jitter, loss and goodput too. A nonpositive uncontrolled p95 cannot produce a valid percentage. Retain bulk-progress checks; a latency win obtained by stopping useful delivery fails acceptance. |
| PT02 | Settle after each capacity drop within 15 seconds. | T03: in a demanded direction, shaping is 70-95% of actual emulated capacity and p95 RTT excess over quiet baseline is at most 20 ms for five consecutive 1-second windows. Shape-rate band and delay bound are chosen operational definitions, evaluated offline. Insufficient samples/demand cannot produce a pass. |
| PT03 | Recover aggregate useful goodput to at least 80% of restored directional capacity within 120 seconds and sustain it for 10 consecutive 1-second windows. | T03: maintain enough offered load for the full recovery phase; the plan's 90-120 seconds becomes a 120-second upper gate. Record the time of the first qualifying window and the confirmation time. |
| PT04 | Every continuously backlogged bulk test device receives at least 5% of aggregate delivered goodput in each full 30-second scored window, with no zero-delivery interval longer than 5 seconds. | T05/T06: one or two bulk devices inside the four-device supported workload envelope, including active intent. This is an empirical progress check, not a hard queue reservation or an unlimited-device guarantee. |
| PT05 | Jain's index at least 0.9 over each full 30-second steady-state window for the two equal-service backlogged devices. | T05: one-flow versus eight-flow test; do not apply across unequal intended classes or demand-limited applications. |
| PT06 | Externally verify the safe state within 5 seconds after the last valid controller heartbeat in the crash test. | T09: configure heartbeat interval at most 1 second, stale threshold at most 2 seconds, watchdog polling at most 1 second and allow at most 2 seconds for apply/verification. Measure actual completion rather than assuming these budgets are met. |
| PT07 | Known-category macro-F1 at least 0.75 and known-workload coverage at least 70%. | T02: a frozen held-out set includes at least 10 independent sessions per known category, with equal-duration evaluation per session and changed traffic parameters. Abstentions count as missed known labels; disclose session variation and ambiguous-case unknown rate. These are initial prototype targets, not real-app recognition claims. |
| PT08 | Mean relative absolute capacity-estimation error at most 20%, with estimates present for at least 80% of eligible loaded 1-second windows. | T03: score each direction separately, beginning 15 seconds after a change and ending at the next change; offered demand exceeds true capacity, and each plateau provides at least 30 scored windows. Low-demand windows are a separately reported negative case, not silently included or discarded to improve the score. |
| PT09 | Remove cancelled/expired intent from effective policy and verify enforcement within 2 seconds of accepted cancellation or expiry. | T06: a healthy controller meets this tolerance; a dead controller instead meets PT06 and leaves no temporary grants. Record request acceptance, expiry and kernel verification times independently. |

PT07 combines an accuracy floor with coverage so that predicting `unknown` for everything cannot pass. PT08 combines estimation error with availability so that reporting a few favorable estimates cannot pass. Their numerical values are initial prototype choices, not evidence of expected real-world accuracy. PT09 gives expiry a measurable tolerance for a controller intended to observe on approximately one-second intervals; execution must confirm that the budget is feasible.

No universal requirement is inferred that adaptive control must beat competent static QoS on every link. Stable-rate cases may favor static QoS. Packet loss already at zero cannot be reduced; jitter/loss effects and any regressions must remain in the report. CPU overhead must be measured as requested; the brief gives no CPU ceiling, and this first version does not invent a hardware-independent ceiling.

## 7. Evidence, status and step-01 completion

Planned run layout, to implement in later steps:

```text
results/<scenario>/<mode>/<run-id>/
  manifest.json       # git revision and dirty-state identity, versions, resources, seeds, timing
  scenario.json       # evaluator ground truth; unavailable to runtime estimator/classifier
  config.json         # frozen controller settings, metric definitions, safe policy, target version
  metrics.jsonl       # normalized timestamped measurements
  events.jsonl        # policy generations, intents, correction, failures and recovery
  raw/                # metadata counters, queue/interface snapshots, synthetic endpoint records
  datasets/           # generated evaluation features/labels; evaluator-only, no private payloads
  T01/ ... T16/       # applicable check-specific evidence named in section 3
  acceptance.json     # requirement/check IDs, outcome, reasons and evidence paths
  summary.json
  report.md
```

An acceptance entry must identify requirement IDs, test ID, configuration/target version, run IDs, status, actual measurements, reason and evidence paths. Valid runtime statuses are **Not run**, **Pass**, **Fail**, **Blocked** (with failed prerequisite), and **Not applicable** (only with a justified condition such as no AI). Missing required evidence cannot be replaced with a screenshot or a claim. Documentation and repository-review checks may use a dedicated review run ID.

**Current implementation status:** R01-R21 are specified; full QoS acceptance procedures T01-T16 are **Not run**. The Linux environment and phase-3 shared package/configuration contracts are implemented and have separate verification records. IPv6 capability was verified in preflight; complete gateway IPv6 enforcement remains part of later integration checks. The observer/classifier/estimator algorithms, actuator, watchdog, intent service, dashboard and benchmark evidence still need implementation. R21 is conditional and can be marked not applicable in the eventual deterministic release.

**Step-01 documentation gate:**

- [x] Every required feature and constraint on PDF pages 2-3 has an ID and source reference, including conditional IPv6 and AI behavior.
- [x] Each requirement has a responsible module, an acceptance procedure and named planned evidence.
- [x] The first-version scope explicitly uses synthetic traffic in an isolated Linux lab and states production boundaries.
- [x] Required behavior is separated from tool choices, optional extensions and numerical project targets.
- [x] Metric meanings, negative/failure checks, module contracts and evidence status are defined without invented results.

Step 01 is complete when these documentation checks are reviewed. Passing them does not satisfy the later implementation acceptance checks. Consult the implementation checklist for current build progress.
