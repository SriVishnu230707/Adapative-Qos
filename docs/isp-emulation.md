# ISP bandwidth emulation

Phase 5 installs independent bandwidth limits inside the routed lab. Download traffic leaves `qos-isp` through `gw0` toward the gateway. Upload traffic leaves through `srv0` toward the server. Both the single-client and four-device configurations declare these bindings. The runner checks the saved lab identity and interface ownership before changing queues.

| ISP egress | Initial capacity | Minimum supported capacity | Queue |
| --- | ---: | ---: | --- |
| `qos-isp/gw0` | 100 Mbit/s | 20 Mbit/s | HTB with a 1000-packet FIFO |
| `qos-isp/srv0` | 20 Mbit/s | 5 Mbit/s | HTB with a 1000-packet FIFO |

Each interface has one HTB default class with the same rate and ceiling. Both token buckets use a requested burst of 32 KiB. Scheduled events change the existing class rates, which preserves the queue hierarchy and its counters. The fixed packet limit represents more buffering time at lower rates. Actual delay depends on packet sizes, queue occupancy and the lab's offloads. [HTB manual](https://man7.org/linux/man-pages/man8/tc-htb.8.html) · [FIFO manual](https://man7.org/linux/man-pages/man8/tc-pfifo.8.html)

The runner verifies the root and leaf with `tc` JSON. It reads class rates through `RTM_GETTCLASS` and converts kernel bytes/s to bit/s. The reference VM's iproute2 emits formatted text for HTB class inspection even with `-j`. The read-only helper uses the Linux UAPI to preserve exact rates. [Linux HTB UAPI](https://github.com/torvalds/linux/blob/master/include/uapi/linux/pkt_sched.h)

## Run the emulator

From the dedicated Ubuntu VM's `/opt/adaptive-qos` checkout, set up the routed lab if needed and install the initial profile.

```bash
sudo bash lab/up.sh --config configs/lab-multi.yaml
sudo bash lab/isp_profile.sh install
sudo bash lab/isp_profile.sh inspect
```

`install` requires a ready lab, an available ISP journal and the default unshaped queues on both interfaces. `inspect` returns the recorded profile and fresh kernel read-back.

For a manual rate change, specify both capacities in integer bit/s. The supported range extends from each direction's declared minimum to its initial capacity. Rates must be representable as whole bytes/s.

```bash
sudo bash lab/isp_profile.sh apply --download-bps 20000000 --upload-bps 5000000
sudo bash lab/isp_profile.sh restore
```

Run one timed profile at a time in the foreground. The download template holds upload at 20 Mbit/s while download changes from 100 to 20 and back to 100. The upload template holds download at 100 Mbit/s while upload changes from 20 to 5 and back to 20. Both templates change rates at 60 and 120 seconds and finish at 270 seconds.

```bash
sudo bash lab/isp_profile.sh run --scenario experiments/scenarios/download-drop.yaml
# Alternatively, use experiments/scenarios/upload-drop.yaml.
```

From another terminal, `inspect` shows current read-back and `cancel` requests cancellation and waits for verified restoration. Both successful completion and cooperative cancellation restore the initial profile. `restore` also recovers the baseline after a worker exits abruptly. A failed restoration surfaces as an error with the residual state in the journal. `remove` releases the owned queue trees, and `lab/down.sh` cancels and removes the emulator before releasing the topology.

```bash
sudo bash lab/isp_profile.sh cancel
sudo bash lab/isp_profile.sh remove
sudo bash lab/down.sh
```

## Timing, ownership and ground truth

The worker schedules events against a monotonic run origin. Its journal stores the requested offset, operation start, per-direction verification time, requested rates, observed rates and UTC timestamp. Actual verified times determine the measurement windows. If an update fails midway, the runner attempts to restore the prior verified pair and records any rollback errors.

ISP operations use a separate transaction lock and worker registry under `/run/adaptive-qos-isp`. Traffic verification can hold the topology lock while the ISP worker updates its rates. Each mutation checks the lab run, namespace pin and interface identity. Cleanup coordinates with the scenario worker before deleting queues or namespaces.

The active journal and resolved schedule use root-only permissions. Public YAML files are templates. The live tests use the `nobody` identity to check that the private scenario file cannot be read. A future controller service needs its runtime input boundary checked under its own identity.

After a scenario stops, export its trace for offline analysis before removing its installation. Use a private output directory for the export.

```bash
umask 077
mkdir -p work/isp-evidence
sudo bash lab/isp_profile.sh report > work/isp-evidence/run.json
```

## Verify sustained delivery

The opt-in tests first measure unshaped headroom, then compare paced UDP sender and receiver payload rates with the configured limits. The probe batches eight 1,400-byte datagrams per send and measures three steady one-second windows in each six-second sample. Each capacity check requires actual offered load of at least 105% of the limit and receiver goodput between 75% and 115%. The tests exercise both address families, timed drops in each direction, four clients sharing one bottleneck, low offered demand, ownership protection, partial-update recovery, cancellation, and topology teardown during a scenario. They also compare the initial namespace's network configuration before and after the isolated lab run. Run them as root inside the dedicated VM.

```bash
sudo env AQOS_RUN_ISP_INTEGRATION=1 .venv/bin/python -m pytest tests/integration/test_isp_profiles.py -ra
```

The lab models bandwidth and buffering. Added delay and loss require separately validated queue composition. The measured throughput bands apply to the synthetic workloads and documented VM. Adaptive-controller acceptance experiments are a later milestone. [Phase 5 verification](environments/phase-05-verification.json)
