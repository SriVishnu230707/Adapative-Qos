"""Learn from the implemented contracts using synthetic examples and a fake clock.

Run from the repository with its installed Python environment. This walkthrough
reads repository examples; it makes no network changes and needs no root access.
"""

import json
from pathlib import Path

from adaptive_qos.clock import FakeClock, intent_is_active, is_fresh
from adaptive_qos.config import load_config_bundle
from adaptive_qos.contracts import replay_records
from adaptive_qos.models import Event, FlowWindow, Intent, LinkEstimate, Policy, record_from_json
from adaptive_qos.validation import validate_policy_context


ROOT = Path(__file__).resolve().parents[2]


def expect_rejection(label, action, expected):
    """A negative example succeeds only when it fails for the expected reason."""
    try:
        action()
    except ValueError as exc:
        if expected not in str(exc):
            raise AssertionError(f"{label}: unexpected rejection: {exc}") from exc
        print(f"  REJECTED: {label} ({expected})")
    else:
        raise AssertionError(f"{label}: invalid data was accepted")


def main():
    bundle = load_config_bundle(ROOT / "configs")
    runtime = bundle.runtime
    examples = ROOT / "examples/contracts/valid"
    records = tuple(record_from_json(path.read_bytes()) for path in sorted(examples.glob("*.json")))
    inputs = {record.record_id: record for record in records}
    flow = next(record for record in records if isinstance(record, FlowWindow))
    intent = next(record for record in records if isinstance(record, Intent))
    policy = next(record for record in records if isinstance(record, Policy))
    flows = {flow.flow.flow_id: flow.flow}

    print("1. Separate lab truth from controller settings")
    assert not hasattr(runtime, "lab") and not hasattr(runtime, "emulator")
    print(f"  Lab knows {len(bundle.lab.namespaces)} namespaces; runtime receives controller/fallback settings.")

    print("2. Calculate rates from bytes and elapsed time")
    assert flow.rate_bps("upload") == 2_000_000
    assert flow.rate_bps("download") == 8_000_000
    print(f"  Upload: {flow.upload.bytes_delta} bytes / 1 second * 8 = {flow.rate_bps('upload'):,.0f} bit/s")
    download = next(record for record in records if isinstance(record, LinkEstimate) and record.direction == "download")
    assert download.estimated_capacity_bps is None
    print(f"  Download observed={download.observed_rate_bps:,.0f}; configured shaper={download.recommended_shaper_rate_bps:,}; capacity=unknown.")

    print("3. Reject a numeric string used as a configured rate")
    data = json.loads((examples / "policy.json").read_text(encoding="utf-8"))
    data["upload_shaper_rate_bps"] = "18000000"
    expect_rejection("string instead of integer", lambda: record_from_json(json.dumps(data)), "valid integer")

    print("4. Preserve the distinction between unavailable and measured zero")
    data = json.loads((examples / "metric-sample.json").read_text(encoding="utf-8"))
    data.update(value=None, sample_count=0, unavailable_reason="no_samples")
    assert record_from_json(json.dumps(data)).value is None
    data.update(value=0.0, unavailable_reason=None)
    expect_rejection("measurement with zero samples", lambda: record_from_json(json.dumps(data)), "requires samples")

    print("5. Advance time without sleeping")
    clock = FakeClock(flow.boot_id, policy.observed_mono_ns)
    validate_policy_context(policy, runtime, clock, issuer_epoch=policy.issuer_epoch, inputs=inputs, flows=flows)
    assert is_fresh(flow, clock, runtime.controller.telemetry_max_age_ms)
    assert intent_is_active(intent, clock, owner_epoch=policy.issuer_epoch)
    clock.advance_ms(2500)
    assert not is_fresh(flow, clock, runtime.controller.telemetry_max_age_ms)
    assert clock.now_ns() < policy.valid_until_mono_ns
    expect_rejection("fresh lease with stale telemetry", lambda: validate_policy_context(
        policy, runtime, clock, issuer_epoch=policy.issuer_epoch, inputs=inputs, flows=flows), "telemetry is stale")
    clock.advance_ms(2500)
    expect_rejection("policy at its lease deadline", lambda: validate_policy_context(
        policy, runtime, clock, issuer_epoch=policy.issuer_epoch, inputs=inputs, flows=flows), "lease has expired")
    clock.advance_ms(25000)
    assert not intent_is_active(intent, clock, owner_epoch=policy.issuer_epoch)
    assert intent.state == "active"  # The old snapshot is unchanged; current validity is computed.
    print("  At t=32s, the saved state still says active, but current validity is False.")

    print("6. Check relationships as well as individual field types")
    event = next(record for record in records if isinstance(record, Event))
    data = json.loads(event.model_dump_json())
    data["observed_mono_ns"] = policy.observed_mono_ns - 1
    early_event = record_from_json(json.dumps(data))  # Shape-valid, but chronologically impossible.
    altered = tuple(early_event if record.record_id == event.record_id else record for record in records)
    expect_rejection("verification before its policy", lambda: replay_records(altered, runtime), "precedes")
    replay = replay_records(records, runtime)
    assert replay["execution"] == "simulated" and replay["network_changes"] is False
    print(f"  Original prediction={replay['predicted_category']}; correction={replay['corrected_category']}; treatment={replay['effective_service_class']}.")
    print("PASS: all six lessons matched the current implementation. No real QoS was applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
