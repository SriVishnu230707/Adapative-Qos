import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from adaptive_qos.models import record_from_json, record_to_json

EXAMPLES = Path(__file__).resolve().parents[2] / "examples/contracts"
VALID = sorted(path.name for path in (EXAMPLES / "valid").glob("*.json"))
INVALID = json.loads((EXAMPLES / "invalid/index.json").read_text())["cases"]


@pytest.mark.parametrize("filename", VALID)
def test_every_record_round_trips(filename):
    record = record_from_json((EXAMPLES / "valid" / filename).read_bytes())
    assert record_from_json(record_to_json(record)) == record


@pytest.mark.parametrize("case", INVALID, ids=[case["filename"] for case in INVALID])
def test_invalid_examples_fail_for_declared_reason(case):
    with pytest.raises(ValueError) as error:
        record_from_json((EXAMPLES / "invalid" / case["filename"]).read_bytes())
    assert case["expected_fragment"] in str(error.value).lower()


def test_rates_use_bytes_bits_and_actual_elapsed_time(payload, parse):
    data = payload("flow-window.json")
    assert parse(data).rate_bps("upload") == 2_000_000
    assert parse(data).rate_bps("download") == 8_000_000
    data["window"]["end_mono_ns"] = data["observed_mono_ns"] = 3_000_000_000
    assert parse(data).rate_bps("download") == 4_000_000


def test_counter_reset_is_unavailable_not_zero(payload, parse):
    data = payload("flow-window.json")
    data["upload"] = {"bytes_delta": None, "packets_delta": None, "unavailable_reason": "counter_reset"}
    assert parse(data).rate_bps("upload") is None
    data["upload"] = {"bytes_delta": 0, "packets_delta": 0, "unavailable_reason": None}
    assert parse(data).rate_bps("upload") == 0


def test_ipv6_and_integer_family(payload, parse):
    data = payload("flow-window.json")
    data["flow"].update(address_family=6, local_ip="fd42:ad:10::2", remote_ip="fd42:ad:30::2")
    assert parse(data).flow.address_family == 6
    data["flow"]["address_family"] = 6.0
    with pytest.raises(ValueError, match="must be an integer"):
        parse(data)


def test_records_and_nested_values_are_frozen(payload, parse):
    data = payload("policy.json")
    record = parse(data)
    data["treatments"][0]["service_class"] = "background"
    assert record.treatments[0].service_class == "interactive"
    assert isinstance(record.treatments, tuple)
    with pytest.raises(ValidationError):
        record.generation = 2
    with pytest.raises(ValidationError):
        record.treatments[0].service_class = "background"


def test_unvalidated_copy_is_revalidated_before_serialization(payload, parse):
    record = parse(payload("policy.json"))
    corrupted = record.model_copy(update={"upload_shaper_rate_bps": -1})
    with pytest.raises(ValueError):
        record_to_json(corrupted)


@pytest.mark.parametrize("value", [True, "18000000", 18_000_000.0, 0, -1, 2**64])
def test_policy_rate_is_strict_positive_uint64(value, payload, parse):
    data = payload("policy.json")
    data["upload_shaper_rate_bps"] = value
    with pytest.raises(ValueError):
        parse(data)


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity", "1e309"])
def test_non_finite_json_numbers_are_rejected(constant, payload):
    text = json.dumps(payload("metric-sample.json")).replace('"value": 8.0', f'"value": {constant}')
    with pytest.raises(ValueError):
        record_from_json(text)


def test_duplicate_top_level_and_nested_json_keys_are_rejected(payload):
    text = json.dumps(payload("flow-window.json"))
    for original, duplicate in (
        ('"schema_version": 1', '"schema_version": 1, "schema_version": 2'),
        ('"device_id": "device-1"', '"device_id": "device-1", "device_id": "device-2"'),
    ):
        with pytest.raises(ValueError, match="duplicate key"):
            record_from_json(text.replace(original, duplicate, 1))


def test_naive_timestamp_rejected_and_offset_normalized(payload, parse):
    data = payload("flow-window.json")
    data["created_at_utc"] = "2026-11-04T00:00:02"
    with pytest.raises(ValueError):
        parse(data)
    data["created_at_utc"] = "2026-11-04T05:30:02+05:30"
    assert parse(data).created_at_utc.isoformat() == "2026-11-04T00:00:02+00:00"


def test_metric_zero_samples_are_not_zero_loss(payload, parse):
    data = payload("metric-sample.json")
    data.update(metric="endpoint_loss_fraction", unit="fraction", value=0.0, sample_count=0)
    with pytest.raises(ValueError, match="requires samples"):
        parse(data)
    data.update(value=None, unavailable_reason="no_samples")
    assert parse(data).value is None
    data.update(value=0.0, sample_count=10, unavailable_reason=None)
    assert parse(data).value == 0


def test_metric_cpu_normalization_and_integer_counts(payload, parse):
    data = payload("metric-sample.json")
    data.update(metric="cpu_one_core_pct", unit="percent", value=350.0)
    assert parse(data).value == 350
    data["metric"] = "vm_cpu_pct"
    with pytest.raises(ValueError, match="cannot exceed 100"):
        parse(data)
    data.update(metric="queue_backlog_bytes", unit="bytes", value=2**60)
    assert parse(data).value == 2**60
    assert record_from_json(record_to_json(parse(data))).value == 2**60
    data["value"] = 0.5
    with pytest.raises(ValueError, match="integer value"):
        parse(data)


def test_unknown_prediction_retains_unknown_capacity(payload, parse):
    data = payload("classification.json")
    data.update(predicted_category="unknown", confidence_grade="unavailable")
    assert parse(data).predicted_category == "unknown"
    estimate = parse(payload("link-download.json"))
    assert estimate.estimated_capacity_bps is None
    assert estimate.observed_rate_bps == 8_000_000
    assert estimate.recommended_shaper_rate_bps == 90_000_000


def test_partial_application_can_be_recorded_but_not_verified(payload, parse):
    data = payload("apply-result.json")
    error = {"code": "readback_mismatch", "message": "Synthetic mismatch"}
    data.update(outcome="partial", error=error)
    data["resources"][1].update(status="failed", exit_code=0, observed_rate_bps=None, error=error)
    assert parse(data).outcome == "partial"
    data["outcome"] = "verified"
    with pytest.raises(ValueError, match="all three verified"):
        parse(data)


def test_verification_requires_actual_readback_fields(payload, parse):
    data = payload("apply-result.json")
    data["resources"][0]["observed_rate_bps"] = None
    with pytest.raises(ValueError, match="requires read-back"):
        parse(data)


def test_event_payload_is_discriminated_and_closed(payload, parse):
    data = payload("event.json")
    data["payload"]["kind"] = "intent_expired"
    with pytest.raises(ValueError):
        parse(data)
    data = payload("event.json")
    data["payload"]["shell_command"] = "unused"
    with pytest.raises(ValueError, match="Extra inputs"):
        parse(data)
