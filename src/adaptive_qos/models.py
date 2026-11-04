"""Versioned, immutable records. Validation never reads files or changes networking."""

from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
import json
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")]
NamespaceName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")]
InterfaceName = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,14}$")]
NonNegativeInt = Annotated[int, Field(ge=0, le=2**64 - 1)]
PositiveInt = Annotated[int, Field(gt=0, le=2**64 - 1)]
NonNegativeFloat = Annotated[float, Field(ge=0)]
PositiveFloat = Annotated[float, Field(gt=0)]
Port = Annotated[int, Field(ge=0, le=65535)]
ReasonList = Annotated[tuple[Identifier, ...], Field(min_length=1)]
Category = Literal["interactive_like", "streaming_like", "bulk_like", "unknown"]
Confidence = Literal["low", "medium", "high", "unavailable"]
Direction = Literal["upload", "download"]
ServiceClass = Literal["interactive", "streaming", "best_effort", "background"]


def _integer_version(value: object) -> object:
    if type(value) is not int:
        raise ValueError("schema_version must be an integer")
    return value


VersionOne = Annotated[Literal[1], BeforeValidator(_integer_version)]


class ValueModel(BaseModel):
    model_config = ConfigDict(
        strict=True,
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
        validate_default=True,
        revalidate_instances="always",
    )


class Record(ValueModel):
    schema_version: VersionOne
    record_id: Identifier
    run_id: Identifier
    producer_instance_id: Identifier
    sequence: NonNegativeInt
    boot_id: Identifier
    created_at_utc: AwareDatetime
    observed_mono_ns: NonNegativeInt
    source_kind: Literal["runtime", "synthetic_fixture"]

    @field_validator("created_at_utc")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class ObservationWindow(ValueModel):
    start_mono_ns: NonNegativeInt
    end_mono_ns: PositiveInt

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.end_mono_ns <= self.start_mono_ns:
            raise ValueError("observation window must have positive duration")
        return self


class GatewayTargets(ValueModel):
    namespace: NamespaceName
    upload_interface: InterfaceName
    download_interface: InterfaceName

    @model_validator(mode="after")
    def distinct_interfaces(self) -> Self:
        if self.upload_interface == self.download_interface:
            raise ValueError("upload and download interfaces must differ")
        return self


