"""Offline setup validation and an explicitly smaller controller configuration."""

import hashlib
from ipaddress import ip_interface
import json
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, field_validator, model_validator
import yaml

from adaptive_qos.models import (
    GatewayTargets,
    Identifier,
    InterfaceName,
    NamespaceName,
    PositiveFloat,
    PositiveInt,
    ValueModel,
    VersionOne,
)


class ConfigurationError(ValueError):
    pass


class UniqueSafeLoader(yaml.SafeLoader):
    pass


def _unique_mapping(loader: UniqueSafeLoader, node: yaml.MappingNode, deep: bool = False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if type(key) is not str:
            raise ConfigurationError("configuration mapping keys must be strings")
        if key in result:
            raise ConfigurationError(f"duplicate configuration key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


class LabEndpoint(ValueModel):
    namespace: NamespaceName
    interface: InterfaceName
    ipv4_cidr: str
    ipv6_cidr: str

    @model_validator(mode="after")
    def addresses(self) -> Self:
        if ip_interface(self.ipv4_cidr).version != 4 or ip_interface(self.ipv6_cidr).version != 6:
            raise ValueError("lab endpoint CIDRs must match their address family")
        return self


class LabLink(ValueModel):
    name: Identifier
    a: LabEndpoint
    b: LabEndpoint

    @model_validator(mode="after")
    def connected_subnets(self) -> Self:
        if self.a.namespace == self.b.namespace:
            raise ValueError("link endpoints must belong to different namespaces")
        for name in ("ipv4_cidr", "ipv6_cidr"):
            first = ip_interface(getattr(self.a, name))
            second = ip_interface(getattr(self.b, name))
            if first.network != second.network or first.ip == second.ip:
                raise ValueError("link endpoints require distinct addresses on the same subnet")
        return self


class EmulatorProfile(ValueModel):
    initial_download_rate_bps: PositiveInt
    initial_upload_rate_bps: PositiveInt
    minimum_supported_download_rate_bps: PositiveInt
    minimum_supported_upload_rate_bps: PositiveInt
    queue_limit_packets: PositiveInt

    @model_validator(mode="after")
    def supported_envelope(self) -> Self:
        if self.initial_download_rate_bps < self.minimum_supported_download_rate_bps:
            raise ValueError("initial download is below the supported envelope")
        if self.initial_upload_rate_bps < self.minimum_supported_upload_rate_bps:
            raise ValueError("initial upload is below the supported envelope")
        return self


class LabConfig(ValueModel):
    schema_version: VersionOne
    namespaces: Annotated[tuple[NamespaceName, ...], Field(min_length=4)]
    gateway: GatewayTargets
    links: Annotated[tuple[LabLink, ...], Field(min_length=3)]
    emulator: EmulatorProfile
    synthetic_traffic: Literal[True]

    @field_validator("synthetic_traffic", mode="before")
    @classmethod
    def boolean_traffic_flag(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("synthetic_traffic must be Boolean")
        return value

    @model_validator(mode="after")
    def topology(self) -> Self:
        if len(set(self.namespaces)) != len(self.namespaces):
            raise ValueError("duplicate namespace")
        endpoints = [(e.namespace, e.interface) for link in self.links for e in (link.a, link.b)]
        if len(set(endpoints)) != len(endpoints):
            raise ValueError("duplicate namespace/interface endpoint")
        if len({link.name for link in self.links}) != len(self.links):
            raise ValueError("duplicate link name")
        if any(ns not in self.namespaces for ns, _ in endpoints):
            raise ValueError("link references an unknown namespace")
        if any((self.gateway.namespace, iface) not in endpoints for iface in (
            self.gateway.upload_interface, self.gateway.download_interface
        )):
            raise ValueError("gateway target is not a declared link endpoint")
        return self


class RateBounds(ValueModel):
    minimum_bps: PositiveInt
    initial_bps: PositiveInt
    maximum_bps: PositiveInt
    nominal_rate_bps: PositiveInt
    nominal_source: Literal["operator_configured"]

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if not self.minimum_bps <= self.initial_bps <= self.maximum_bps:
            raise ValueError("require minimum_bps <= initial_bps <= maximum_bps")
        if not self.minimum_bps <= self.nominal_rate_bps <= self.maximum_bps:
            raise ValueError("nominal rate must be inside configured bounds")
        return self


class HeartbeatBudget(ValueModel):
    interval_ms: PositiveInt
    stale_after_ms: PositiveInt
    watchdog_poll_ms: PositiveInt
    apply_verify_budget_ms: PositiveInt
    recovery_target_ms: PositiveInt

    @model_validator(mode="after")
    def feasible_budget(self) -> Self:
        if self.interval_ms >= self.stale_after_ms:
            raise ValueError("heartbeat interval must be below stale threshold")
        if self.stale_after_ms + self.watchdog_poll_ms + self.apply_verify_budget_ms > self.recovery_target_ms:
            raise ValueError("heartbeat recovery budget exceeds its target")
        return self


class ControllerConfig(ValueModel):
    schema_version: VersionOne
    targets: GatewayTargets
    upload: RateBounds
    download: RateBounds
    observation_interval_ms: PositiveInt
    telemetry_max_age_ms: PositiveInt
    policy_lease_ms: PositiveInt
    cooldown_ms: PositiveInt
    decrease_fraction: Annotated[PositiveFloat, Field(lt=1)]
    increase_fraction: Annotated[PositiveFloat, Field(lt=1)]
    max_intent_duration_ms: PositiveInt
    intent_expiry_tolerance_ms: PositiveInt
    heartbeat: HeartbeatBudget

    @model_validator(mode="after")
    def timing(self) -> Self:
        if self.observation_interval_ms >= self.telemetry_max_age_ms:
            raise ValueError("telemetry maximum age must exceed observation interval")
        if self.cooldown_ms < self.observation_interval_ms or self.policy_lease_ms <= self.cooldown_ms:
            raise ValueError("require observation interval <= cooldown < policy lease")
        if self.observation_interval_ms > self.intent_expiry_tolerance_ms:
            raise ValueError("observation interval exceeds intent expiry tolerance")
        return self


class FallbackAssumptions(ValueModel):
    minimum_supported_download_rate_bps: PositiveInt
    minimum_supported_upload_rate_bps: PositiveInt
    source: Literal["declared_lab_envelope"]


class SafePolicyConfig(ValueModel):
    schema_version: VersionOne
    mode: Literal["safe"]
    upload_shaper_rate_bps: PositiveInt
    download_shaper_rate_bps: PositiveInt
    default_service_class: Literal["best_effort"]
    host_isolation: Literal["triple-isolate"]
    temporary_grants_allowed: Literal[False]
    assumptions: FallbackAssumptions

    @field_validator("temporary_grants_allowed", mode="before")
    @classmethod
    def boolean_grant_flag(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("temporary_grants_allowed must be Boolean")
        return value

    @model_validator(mode="after")
    def conservative_within_envelope(self) -> Self:
        if self.download_shaper_rate_bps >= self.assumptions.minimum_supported_download_rate_bps:
            raise ValueError("safe download rate must be below its declared capacity floor")
        if self.upload_shaper_rate_bps >= self.assumptions.minimum_supported_upload_rate_bps:
            raise ValueError("safe upload rate must be below its declared capacity floor")
        return self


class ConfigSource(ValueModel):
    filename: Literal["controller.yaml", "safe-policy.yaml"]
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class RuntimeConfig(ValueModel):
    controller: ControllerConfig
    safe_policy: SafePolicyConfig
    source_files: tuple[ConfigSource, ...]
    effective_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ConfigBundle(ValueModel):
    """Offline setup validator only. Do not pass this object to the controller."""

    lab: LabConfig
    runtime: RuntimeConfig


def _load(path: Path, model: type[ValueModel]) -> tuple[ValueModel, str]:
    try:
        if path.stat().st_size > 256 * 1024:
            raise ConfigurationError("configuration exceeds 256 KiB")
        raw = path.read_bytes()
        text = raw.decode("utf-8")
        if any(isinstance(event, yaml.AliasEvent) for event in yaml.parse(text, Loader=UniqueSafeLoader)):
            raise ConfigurationError("YAML aliases are not supported")
        documents = list(yaml.load_all(text, Loader=UniqueSafeLoader))
        if len(documents) != 1 or type(documents[0]) is not dict:
            raise ConfigurationError("configuration must contain one mapping document")
        parsed = documents[0]
        if type(parsed.get("schema_version")) is not int:
            raise ConfigurationError("schema_version must be an integer")
        for key in ("synthetic_traffic", "temporary_grants_allowed"):
            if key in parsed and type(parsed[key]) is not bool:
                raise ConfigurationError(f"{key} must be a Boolean")
        value = model.model_validate_json(json.dumps(parsed, allow_nan=False), strict=True)
        return value, hashlib.sha256(raw).hexdigest()
    except (OSError, UnicodeError, ValueError, TypeError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"{path.name}: {exc}") from exc


def load_runtime_config(config_dir: str | Path) -> RuntimeConfig:
    """Read ONLY controller.yaml and safe-policy.yaml; no lab/emulator data."""
    directory = Path(config_dir)
    controller, controller_hash = _load(directory / "controller.yaml", ControllerConfig)
    safe, safe_hash = _load(directory / "safe-policy.yaml", SafePolicyConfig)
    for direction in ("upload", "download"):
        rate = getattr(safe, f"{direction}_shaper_rate_bps")
        bounds = getattr(controller, direction)
        if not bounds.minimum_bps <= rate <= bounds.maximum_bps:
            raise ConfigurationError(f"safe {direction} rate is outside controller bounds")
    effective = {"controller": controller.model_dump(mode="json"), "safe_policy": safe.model_dump(mode="json")}
    digest = hashlib.sha256(json.dumps(effective, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return RuntimeConfig(
        controller=controller,
        safe_policy=safe,
        source_files=(
            ConfigSource(filename="controller.yaml", sha256=controller_hash),
            ConfigSource(filename="safe-policy.yaml", sha256=safe_hash),
        ),
        effective_sha256=digest,
    )


def load_config_bundle(config_dir: str | Path) -> ConfigBundle:
    """Offline cross-check, including runner-only topology and capacity envelope."""
    runtime = load_runtime_config(config_dir)
    lab, _ = _load(Path(config_dir) / "lab.yaml", LabConfig)
    if runtime.controller.targets != lab.gateway:
        raise ConfigurationError("controller targets differ from declared gateway bindings")
    for direction in ("upload", "download"):
        name = f"minimum_supported_{direction}_rate_bps"
        if getattr(runtime.safe_policy.assumptions, name) != getattr(lab.emulator, name):
            raise ConfigurationError("fallback assumptions differ from the lab envelope")
    return ConfigBundle(lab=lab, runtime=runtime)
