# Step 7 static gateway QoS

The actuator installs fixed CAKE queues on both gateway interfaces and verifies the settings directly from the kernel. The reference profile shapes upload at **4.5 Mbit/s** on `wan0` and download at **18 Mbit/s** on `lan0` for the 5/20 Mbit/s ISP scenario. Those values leave a 10% margin in the emulator. The controller configuration supplies the permitted rate bounds. [Reference static profile](../configs/static-qos.yaml)

Upload uses source-host isolation. Download uses destination-host isolation and CAKE's `ingress` accounting mode. The download queue sits on LAN egress after the ISP bottleneck. It can influence responsive senders, and delay that packets have already experienced upstream still contributes to measured RTT. [CAKE manual](https://man7.org/linux/man-pages/man8/tc-cake.8.html)

The profile selects `diffserv4`. A dedicated nftables forward chain assigns DSCP using the configured synthetic client addresses and the direction of traversal. It covers IPv4 and IPv6. TCP and UDP for mapped clients receive the configured treatment. Other forwarded IP traffic between the two gateway interfaces receives CS0. This uses the lab's device assignments as an input. Traffic inference comes in step 9. [nftables manual](https://netfilter.org/projects/nftables/manpage.html)

| Service class | DSCP | Lab traffic |
| --- | --- | --- |
| Interactive | EF (46) | Call-like and game-like clients |
| Streaming | AF41 (34) | TV-like client |
| Best effort | CS0 (0) | Forwarded IP traffic without a matching static rule |
| Background | CS1 (8) | Bulk TCP client |

A custom profile may select `besteffort` with an empty `marks` list to exercise CAKE with a single tin. The run uses the operator-selected rates in either mode.

## Apply, inspect and restore

Start a fresh four-device topology in the dedicated Ubuntu VM.

```bash
sudo bash lab/up.sh --config configs/lab-multi.yaml
sudo bash lab/isp_profile.sh install
sudo bash lab/isp_profile.sh apply --download-bps 20000000 --upload-bps 5000000
sudo .venv/bin/python -m adaptive_qos.actuator apply
sudo .venv/bin/python -m adaptive_qos.actuator inspect
sudo .venv/bin/python -m adaptive_qos.actuator restore
sudo .venv/bin/python -m adaptive_qos.actuator inspect
sudo bash lab/down.sh
```

`apply` checks the rate bounds, configured devices, journaled namespace and interface identities, and existing queues. It checks the proposed nftables transaction before changing the gateway. It records the installation in the root-owned lab journal and verifies both kernel queue settings and the marking table after application. Keep the topology and actuator commands serialized through the lab journal lock.

`inspect` reports the installed rates, queue and tin counters, and per-family marking counters. `restore` removes the journaled CAKE roots and marking table, then verifies the default gateway queues. Failed or interrupted applications retain sufficient journal state to retry restoration. A foreign queue or an altered marking table requires reconciliation before restoration. The independent watchdog and adaptive fallback belong to later steps.

New installations record the verified kernel queue configuration in the journal. `inspect` rejects later changes to options such as CAKE's RTT setting. Restoration still works for the owned queues after their options change. Capture replay checks the before-and-after queue configuration in both directions, including in recordings created before the journal began storing it.

## Run a matched comparison

Begin with a fresh lab and the ISP profile from the first three commands above. Run the same durations and workloads in each mode. Use a new output directory for every capture.

```bash
for family in 4 6; do
  sudo .venv/bin/python -m adaptive_qos.baseline run \
    --family "$family" --mode uncontrolled --output "work/step07/uncontrolled-v$family"
  sudo .venv/bin/python -m adaptive_qos.actuator apply
  sudo .venv/bin/python -m adaptive_qos.baseline run \
    --family "$family" --mode static --output "work/step07/static-v$family"
  sudo .venv/bin/python -m adaptive_qos.baseline compare \
    "work/step07/uncontrolled-v$family" "work/step07/static-v$family"
  sudo .venv/bin/python -m adaptive_qos.actuator restore
done
sudo bash lab/down.sh
```

The comparison checks that the topology configuration, address family, ISP rates, workload settings, requested durations and reported tool versions match. The static capture checks both CAKE roots, packet progress in the expected tins, marking counters for the selected address family and bulk payload delivery in both directions. The raw capture includes before/after read-back and the network and endpoint observations described in the baseline guide. [Measurement definitions](uncontrolled-baseline.md)

For focused kernel checks, run the opt-in integration tests with the lab down.

```bash
sudo env AQOS_RUN_STATIC_INTEGRATION=1 .venv/bin/python -m pytest -q tests/integration/test_static_qos.py
```

These tests check rejection of an out-of-bounds policy before any change, best-effort installation, IPv4 and IPv6 fallback marking, removal, and recovery after an injected failure during application. The tests also check preservation of unrelated state.

## Reference measurements

One matched pair per address family ran at 20 Mbit/s down and 5 Mbit/s up in the Ubuntu lab. The pairs used the same synthetic workloads and requested durations. IPv4 ran static first. IPv6 ran uncontrolled first. [Verification record](environments/phase-07-verification.json)

| Address family | Loaded echo p95 RTT, uncontrolled → static | One-second echo deadline misses | Bulk download goodput | Bulk upload goodput |
| --- | ---: | ---: | ---: | ---: |
| IPv4 | 992.1 → 17.9 ms | 91.4% → 0% | 0.99 → 12.52 Mbit/s | 3.98 → 3.07 Mbit/s |
| IPv6 | 991.1 → 20.5 ms | 95.6% → 0% | 0.41 → 12.00 Mbit/s | 3.94 → 2.97 Mbit/s |

RTT percentiles use replies that arrived within one second, so read them alongside the deadline miss percentages. Bulk upload goodput fell under static QoS. Both bulk receivers still recorded payload delivery in all 18 reported intervals of at least 0.75 seconds in each static run. The TV-like workload met eight of nine segment deadlines in each static run, compared with zero in each uncontrolled run. Call-like packet loss was zero during the measured load windows in both modes.

The three privileged integration checks passed. The default test suites passed 188 tests on Ubuntu and 187 on Windows. The final lab cleanup removed all eight project namespaces. [Test counts and capture checksums](environments/phase-07-verification.json)
