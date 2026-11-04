"""Validate and replay explicitly simulated example records without network access."""

from collections import Counter
import json
from pathlib import Path
from typing import Annotated

from pydantic import Field

from adaptive_qos.clock import FakeClock, intent_is_active, is_fresh
from adaptive_qos.config import RuntimeConfig
from adaptive_qos.models import (
    ApplyResult, Classification, Event, FlowWindow, Intent, LinkEstimate, MetricSample,
    Policy, PolicyVerifiedPayload, RecordType, ValueModel, VersionOne,
    record_from_json, record_to_json, reject_duplicate_keys,
)
from adaptive_qos.validation import ensure_apply_matches_policy, validate_intent_context, validate_policy_context


class InvalidCase(ValueModel):
    filename: Annotated[str, Field(pattern=r"^[a-z0-9_-]+\.json$")]
    expected_fragment: Annotated[str, Field(min_length=1)]


class InvalidIndex(ValueModel):
    schema_version: VersionOne
    cases: Annotated[tuple[InvalidCase, ...], Field(min_length=1)]


def replay_records(records: tuple[RecordType, ...], config: RuntimeConfig) -> dict:
    if len({record.record_id for record in records}) != len(records):
        raise ValueError("duplicate record ID")
    if len({record.run_id for record in records}) != 1 or len({record.boot_id for record in records}) != 1:
        raise ValueError("fixture chain must share one run and boot")
    if any(record.source_kind != "synthetic_fixture" for record in records):
        raise ValueError("contract replay accepts only synthetic_fixture records")

    def one(kind):
        matches = [record for record in records if isinstance(record, kind)]
        if len(matches) != 1:
            raise ValueError(f"fixture replay requires exactly one {kind.__name__}")
        return matches[0]

    flow, prediction, metric = one(FlowWindow), one(Classification), one(MetricSample)
    intent, policy, applied, event = one(Intent), one(Policy), one(ApplyResult), one(Event)
    estimates = [record for record in records if isinstance(record, LinkEstimate)]
    if len(estimates) != 2 or {record.direction for record in estimates} != {"upload", "download"}:
        raise ValueError("fixture replay requires one link estimate for each direction")
    if prediction.flow_id != flow.flow.flow_id or prediction.window_id != flow.window_id:
        raise ValueError("classification does not reference the observed flow window")
    if metric.scope != "flow" or metric.target_id != flow.flow.flow_id:
        raise ValueError("metric does not reference the observed flow")
    for estimate in estimates:
        if estimate.observed_rate_bps != flow.rate_bps(estimate.direction):
            raise ValueError("link observed rate does not match flow accounting")
        if not {flow.record_id, metric.record_id}.issubset(estimate.input_record_ids):
            raise ValueError("link estimate lacks observation references")
    clock = FakeClock(flow.boot_id, max(record.observed_mono_ns for record in records))
    flows = {flow.flow.flow_id: flow.flow}
    inputs = {record.record_id: record for record in records}
    validate_intent_context(intent, config, clock, owner_epoch=policy.issuer_epoch, flows=flows)
    validate_policy_context(policy, config, clock, issuer_epoch=policy.issuer_epoch, inputs=inputs, flows=flows)
    ensure_apply_matches_policy(policy, applied)
    if not isinstance(event.payload, PolicyVerifiedPayload) or (
        event.payload.policy_id != policy.policy_id or event.payload.apply_result_id != applied.record_id
        or event.payload.generation != policy.generation or event.payload.execution != applied.execution
        or not {policy.record_id, applied.record_id}.issubset(event.caused_by_record_ids)
    ):
        raise ValueError("verification event does not reference the validated policy and result")
    if applied.execution != "simulated":
        raise ValueError("contract example must not claim real enforcement")
    if len(policy.treatments) != 1 or policy.treatments[0].correction is None:
        raise ValueError("example must demonstrate a separate classification correction")
    treatment = policy.treatments[0]
    if treatment.predicted_category != prediction.predicted_category:
        raise ValueError("classification prediction was overwritten")
    clock.advance_ms((intent.expires_mono_ns - clock.now_ns() + 999999) // 1_000_000)
    if intent_is_active(intent, clock, owner_epoch=policy.issuer_epoch):
        raise ValueError("intent remained active at expiry")
    if is_fresh(flow, clock, config.controller.telemetry_max_age_ms):
        raise ValueError("old observation remained fresh")
    try:
        validate_policy_context(policy, config, clock, issuer_epoch=policy.issuer_epoch, inputs=inputs, flows=flows)
    except ValueError as exc:
        if "lease has expired" not in str(exc):
            raise
    else:
        raise ValueError("expired policy was accepted")
    return {
        "execution": "simulated", "network_changes": False,
        "upload_observed_rate_bps": flow.rate_bps("upload"),
        "download_observed_rate_bps": flow.rate_bps("download"),
        "estimated_capacity_bps": {record.direction: record.estimated_capacity_bps for record in estimates},
        "predicted_category": prediction.predicted_category,
        "corrected_category": treatment.correction.category,
        "effective_service_class": treatment.service_class,
        "intent_expired": True, "policy_expired": True, "observation_stale": True,
    }


def check_contracts(examples: str | Path, config: RuntimeConfig) -> dict:
    root = Path(examples)
    valid_files = sorted((root / "valid").glob("*.json"))
    if not valid_files:
        raise ValueError("no valid contract examples found")
    records = tuple(record_from_json(path.read_bytes()) for path in valid_files)
    expected = {"flow_window", "classification", "link_estimate", "intent", "policy", "apply_result", "metric_sample", "event"}
    counts = Counter(record.record_type for record in records)
    if set(counts) != expected:
        raise ValueError("examples must cover all eight record types")
    for record in records:
        if record_from_json(record_to_json(record)) != record:
            raise ValueError("record JSON round-trip changed data")
    raw_index = json.loads((root / "invalid/index.json").read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys)
    index = InvalidIndex.model_validate_json(json.dumps(raw_index), strict=True)
    filenames = [case.filename for case in index.cases]
    if len(set(filenames)) != len(filenames):
        raise ValueError("duplicate invalid example filename")
    for case in index.cases:
        try:
            record_from_json((root / "invalid" / case.filename).read_bytes())
        except ValueError as exc:
            if case.expected_fragment not in str(exc).lower():
                raise ValueError(f"{case.filename}: failed for an unexpected reason") from exc
        else:
            raise ValueError(f"invalid example was accepted: {case.filename}")
    return {
        "status": "pass", "scope": "synthetic contract examples; no QoS benchmark or enforcement claim",
        "valid_records": len(records), "record_types": dict(sorted(counts.items())),
        "invalid_records_rejected": len(index.cases), "runtime_settings_sha256": config.effective_sha256,
        "replay": replay_records(records, config),
    }
