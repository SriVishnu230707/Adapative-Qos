# Recorded verification environments

- `ubuntu-24.04-virtualbox.json` is the original phase-2 snapshot: Ubuntu 24.04.5, kernel 6.8.0-147-generic, four vCPUs, 3 GiB RAM and 34 passing capability checks with a planning-resource warning.
- `phase-03-verification.json` records application package checks after recovery from a later early-boot clock stall. The VM used one vCPU and 3 GiB RAM. It includes source hashes, pinned versions, unit-test outcomes, a fresh wheel installation outside the source directory and standalone preflight compatibility.
- `phase-04-verification.json` records real IPv4/IPv6 TCP delivery in both topologies, repeated setup/reset, privileged lifecycle/failure checks, source hashes, environment versions and main-namespace preservation. It also records the namespace-inode reuse defect found during testing and the persistent-reference fix.
- `phase-05-verification.json` records measured ISP throughput, scheduled transitions, queue traversal, failure recovery, private schedule access, test counts and source hashes. [Phase 5 verification](phase-05-verification.json)
- `phase-06-verification.json` records the IPv4 and IPv6 uncontrolled workload summaries, raw capture hashes, test counts, and restored lab state. [Step 6 verification](phase-06-verification.json)

The phase-3 example apply results are explicitly simulated. These files establish environment and contract verification; full QoS performance and recovery acceptance requires the later implementation and experiments. Historical snapshots are retained rather than rewritten to describe a different run.