class FlowIdentity(ValueModel):
    flow_id: Identifier
    device_id: Identifier
    namespace: NamespaceName
    address_family: Literal[4, 6]
    protocol: Literal["tcp", "udp"]
    local_ip: str
    local_port: Port
    remote_ip: str
    remote_port: Port
    orientation: Literal["household_lan_to_wan"]

    @field_validator("address_family", mode="before")
    @classmethod
    def integer_family(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("address_family must be an integer")
        return value

    @model_validator(mode="after")
    def matching_address_family(self) -> Self:
        for address in (self.local_ip, self.remote_ip):
            if "%" in address or ip_address(address).version != self.address_family:
                raise ValueError("endpoint IP must match address_family and have no zone suffix")
        return self


class TrafficCounters(ValueModel):
    bytes_delta: NonNegativeInt | None
    packets_delta: NonNegativeInt | None
    unavailable_reason: Identifier | None

    @model_validator(mode="after")
    def explicit_availability(self) -> Self:
        available = self.bytes_delta is not None and self.packets_delta is not None
        unavailable = self.bytes_delta is None and self.packets_delta is None
        if not (available or unavailable):
            raise ValueError("byte and packet deltas must be available together")
        if available == (self.unavailable_reason is not None):
            raise ValueError("unavailable counters require a reason; available counters forbid it")
        return self


class FlowWindow(Record):
    record_type: Literal["flow_window"]
    window_id: Identifier
    flow: FlowIdentity
    window: ObservationWindow
    upload: TrafficCounters
    download: TrafficCounters
    counter_source: Literal["conntrack", "synthetic_fixture"]

    @model_validator(mode="after")
    def window_timestamp(self) -> Self:
        if self.observed_mono_ns != self.window.end_mono_ns:
            raise ValueError("observed_mono_ns must equal the observation window end")
        return self

    def rate_bps(self, direction: Direction) -> float | None:
        if direction not in ("upload", "download"):
            raise ValueError("unknown direction")
        counters = self.upload if direction == "upload" else self.download
        if counters.bytes_delta is None:
            return None
        return 8 * counters.bytes_delta * 1_000_000_000 / (
            self.window.end_mono_ns - self.window.start_mono_ns
        )


class Classification(Record):
    record_type: Literal["classification"]
    flow_id: Identifier
    window_id: Identifier
    predicted_category: Category
    confidence_grade: Confidence
    reasons: ReasonList
    classifier_version: Identifier
    valid_for_ms: PositiveInt

    @model_validator(mode="after")
    def honest_confidence(self) -> Self:
        if (self.predicted_category == "unknown") != (self.confidence_grade == "unavailable"):
            raise ValueError("unknown predictions use unavailable confidence; known ones require a grade")
        return self


class LinkEstimate(Record):
    record_type: Literal["link_estimate"]
    direction: Direction
    observed_rate_bps: NonNegativeFloat | None
    observed_rate_source: Literal["conntrack", "interface", "instrumented_endpoint", "synthetic_fixture"]
    estimated_capacity_bps: PositiveFloat | None
    capacity_lower_bound_bps: NonNegativeFloat
    capacity_upper_bound_bps: PositiveFloat | None
    estimate_source: Literal["inferred", "operator_nominal", "unavailable"]
    unavailable_reason: Identifier | None
    recommended_shaper_rate_bps: PositiveInt
    rate_recommendation_source: Literal["configured_start", "bounded_controller", "safe_template"]
    confidence_grade: Confidence
    load_state: Literal["idle", "demand_limited", "saturated", "uncertain"]
    probe_health: Literal["healthy", "degraded", "unavailable"]
    input_record_ids: ReasonList
    reasons: ReasonList
    valid_for_ms: PositiveInt

    @model_validator(mode="after")
    def estimate_consistency(self) -> Self:
        missing = self.estimated_capacity_bps is None
        if missing != (self.estimate_source == "unavailable"):
            raise ValueError("capacity availability must match estimate_source")
        if missing != (self.unavailable_reason is not None):
            raise ValueError("unavailable capacity requires a reason")
        if missing != (self.confidence_grade == "unavailable"):
            raise ValueError("capacity availability must match confidence")
        if self.confidence_grade == "high" and (
            self.load_state in ("idle", "demand_limited") or self.estimate_source == "operator_nominal"
        ):
            raise ValueError("low demand or configured nominal rates cannot justify high confidence")
        lower, upper, estimate = (
            self.capacity_lower_bound_bps,
            self.capacity_upper_bound_bps,
            self.estimated_capacity_bps,
        )
        if upper is not None and upper < lower:
            raise ValueError("capacity uncertainty bounds are reversed")
        if estimate is not None and (estimate < lower or (upper is not None and estimate > upper)):
            raise ValueError("capacity estimate lies outside its uncertainty bounds")
        return self


class FlowSelector(ValueModel):
    flow_id: Identifier
    device_id: Identifier


class Intent(Record):
    record_type: Literal["intent"]
    intent_id: Identifier
    owner_epoch: Identifier
    selector: FlowSelector
    goal: Literal["protect_interaction"]
    state: Literal["pending", "active", "cancelled", "expired", "rejected"]
    requester: Identifier
    starts_at_utc: AwareDatetime
    expires_at_utc: AwareDatetime
    start_mono_ns: NonNegativeInt
    expires_mono_ns: PositiveInt
    duration_ms: PositiveInt
    cancelled_at_mono_ns: NonNegativeInt | None
    rejection_reason: Identifier | None

    @field_validator("starts_at_utc", "expires_at_utc")
    @classmethod
    def utc_times(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def lifecycle_consistency(self) -> Self:
        if self.expires_mono_ns != self.start_mono_ns + self.duration_ms * 1_000_000:
            raise ValueError("intent monotonic expiry must match duration_ms")
        try:
            expected_expiry = self.starts_at_utc + timedelta(milliseconds=self.duration_ms)
        except OverflowError as exc:
            raise ValueError("intent duration exceeds the timestamp range") from exc
        if self.expires_at_utc != expected_expiry:
            raise ValueError("intent UTC expiry must match duration_ms")
        if self.observed_mono_ns < self.start_mono_ns:
            raise ValueError("intent record precedes its start")
        if self.state == "active" and self.observed_mono_ns >= self.expires_mono_ns:
            raise ValueError("intent is already expired at observation time")
        if self.state == "expired" and self.observed_mono_ns < self.expires_mono_ns:
            raise ValueError("expired state precedes expiry")
        if (self.state == "cancelled") != (self.cancelled_at_mono_ns is not None):
            raise ValueError("cancelled state requires cancellation time")
        if self.cancelled_at_mono_ns is not None and not (
            self.start_mono_ns <= self.cancelled_at_mono_ns <= self.observed_mono_ns
        ):
            raise ValueError("cancellation time is outside the observed lifetime")
        if (self.state == "rejected") != (self.rejection_reason is not None):
            raise ValueError("rejected state requires rejection_reason")
        return self


class CategoryCorrection(ValueModel):
    correction_id: Identifier
    category: Category
    expires_mono_ns: PositiveInt


class FlowTreatment(ValueModel):
    flow_id: Identifier
    device_id: Identifier
    prediction_record_id: Identifier
    predicted_category: Category
    correction: CategoryCorrection | None
    service_class: ServiceClass
    basis: Literal["classifier", "intent", "correction", "default"]
    intent_id: Identifier | None
    intent_expires_mono_ns: PositiveInt | None

    @model_validator(mode="after")
    def treatment_provenance(self) -> Self:
        if (self.intent_id is None) != (self.intent_expires_mono_ns is None):
            raise ValueError("intent reference and deadline must appear together")
        if self.basis == "intent" and self.intent_id is None:
            raise ValueError("intent treatment requires an intent reference")
        if self.basis == "correction" and self.correction is None:
            raise ValueError("correction treatment requires a correction")
        if self.predicted_category == "unknown" and self.correction is None and self.intent_id is None:
            if self.service_class != "best_effort" or self.basis != "default":
                raise ValueError("unknown traffic without user input requires fair default service")
        return self


class Policy(Record):
    record_type: Literal["policy"]
    policy_id: Identifier
    issuer_epoch: Identifier
    generation: PositiveInt
    mode: Literal["static", "adaptive", "safe"]
    upload_shaper_rate_bps: PositiveInt
    download_shaper_rate_bps: PositiveInt
    targets: GatewayTargets
    default_service_class: Literal["best_effort"]
    treatments: tuple[FlowTreatment, ...]
    input_record_ids: ReasonList
    reasons: ReasonList
    valid_until_mono_ns: PositiveInt | None

    @model_validator(mode="after")
    def policy_validity(self) -> Self:
        ids = [item.flow_id for item in self.treatments]
        if len(ids) != len(set(ids)):
            raise ValueError("policy contains duplicate flow treatments")
        if self.mode == "safe":
            if self.treatments or self.valid_until_mono_ns is not None:
                raise ValueError("safe policy has no temporary grants and remains until replaced")
        elif self.valid_until_mono_ns is None or self.valid_until_mono_ns <= self.observed_mono_ns:
            raise ValueError("managed policy requires a future finite lease")
        for treatment in self.treatments:
            if treatment.intent_expires_mono_ns is not None:
                if treatment.intent_expires_mono_ns <= self.observed_mono_ns:
                    raise ValueError("policy includes expired intent")
                if self.valid_until_mono_ns > treatment.intent_expires_mono_ns:
                    raise ValueError("policy lease exceeds intent expiry")
            if treatment.correction is not None:
                if treatment.correction.expires_mono_ns <= self.observed_mono_ns:
                    raise ValueError("policy includes expired correction")
                if self.valid_until_mono_ns > treatment.correction.expires_mono_ns:
                    raise ValueError("policy lease exceeds correction expiry")
        return self


class ErrorDetail(ValueModel):
    code: Identifier
    message: Annotated[str, Field(min_length=1, max_length=512)]


class ResourceResult(ValueModel):
    resource: Literal["upload", "download", "marking"]
    status: Literal["not_attempted", "applied", "verified", "failed"]
    exit_code: int | None
    observed_rate_bps: PositiveInt | None
    observed_rules_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None
    error: ErrorDetail | None

    @model_validator(mode="after")
    def resource_consistency(self) -> Self:
        if self.status in ("applied", "verified") and (self.exit_code != 0 or self.error is not None):
            raise ValueError("successful resource status requires exit code zero and no error")
        if self.status == "failed" and self.error is None:
            raise ValueError("failed resource requires an error")
        if self.status == "not_attempted" and any(
            item is not None for item in (self.exit_code, self.observed_rate_bps, self.observed_rules_sha256, self.error)
        ):
            raise ValueError("not_attempted resource cannot report execution or read-back")
        if self.resource == "marking" and self.observed_rate_bps is not None:
            raise ValueError("marking resource has no shaping rate")
        if self.resource != "marking" and self.observed_rules_sha256 is not None:
            raise ValueError("queue resource has no ruleset hash")
        if self.status == "verified":
            observed = self.observed_rules_sha256 if self.resource == "marking" else self.observed_rate_bps
            if observed is None:
                raise ValueError("verified resource requires read-back evidence")
        return self


class ApplyResult(Record):
    record_type: Literal["apply_result"]
    policy_id: Identifier
    issuer_epoch: Identifier
    generation: PositiveInt
    execution: Literal["simulated", "kernel"]
    outcome: Literal["rejected", "failed", "partial", "applied_unverified", "verified", "restored", "restoration_failed"]
    resources: tuple[ResourceResult, ...]
    restored_policy_id: Identifier | None
    error: ErrorDetail | None

    @model_validator(mode="after")
    def truthful_outcome(self) -> Self:
        names = [item.resource for item in self.resources]
        if len(names) != len(set(names)):
            raise ValueError("duplicate resource result")
        if self.source_kind == "synthetic_fixture" and self.execution != "simulated":
            raise ValueError("fixture apply results must be simulated")
        statuses = [item.status for item in self.resources]
        if self.outcome in ("verified", "restored"):
            if set(names) != {"upload", "download", "marking"} or any(s != "verified" for s in statuses):
                raise ValueError("fully verified outcome requires all three verified resources")
            if self.error is not None:
                raise ValueError("fully verified outcome cannot carry an error")
        if self.outcome in ("restored", "restoration_failed"):
            if self.restored_policy_id is None:
                raise ValueError("restoration outcome requires restored_policy_id")
        elif self.restored_policy_id is not None:
            raise ValueError("restored_policy_id requires a restoration outcome")
        if self.outcome in ("rejected", "failed", "partial", "restoration_failed") and self.error is None:
            raise ValueError("unsuccessful outcome requires an error")
        if self.outcome == "partial" and not (
            any(s in ("applied", "verified") for s in statuses)
            and any(s in ("failed", "not_attempted") for s in statuses)
        ):
            raise ValueError("partial outcome requires mixed resource results")
        if self.outcome == "applied_unverified" and (
            set(names) != {"upload", "download", "marking"}
            or any(s not in ("applied", "verified") for s in statuses)
            or all(s == "verified" for s in statuses)
            or self.error is not None
        ):
            raise ValueError("applied_unverified requires complete application and pending verification")
        if self.outcome == "rejected" and any(s != "not_attempted" for s in statuses):
            raise ValueError("rejected policy cannot have mutated a resource")
        if self.outcome == "failed" and any(s in ("applied", "verified") for s in statuses):
            raise ValueError("mixed success must be reported as partial")
        return self


MetricName = Literal[
    "rtt_ms", "rtt_variation_ms", "endpoint_loss_fraction", "interface_rate_bps",
    "endpoint_goodput_bps", "queue_backlog_bytes", "queue_backlog_packets",
    "jain_index", "cpu_one_core_pct", "vm_cpu_pct", "rss_bytes",
]
METRIC_UNITS = {
    "rtt_ms": "ms", "rtt_variation_ms": "ms", "endpoint_loss_fraction": "fraction",
    "interface_rate_bps": "bps", "endpoint_goodput_bps": "bps", "queue_backlog_bytes": "bytes",
    "queue_backlog_packets": "packets", "jain_index": "fraction",
    "cpu_one_core_pct": "percent", "vm_cpu_pct": "percent", "rss_bytes": "bytes",
}


class MetricSample(Record):
    record_type: Literal["metric_sample"]
    metric: MetricName
    value: NonNegativeInt | NonNegativeFloat | None
    unit: Literal["ms", "fraction", "bps", "bytes", "packets", "percent"]
    scope: Literal["flow", "device", "interface", "gateway", "vm"]
    target_id: Identifier
    measurement_source: Literal["conntrack", "tc", "interface", "instrumented_endpoint", "process", "vm", "synthetic_fixture"]
    direction: Direction | None
    address_family: Literal[4, 6] | None
    window: ObservationWindow
    sample_count: NonNegativeInt
    unavailable_reason: Identifier | None

    @field_validator("address_family", mode="before")
    @classmethod
    def integer_family(cls, value: object) -> object:
        if value is not None and type(value) is not int:
            raise ValueError("address_family must be an integer")
        return value

    @model_validator(mode="after")
    def metric_consistency(self) -> Self:
        if self.unit != METRIC_UNITS[self.metric]:
            raise ValueError("metric unit does not match its definition")
        if self.observed_mono_ns != self.window.end_mono_ns:
            raise ValueError("metric observation must equal the window end")
        if (self.value is None) != (self.unavailable_reason is not None):
            raise ValueError("unavailable metric requires a reason")
        if self.value is not None:
            if self.sample_count == 0:
                raise ValueError("measured value requires samples")
            if self.metric in ("queue_backlog_bytes", "queue_backlog_packets", "rss_bytes") and type(self.value) is not int:
                raise ValueError("count metrics require an integer value")
            if self.metric in ("endpoint_loss_fraction", "jain_index") and self.value > 1:
                raise ValueError("fraction must be between zero and one")
            if self.metric == "vm_cpu_pct" and self.value > 100:
                raise ValueError("whole-VM normalized CPU cannot exceed 100 percent")
        return self


class PolicyVerifiedPayload(ValueModel):
    kind: Literal["policy_verified"]
    policy_id: Identifier
    apply_result_id: Identifier
    generation: PositiveInt
    execution: Literal["simulated", "kernel"]


class PolicyFailedPayload(ValueModel):
    kind: Literal["policy_failed"]
    policy_id: Identifier
    apply_result_id: Identifier
    reason: Identifier


class IntentPayload(ValueModel):
    kind: Literal["intent_created", "intent_expired", "intent_cancelled"]
    intent_id: Identifier
    flow_id: Identifier


class TelemetryStalePayload(ValueModel):
    kind: Literal["telemetry_stale"]
    stale_record_id: Identifier
    reason: Identifier


class SafeStatePayload(ValueModel):
    kind: Literal["safe_state_entered"]
    policy_id: Identifier
    reason: Identifier


EventPayload = Annotated[
    PolicyVerifiedPayload | PolicyFailedPayload | IntentPayload | TelemetryStalePayload | SafeStatePayload,
    Field(discriminator="kind"),
]


class Event(Record):
    record_type: Literal["event"]
    severity: Literal["info", "warning", "error"]
    correlation_id: Identifier
    caused_by_record_ids: ReasonList
    payload: EventPayload

    @model_validator(mode="after")
    def fixture_event(self) -> Self:
        if self.source_kind == "synthetic_fixture" and isinstance(self.payload, PolicyVerifiedPayload):
            if self.payload.execution != "simulated":
                raise ValueError("fixture verification events must be simulated")
        return self


RecordType = FlowWindow | Classification | LinkEstimate | Intent | Policy | ApplyResult | MetricSample | Event
RECORD_ADAPTER = TypeAdapter(Annotated[RecordType, Field(discriminator="record_type")])


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def record_from_json(data: str | bytes) -> RecordType:
    """Validate one strict JSON record, including duplicate keys and JSON constants."""
    parsed = json.loads(data, object_pairs_hook=reject_duplicate_keys, parse_constant=_reject_constant)
    if not isinstance(parsed, dict):
        raise ValueError("record must be a JSON object")
    return RECORD_ADAPTER.validate_json(json.dumps(parsed, allow_nan=False), strict=True)


def record_to_json(record: RecordType) -> str:
    validated = RECORD_ADAPTER.validate_python(record, strict=True)
    return validated.model_dump_json(indent=2)
