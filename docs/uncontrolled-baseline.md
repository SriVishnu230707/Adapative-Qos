# Step 6 uncontrolled baseline

The four-device lab measures how ISP contention affects synthetic interactive traffic, segment delivery, and backlogged TCP. The gateway uses its default queues. The ISP retains its Phase 5 HTB rate limits and FIFO buffers.

| Device namespace | Synthetic traffic |
| --- | --- |
| `qos-client` | Bidirectional UDP with 1,200-byte payloads every 10 ms in each direction. |
| `qos-game` | A 64-byte UDP echo request every 20 ms with a one-second reply deadline. |
| `qos-tv` | A 1,000,000-byte HTTP segment every two seconds during the load period. A segment is due at the next scheduled slot, modeling two seconds of prefetch time. |
| `qos-bulk` | Simultaneous backlogged TCP upload and download using separate iperf3 server ports. |

Call and echo traffic run through the quiet, loaded, and recovery windows. The clock starts after the interactive endpoints report readiness. The runner records the TCP launch and observed exit times. It gives both flows one second to start, leaves a one-second guard at the end of the common load window, and waits one second after all load processes finish before scoring recovery. The load window is bounded by the requested TCP duration. TCP can drain for up to 24 seconds after its scheduled end.

## Reproduce the run

Use the dedicated Ubuntu VM. Start the four-device topology and install the ISP profile if the lab is down.

```bash
sudo bash lab/up.sh --config configs/lab-multi.yaml
sudo bash lab/isp_profile.sh install
```

The reference run uses 20 Mbit/s down and 5 Mbit/s up to produce sustained contention within the verified emulator range. Use a fresh output path for each run.

```bash
sudo bash lab/isp_profile.sh apply --download-bps 20000000 --upload-bps 5000000
sudo .venv/bin/python -m adaptive_qos.baseline run --family 4 --output work/step06/reference-v4
sudo .venv/bin/python -m adaptive_qos.baseline run --family 6 --output work/step06/reference-v6
sudo bash lab/isp_profile.sh restore
```

The runner holds the topology and ISP locks during capture. It validates namespace ownership, gateway queues, and the static ISP rates. It tracks child processes in the lab journal and stops them during cleanup. Each output directory is private to the runner. Failed runs retain their manifest and the measurements recorded before failure.

## Read the evidence

- `manifest.json` records the lab identity, address family, tool versions, requested durations, verified ISP rates, and final status.
- `events.jsonl` records monotonic process events, measurement windows, and approximately one-second snapshots of the gateway and ISP interfaces and queues. Snapshots include each namespace's collection time.
- Endpoint JSON and JSONL files preserve iperf3 intervals, UDP send and receive events, and HTTP segment outcomes. The records contain sequence IDs and payload lengths. The synthetic role mapping and generator settings live in `ground-truth.json` for offline evaluation. Keep the raw run directory out of runtime classifier inputs.
- `summary.json` is derived from the raw files. Regenerate it with the following command.

```bash
sudo .venv/bin/python -m adaptive_qos.baseline summarize work/step06/reference-v4
```

| Measurement | Definition |
| --- | --- |
| Echo RTT | Elapsed milliseconds from request send to first reply received within one second. The summary reports the median, nearest-rank p95, and mean absolute change between successive successful RTTs in send order. |
| Echo deadline misses | The percentage of transmitted requests with no reply within one second. Late and missing replies both count. |
| UDP call delivery | Match each transmitted sequence ID to its first arrival, including the final receive drain. The difference between transmitted and matched packets gives loss. Offered and delivered payload bits are divided by the originating send window duration. |
| UDP spacing variation | Mean absolute difference between receiver spacing and actual sender spacing for consecutive sequence IDs that both arrived. |
| Bulk TCP goodput | iperf3 receiver payload bits per second across the whole transfer. The receiver is the client for downloads and the server for uploads. |
| TV segments | Scheduled request slots, complete payloads, deadlines missed, and received payload bytes, including partial segments. The scheduled count includes any slot that elapsed before a request could start. A success needs all 1,000,000 bytes by the deadline. |
| Interface and queue counters | Kernel RX and TX byte deltas between the first and last snapshots, FIFO drop deltas in packets, and the highest sampled FIFO backlog in bytes during each window. |

An unavailable statistic appears as `null`. The reference record contains one run per IP family. Repeated trials and comparisons with gateway QoS belong to later steps. [Step 6 verification](environments/phase-06-verification.json)
