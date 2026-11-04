"""Context-dependent checks over explicit inputs, with no network side effects."""

from collections.abc import Mapping

from adaptive_qos.clock import Clock, age_ns, intent_is_active, is_fresh
from adaptive_qos.config import RuntimeConfig
from adaptive_qos.models import (
    ApplyResult,
    Classification,
    FlowIdentity,
    FlowWindow,
    Intent,
    LinkEstimate,
    MetricSample,
    Policy,
    RecordType,
)


def validate_intent_context(
    intent: Intent,
    config: RuntimeConfig,
    clock: Clock,
    *,
    owner_epoch: str,
    flows: Mapping[str, FlowIdentity],
) -> None:
    flow = flows.get(intent.selector.flow_id)
    if (flow is None or flow.flow_id != intent.selector.flow_id
            or flow.device_id != intent.selector.device_id
            or flow.namespace != config.controller.targets.namespace):
        raise ValueError("intent selector does not match an observed flow/device")
    if intent.duration_ms > config.controller.max_intent_duration_ms:
        raise ValueError("intent duration exceeds configured maximum")
    if not intent_is_active(intent, clock, owner_epoch=owner_epoch):
        raise ValueError("intent is inactive, expired or belongs to a different controller epoch")


def validate_policy_context(
    policy: Policy,
    config: RuntimeConfig,
    clock: Clock,
    *,
    issuer_epoch: str,
    inputs: Mapping[str, RecordType],
    flows: Mapping[str, FlowIdentity],
    previous_policy: Policy | None = None,
) -> None:
    """Validate a NEW proposal; idempotent actuator retries are a later interface."""
    age_ns(policy, clock)
    if policy.issuer_epoch != issuer_epoch:
        raise ValueError("policy issuer epoch is not authorized")
    if policy.targets != config.controller.targets:
        raise ValueError("policy targets differ from trusted gateway bindings")
    for direction in ("upload", "download"):
        rate = getattr(policy, f"{direction}_shaper_rate_bps")
        bounds = getattr(config.controller, direction)
        if not bounds.minimum_bps <= rate <= bounds.maximum_bps:
            raise ValueError(f"policy {direction} rate is outside configured bounds")
    if previous_policy is not None:
        if previous_policy.issuer_epoch != issuer_epoch:
            raise ValueError("previous policy requires explicit epoch reconciliation")
        if policy.generation <= previous_policy.generation:
            raise ValueError("new proposal generation must increase within its epoch")
    if policy.mode == "safe":
        if any(getattr(policy, name) != getattr(config.safe_policy, name) for name in (
            "upload_shaper_rate_bps", "download_shaper_rate_bps"
        )):
            raise ValueError("safe policy rates differ from the validated fallback template")
        return
    if clock.now_ns() >= policy.valid_until_mono_ns:
        raise ValueError("policy lease has expired")
    if policy.valid_until_mono_ns - policy.observed_mono_ns > config.controller.policy_lease_ms * 1_000_000:
        raise ValueError("policy lease exceeds configured maximum")

    referenced = {}
    intents = {}
    for record_id in policy.input_record_ids:
        record = inputs.get(record_id)
        if record is None or record.record_id != record_id:
            raise ValueError("policy input record is missing or indexed under the wrong ID")
        if record.run_id != policy.run_id or record.source_kind != policy.source_kind:
            raise ValueError("policy input has a different run or evidence provenance")
        if record.observed_mono_ns > policy.observed_mono_ns:
            raise ValueError("policy references an input recorded after its decision")
        age_ns(record, clock)
        if isinstance(record, (FlowWindow, MetricSample, Classification, LinkEstimate)):
            maximum = config.controller.telemetry_max_age_ms
            if isinstance(record, (Classification, LinkEstimate)):
                maximum = min(maximum, record.valid_for_ms)
            if not is_fresh(record, clock, maximum):
                raise ValueError("policy input telemetry is stale")
        if isinstance(record, Intent):
            if record.intent_id in intents:
                raise ValueError("policy references multiple snapshots of the same intent")
            intents[record.intent_id] = record
        referenced[record_id] = record
    if policy.mode == "adaptive" and {
        item.direction for item in referenced.values() if isinstance(item, LinkEstimate)
    } != {"upload", "download"}:
        raise ValueError("adaptive policy requires estimates for both directions")

    for treatment in policy.treatments:
        flow = flows.get(treatment.flow_id)
        prediction = referenced.get(treatment.prediction_record_id)
        if (flow is None or flow.flow_id != treatment.flow_id
                or flow.device_id != treatment.device_id
                or flow.namespace != config.controller.targets.namespace):
            raise ValueError("policy treatment does not match an observed flow/device")
        if not isinstance(prediction, Classification) or prediction.flow_id != treatment.flow_id:
            raise ValueError("treatment prediction does not belong to the selected flow")
        if prediction.predicted_category != treatment.predicted_category:
            raise ValueError("treatment rewrites the original classification")
        if treatment.correction is not None and treatment.correction.expires_mono_ns <= clock.now_ns():
            raise ValueError("treatment correction has expired")
        if treatment.intent_id is not None:
            intent = intents.get(treatment.intent_id)
            if intent is None:
                raise ValueError("treatment intent is missing from policy inputs")
            validate_intent_context(intent, config, clock, owner_epoch=issuer_epoch, flows=flows)
            if intent.selector.flow_id != treatment.flow_id or intent.expires_mono_ns != treatment.intent_expires_mono_ns:
                raise ValueError("treatment changes the intent target or deadline")


def ensure_apply_matches_policy(policy: Policy, result: ApplyResult) -> None:
    if (result.policy_id, result.issuer_epoch, result.generation) != (
        policy.policy_id, policy.issuer_epoch, policy.generation
    ):
        raise ValueError("apply result identifies a different policy generation")
    if result.run_id != policy.run_id or result.source_kind != policy.source_kind or result.boot_id != policy.boot_id:
        raise ValueError("apply result has different provenance or clock domain")
    if result.observed_mono_ns < policy.observed_mono_ns:
        raise ValueError("apply result precedes the policy")
    if result.outcome != "verified":
        raise ValueError("apply result does not establish verified requested policy state")
    observed = {item.resource: item for item in result.resources}
    if observed["upload"].observed_rate_bps != policy.upload_shaper_rate_bps:
        raise ValueError("upload read-back differs from requested policy")
    if observed["download"].observed_rate_bps != policy.download_shaper_rate_bps:
        raise ValueError("download read-back differs from requested policy")
