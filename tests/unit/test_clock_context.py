from datetime import datetime, timedelta

import pytest

from adaptive_qos.clock import ClockDomainError, FakeClock, age_ns, intent_is_active, is_fresh
from adaptive_qos.models import ApplyResult, FlowWindow, Intent, Policy
from adaptive_qos.validation import ensure_apply_matches_policy, validate_intent_context, validate_policy_context


def context(records):
    inputs = {record.record_id: record for record in records}
    flow = next(record for record in records if isinstance(record, FlowWindow))
    flows = {flow.flow.flow_id: flow.flow}
    policy = next(record for record in records if isinstance(record, Policy))
    clock = FakeClock(flow.boot_id, 2_000_000_000)
    return inputs, flows, policy, clock


def test_freshness_at_exact_deadline(records):
    inputs, _, _, clock = context(records)
    record = inputs["record-flow-1"]
    assert age_ns(record, clock) == 0
    clock.advance_ms(2499)
    assert is_fresh(record, clock, 2500)
    clock.advance_ms(1)
    assert not is_fresh(record, clock, 2500)


def test_clock_domain_and_future_observation(records):
    record = records[0]
    with pytest.raises(ClockDomainError, match="different boot"):
        age_ns(record, FakeClock("other-boot", 2_000_000_000))
    with pytest.raises(ClockDomainError, match="future"):
        age_ns(record, FakeClock(record.boot_id, 1_000_000_000))


@pytest.mark.parametrize("value", [-1, True, 0.5])
def test_clock_rejects_invalid_advancement(value):
    with pytest.raises(ValueError):
        FakeClock("boot-1").advance_ms(value)


def test_expiry_and_restart_are_conservative(records):
    inputs, _, policy, clock = context(records)
    intent = inputs["record-intent-1"]
    assert intent_is_active(intent, clock, owner_epoch=policy.issuer_epoch)
    assert not intent_is_active(intent, clock, owner_epoch="restarted-controller")
    assert not intent_is_active(intent, FakeClock("restarted-boot", clock.now_ns()), owner_epoch=policy.issuer_epoch)
    clock.advance_ms(30000)
    assert not intent_is_active(intent, clock, owner_epoch=policy.issuer_epoch)
    assert intent.state == "active"  # A historical snapshot does not establish current validity.


def test_linked_policy_and_intent_context(records, runtime):
    inputs, flows, policy, clock = context(records)
    validate_intent_context(inputs["record-intent-1"], runtime, clock, owner_epoch=policy.issuer_epoch, flows=flows)
    validate_policy_context(policy, runtime, clock, issuer_epoch=policy.issuer_epoch, inputs=inputs, flows=flows)
    ensure_apply_matches_policy(policy, inputs["record-apply-1"])


def test_intent_selector_requires_registry_membership(payload, parse, records, runtime):
    _, flows, policy, clock = context(records)
    data = payload("intent.json")
    data["selector"]["device_id"] = "other-device"
    with pytest.raises(ValueError, match="observed flow/device"):
        validate_intent_context(parse(data), runtime, clock, owner_epoch=policy.issuer_epoch, flows=flows)


def test_configured_intent_maximum_is_enforced(payload, parse, records, runtime):
    _, flows, policy, clock = context(records)
    data = payload("intent.json")
    duration = runtime.controller.max_intent_duration_ms + 1
    data["duration_ms"] = duration
    data["expires_mono_ns"] = data["start_mono_ns"] + duration * 1_000_000
    start = datetime.fromisoformat(data["starts_at_utc"])
    data["expires_at_utc"] = (start + timedelta(milliseconds=duration)).isoformat()
    with pytest.raises(ValueError, match="configured maximum"):
        validate_intent_context(parse(data), runtime, clock, owner_epoch=policy.issuer_epoch, flows=flows)


@pytest.mark.parametrize("change,fragment", [
    (lambda d: d.update(upload_shaper_rate_bps=21_000_000), "outside configured bounds"),
    (lambda d: d["targets"].update(namespace="other-ns"), "trusted gateway"),
    (lambda d: d.update(issuer_epoch="old-epoch"), "not authorized"),
    (lambda d: d["treatments"][0].update(device_id="other-device"), "observed flow/device"),
    (lambda d: d["treatments"][0].update(predicted_category="streaming_like"), "rewrites"),
    (lambda d: d.update(input_record_ids=["missing-record"]), "missing"),
])
def test_policy_context_rejects_inconsistent_inputs(change, fragment, payload, parse, records, runtime):
    inputs, flows, _, clock = context(records)
    data = payload("policy.json")
    change(data)
    with pytest.raises(ValueError, match=fragment):
        validate_policy_context(parse(data), runtime, clock, issuer_epoch="controller-epoch-1", inputs=inputs, flows=flows)


