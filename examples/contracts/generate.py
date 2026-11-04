"""Regenerate deterministic SYNTHETIC contract examples; no networking is performed."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

from adaptive_qos.models import record_from_json


ROOT = Path(__file__).resolve().parent


def metadata(kind, name, sequence):
    return {
        "schema_version": 1, "record_type": kind, "record_id": name,
        "run_id": "fixture-run-1", "producer_instance_id": "fixture-producer-1",
        "sequence": sequence, "boot_id": "fixture-boot-1",
        "created_at_utc": "2026-11-04T00:00:02Z", "observed_mono_ns": 2_000_000_000,
        "source_kind": "synthetic_fixture",
    }


def main():
    window = {"start_mono_ns": 1_000_000_000, "end_mono_ns": 2_000_000_000}
    targets = {"namespace": "qos-gw", "upload_interface": "wan0", "download_interface": "lan0"}
    flow = metadata("flow_window", "record-flow-1", 1) | {
        "window_id": "window-1",
        "flow": {"flow_id": "flow-1", "device_id": "device-1", "namespace": "qos-gw",
                 "address_family": 4, "protocol": "udp", "local_ip": "10.10.0.2", "local_port": 40000,
                 "remote_ip": "10.30.0.2", "remote_port": 50000, "orientation": "household_lan_to_wan"},
        "window": window, "upload": {"bytes_delta": 250000, "packets_delta": 250, "unavailable_reason": None},
        "download": {"bytes_delta": 1000000, "packets_delta": 1000, "unavailable_reason": None},
        "counter_source": "synthetic_fixture",
    }
    classification = metadata("classification", "record-classification-1", 2) | {
        "flow_id": "flow-1", "window_id": "window-1", "predicted_category": "bulk_like",
        "confidence_grade": "medium", "reasons": ["synthetic_example"],
        "classifier_version": "fixture-rules-v1", "valid_for_ms": 2500,
    }
    metric = metadata("metric_sample", "record-metric-1", 3) | {
        "metric": "rtt_ms", "value": 8.0, "unit": "ms", "scope": "flow", "target_id": "flow-1",
        "measurement_source": "synthetic_fixture", "direction": None, "address_family": 4,
        "window": window, "sample_count": 10, "unavailable_reason": None,
    }
    estimates = []
    for number, direction, observed, shaper in ((4, "upload", 2_000_000.0, 18_000_000), (5, "download", 8_000_000.0, 90_000_000)):
        estimates.append(metadata("link_estimate", f"record-link-{direction}", number) | {
            "direction": direction, "observed_rate_bps": observed, "observed_rate_source": "synthetic_fixture",
            "estimated_capacity_bps": None, "capacity_lower_bound_bps": observed, "capacity_upper_bound_bps": None,
            "estimate_source": "unavailable", "unavailable_reason": "demand_limited",
            "recommended_shaper_rate_bps": shaper, "rate_recommendation_source": "configured_start",
            "confidence_grade": "unavailable", "load_state": "demand_limited", "probe_health": "healthy",
            "input_record_ids": ["record-flow-1", "record-metric-1"],
            "reasons": ["demand_limited", "configured_start"], "valid_for_ms": 2500,
        })
    intent = metadata("intent", "record-intent-1", 6) | {
        "intent_id": "intent-1", "owner_epoch": "controller-epoch-1",
        "selector": {"flow_id": "flow-1", "device_id": "device-1"},
        "goal": "protect_interaction", "state": "active", "requester": "fixture-user",
        "starts_at_utc": "2026-11-04T00:00:02Z", "expires_at_utc": "2026-11-04T00:00:32Z",
        "start_mono_ns": 2_000_000_000, "expires_mono_ns": 32_000_000_000,
        "duration_ms": 30000, "cancelled_at_mono_ns": None, "rejection_reason": None,
    }
    policy = metadata("policy", "record-policy-1", 7) | {
        "policy_id": "policy-1", "issuer_epoch": "controller-epoch-1", "generation": 1, "mode": "static",
        "upload_shaper_rate_bps": 18_000_000, "download_shaper_rate_bps": 90_000_000,
        "targets": targets, "default_service_class": "best_effort",
        "treatments": [{"flow_id": "flow-1", "device_id": "device-1",
                        "prediction_record_id": "record-classification-1", "predicted_category": "bulk_like",
                        "correction": {"correction_id": "correction-1", "category": "interactive_like", "expires_mono_ns": 32_000_000_000},
                        "service_class": "interactive", "basis": "intent", "intent_id": "intent-1",
                        "intent_expires_mono_ns": 32_000_000_000}],
        "input_record_ids": ["record-flow-1", "record-classification-1", "record-metric-1",
                             "record-link-upload", "record-link-download", "record-intent-1"],
        "reasons": ["configured_start", "temporary_intent"], "valid_until_mono_ns": 7_000_000_000,
    }
    apply = metadata("apply_result", "record-apply-1", 8) | {
        "policy_id": "policy-1", "issuer_epoch": "controller-epoch-1", "generation": 1,
        "execution": "simulated", "outcome": "verified", "restored_policy_id": None, "error": None,
        "resources": [
            {"resource": "upload", "status": "verified", "exit_code": 0, "observed_rate_bps": 18_000_000, "observed_rules_sha256": None, "error": None},
            {"resource": "download", "status": "verified", "exit_code": 0, "observed_rate_bps": 90_000_000, "observed_rules_sha256": None, "error": None},
            {"resource": "marking", "status": "verified", "exit_code": 0, "observed_rate_bps": None,
             "observed_rules_sha256": hashlib.sha256(b"SYNTHETIC marking snapshot").hexdigest(), "error": None},
        ],
    }
    event = metadata("event", "record-event-1", 9) | {
        "severity": "info", "correlation_id": "correlation-1",
        "caused_by_record_ids": ["record-policy-1", "record-apply-1"],
        "payload": {"kind": "policy_verified", "policy_id": "policy-1", "apply_result_id": "record-apply-1",
                    "generation": 1, "execution": "simulated"},
    }
    valid = {"flow-window.json": flow, "classification.json": classification, "metric-sample.json": metric,
             "link-upload.json": estimates[0], "link-download.json": estimates[1], "intent.json": intent,
             "policy.json": policy, "apply-result.json": apply, "event.json": event}
    invalid = []

    def case(name, source, fragment, change):
        value = deepcopy(source)
        change(value)
        invalid.append((name, value, fragment))

    case("negative-rate", policy, "greater than 0", lambda d: d.update(upload_shaper_rate_bps=-1))
    case("boolean-rate", policy, "valid integer", lambda d: d.update(upload_shaper_rate_bps=True))
    case("string-rate", policy, "valid integer", lambda d: d.update(download_shaper_rate_bps="90000000"))
    case("truth-leak", flow, "extra inputs", lambda d: d.update(ground_truth_label="fixture-only-label"))
    case("reversed-window", flow, "positive duration", lambda d: d["window"].update(start_mono_ns=3_000_000_000))
    case("missing-counter", flow, "available together", lambda d: d["upload"].update(bytes_delta=None))
    case("unknown-confidence", classification, "unavailable confidence", lambda d: d.update(predicted_category="unknown"))
    case("idle-capacity-confidence", estimates[0], "cannot justify high confidence", lambda d: d.update(
        estimated_capacity_bps=100_000_000.0, estimate_source="inferred", unavailable_reason=None, confidence_grade="high", load_state="idle"))
    case("expired-active-intent", intent, "already expired", lambda d: d.update(observed_mono_ns=32_000_000_000))

    def partial(value):
        value["resources"][1].update(status="failed", exit_code=1, observed_rate_bps=None,
                                    error={"code": "fixture_failure", "message": "Synthetic failure"})
    case("partial-as-verified", apply, "all three verified", partial)
    case("metric-unit", metric, "unit does not match", lambda d: d.update(unit="bps"))
    case("address-family", flow, "match address_family", lambda d: d["flow"].update(local_ip="fd42:ad:10::2"))
    case("expired-policy", policy, "future finite lease", lambda d: d.update(valid_until_mono_ns=2_000_000_000))
    case("fixture-kernel-result", apply, "must be simulated", lambda d: d.update(execution="kernel"))
    case("schema-version", flow, "should be 1", lambda d: d.update(schema_version=2))
    case("boolean-schema", flow, "must be an integer", lambda d: d.update(schema_version=True))

    for directory in (ROOT / "valid", ROOT / "invalid"):
        directory.mkdir(parents=True, exist_ok=True)
    for name, data in valid.items():
        encoded = json.dumps(data, indent=2, allow_nan=False) + "\n"
        record_from_json(encoded)
        (ROOT / "valid" / name).write_text(encoded, encoding="utf-8", newline="\n")
    index = []
    for name, data, fragment in invalid:
        encoded = json.dumps(data, indent=2, allow_nan=False) + "\n"
        try:
            record_from_json(encoded)
        except ValueError as exc:
            if fragment not in str(exc).lower():
                raise AssertionError(f"{name}: unexpected validation error: {exc}") from exc
        else:
            raise AssertionError(f"{name}: invalid fixture was accepted")
        filename = name + ".json"
        (ROOT / "invalid" / filename).write_text(encoded, encoding="utf-8", newline="\n")
        index.append({"filename": filename, "expected_fragment": fragment})
    (ROOT / "invalid" / "index.json").write_text(
        json.dumps({"schema_version": 1, "cases": index}, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"Generated {len(valid)} synthetic valid records and {len(invalid)} rejected examples.")


if __name__ == "__main__":
    main()
