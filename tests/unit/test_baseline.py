from copy import deepcopy

import pytest

from adaptive_qos.baseline import gateway_qos_delta, packet_metrics
from adaptive_qos.lab import LabError
from adaptive_qos.workload import SECOND


def packet(event, sequence, timestamp_ms, size=1200):
    return {"event": event, "sequence": sequence, "monotonic_ns": timestamp_ms * 1_000_000,
            "payload_bytes": size}


def test_call_delivery_uses_send_window_and_ignores_duplicate_arrivals():
    sent = [packet("sent", index, stamp) for index, stamp in enumerate((980, 1000, 1010, 1020, 2000))]
    arrived = [packet("received", index, stamp) for index, stamp in ((1, 1005), (1, 1006), (2, 1018), (4, 2005))]
    metrics = packet_metrics(sent, arrived, (SECOND, 2 * SECOND))
    assert metrics["packets_sent"] == 3
    assert metrics["packets_delivered"] == 2
    assert metrics["offered_payload_bps"] == 28_800
    assert metrics["delivered_payload_bps"] == 19_200
    assert metrics["mean_absolute_spacing_change_ms"] == 3


def test_echo_rtt_omits_late_and_missing_replies_and_counts_deadline_misses():
    sent = [packet("sent", index, stamp) for index, stamp in enumerate((0, 20, 40, 60))]
    arrived = [packet("received", index, stamp) for index, stamp in ((0, 4), (1, 26), (2, 1041))]
    metrics = packet_metrics(sent, arrived, (0, SECOND), echo=True)
    assert metrics == {"requests": 4, "replies_within_deadline": 2, "deadline_miss_pct": 50,
                       "median_rtt_ms": 5, "p95_rtt_ms": 6, "mean_absolute_rtt_change_ms": 2}


def test_static_capture_rejects_queue_option_drift_with_unchanged_profile():
    queue = {"kind": "cake", "dev": "wan0", "handle": "7101:",
             "options": {"bandwidth": 562_500, "rtt": 100_000}, "packets": 5, "bytes": 512, "drops": 0,
             "tins": [{"sent_packets": 5, "sent_bytes": 512, "drops": 0}]}
    before = {"profile": {"mode": "besteffort"}, "queues": {"upload": queue, "download": deepcopy(queue)},
              "marking": None}
    after = deepcopy(before)
    after["queues"]["upload"]["options"]["rtt"] = 30_000

    with pytest.raises(LabError, match="upload CAKE queue configuration changed"):
        gateway_qos_delta(before, after, 4)