def test_new_generation_and_epoch_require_explicit_order(records, runtime):
    inputs, flows, policy, clock = context(records)
    with pytest.raises(ValueError, match="generation must increase"):
        validate_policy_context(policy, runtime, clock, issuer_epoch=policy.issuer_epoch, inputs=inputs, flows=flows, previous_policy=policy)


def test_lease_and_input_freshness_are_separate(records, runtime):
    inputs, flows, policy, clock = context(records)
    clock.advance_ms(2500)
    with pytest.raises(ValueError, match="telemetry is stale"):
        validate_policy_context(policy, runtime, clock, issuer_epoch=policy.issuer_epoch, inputs=inputs, flows=flows)
    clock.advance_ms(2500)
    with pytest.raises(ValueError, match="lease has expired"):
        validate_policy_context(policy, runtime, clock, issuer_epoch=policy.issuer_epoch, inputs=inputs, flows=flows)


def test_policy_cannot_use_future_input(payload, parse, records, runtime):
    inputs, flows, policy, clock = context(records)
    data = payload("classification.json")
    data["observed_mono_ns"] = 3_000_000_000
    inputs[data["record_id"]] = parse(data)
    clock.advance_ms(1000)
    with pytest.raises(ValueError, match="after its decision"):
        validate_policy_context(policy, runtime, clock, issuer_epoch=policy.issuer_epoch, inputs=inputs, flows=flows)


def test_safe_template_works_without_fresh_telemetry(payload, parse, records, runtime):
    _, flows, _, clock = context(records)
    data = payload("policy.json")
    data.update(mode="safe", valid_until_mono_ns=None, treatments=[],
                upload_shaper_rate_bps=3_000_000, download_shaper_rate_bps=15_000_000)
    policy = parse(data)
    clock.advance_ms(30000)
    validate_policy_context(policy, runtime, clock, issuer_epoch=policy.issuer_epoch, inputs={}, flows=flows)


def test_verified_result_must_match_requested_rates(payload, parse, records):
    _, _, policy, _ = context(records)
    data = payload("apply-result.json")
    data["resources"][1]["observed_rate_bps"] = 89_000_000
    with pytest.raises(ValueError, match="download read-back"):
        ensure_apply_matches_policy(policy, parse(data))


def test_verified_result_must_match_epoch_and_generation(payload, parse, records):
    _, _, policy, _ = context(records)
    data = payload("apply-result.json")
    data["generation"] = 2
    with pytest.raises(ValueError, match="different policy generation"):
        ensure_apply_matches_policy(policy, parse(data))


def test_policy_rejects_ambiguous_intent_snapshots(payload, parse, records, runtime):
    inputs, flows, policy, clock = context(records)
    data = payload("intent.json")
    data.update(record_id="record-intent-cancelled", sequence=7, state="cancelled",
                cancelled_at_mono_ns=2_000_000_000)
    cancelled = parse(data)
    inputs[cancelled.record_id] = cancelled
    policy_data = payload("policy.json")
    policy_data["input_record_ids"].append(cancelled.record_id)
    with pytest.raises(ValueError, match="multiple snapshots"):
        validate_policy_context(parse(policy_data), runtime, clock, issuer_epoch=policy.issuer_epoch,
                                inputs=inputs, flows=flows)


@pytest.mark.parametrize("consumer", ["intent", "policy"])
def test_registry_key_must_match_actual_flow_identity(consumer, records, runtime):
    inputs, flows, policy, clock = context(records)
    original = flows["flow-1"]
    flows["flow-1"] = original.model_copy(update={"flow_id": "other-flow"})
    with pytest.raises(ValueError, match="observed flow/device"):
        if consumer == "intent":
            validate_intent_context(inputs["record-intent-1"], runtime, clock,
                                    owner_epoch=policy.issuer_epoch, flows=flows)
        else:
            validate_policy_context(policy, runtime, clock, issuer_epoch=policy.issuer_epoch,
                                    inputs=inputs, flows=flows)
