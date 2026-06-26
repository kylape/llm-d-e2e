"""Configuration types and YAML loaders for test cases and profiles."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import yaml


def parse_duration(s: str) -> timedelta:
    """Parse duration strings like '15m', '2h', '300s', '1h30m'."""
    if not s:
        return timedelta()
    total = timedelta()
    for match in re.finditer(r"(\d+)\s*([hms])", s):
        val, unit = int(match.group(1)), match.group(2)
        if unit == "h":
            total += timedelta(hours=val)
        elif unit == "m":
            total += timedelta(minutes=val)
        elif unit == "s":
            total += timedelta(seconds=val)
    return total


@dataclass
class CacheConfig:
    enabled: bool = True
    pvc_name: str = ""
    storage_size: str = "10Gi"
    storage_class: str = ""
    timeout: timedelta = field(default_factory=lambda: timedelta(minutes=90))
    keep_pvc: bool = True


@dataclass
class ResourceConfig:
    cpu: str = "4"
    memory: str = "32Gi"
    gpus: int = 1
    ephemeral_storage: str = ""
    rdma: bool = False


@dataclass
class ParallelismConfig:
    data: int = 0
    data_local: int = 0
    expert: bool = False
    tensor: int = 0


@dataclass
class PrefillConfig:
    replicas: int = 1
    parallelism: ParallelismConfig | None = None
    resources: ResourceConfig = field(default_factory=ResourceConfig)


@dataclass
class MetricsCheck:
    enabled: bool = False
    check_vllm: bool = False
    check_epp: bool = False
    check_prefix_cache: bool = False
    check_pd: bool = False
    check_scheduler: bool = False
    check_nixl: bool = False


@dataclass
class MultiPoolCheck:
    enabled: bool = False
    pools: list[dict] = field(default_factory=list)


@dataclass
class BenchmarkConfig:
    """Configuration for GuideLLM benchmark runs."""

    duration: int = 300  # seconds
    concurrency: int = 64
    input_tokens_min: int = 128
    input_tokens_max: int = 1024
    output_tokens_min: int = 64
    output_tokens_max: int = 512
    profile: str = "constant"  # constant, sweep, poisson
    data_source: str | None = None  # HF dataset or JSON path (optional)


@dataclass
class SLOConfig:
    """SLO thresholds for benchmark validation."""

    ttft_p95_ms: float | None = None
    ttft_p99_ms: float | None = None
    throughput_tok_s: float | None = None
    itl_p95_ms: float | None = None
    itl_p99_ms: float | None = None
    error_rate: float | None = None  # 0.0 - 1.0


@dataclass
class AccuracyConfig:
    """Configuration for lm-eval accuracy checks."""

    task: str = "hellaswag"
    limit: int = 20
    min_score: float = 0.7


@dataclass
class ChatPrompt:
    role: str = "user"
    content: str = ""


@dataclass
class ModelConfig:
    name: str = ""
    uri: str = ""
    display_name: str = ""
    category: str = ""
    cache: CacheConfig = field(default_factory=CacheConfig)


@dataclass
class DeployConfig:
    manifest_path: str = ""
    namespace: str = ""
    replicas: int = 1
    service_account: str = ""
    ready_timeout: timedelta = field(default_factory=lambda: timedelta(minutes=15))
    resources: ResourceConfig = field(default_factory=ResourceConfig)
    parallelism: ParallelismConfig | None = None
    prefill: PrefillConfig | None = None
    worker: bool = False
    network_attach: str = ""
    env_overrides: dict[str, str] = field(default_factory=dict)


@dataclass
class ValidateConfig:
    health_endpoint: str = "/health"
    health_port: int = 8000
    health_scheme: str = "HTTPS"
    inference_check: bool = True
    test_prompts: list[str] = field(default_factory=list)
    chat_prompts: list[ChatPrompt] = field(default_factory=list)
    expected_codes: list[int] = field(default_factory=lambda: [200])
    timeout: timedelta = field(default_factory=lambda: timedelta(minutes=2))
    retry_attempts: int = 3
    retry_interval: timedelta = field(default_factory=lambda: timedelta(seconds=15))
    metrics_check: MetricsCheck = field(default_factory=MetricsCheck)
    multi_pool: MultiPoolCheck | None = None


@dataclass
class TestCase:
    name: str = ""
    description: str = ""
    labels: list[str] = field(default_factory=list)
    model: ModelConfig = field(default_factory=ModelConfig)
    deployment: DeployConfig = field(default_factory=DeployConfig)
    validation: ValidateConfig = field(default_factory=ValidateConfig)
    cleanup: bool = True
    # SLO benchmark fields
    hardware: str | None = None  # h100, h200, a100 — for documentation/filtering
    benchmark: BenchmarkConfig | None = None
    slos: SLOConfig | None = None
    accuracy: AccuracyConfig | None = None


@dataclass
class TestProfile:
    name: str = ""
    description: str = ""
    platform: str = "any"
    labels: list[str] = field(default_factory=list)
    test_cases: list[str] = field(default_factory=list)
    parallel: bool = False
    timeout: timedelta = field(default_factory=lambda: timedelta(hours=2))


def _build(cls, data: dict | None):
    """Recursively build a dataclass from a dict. Keys must be snake_case."""
    if not data:
        return cls()
    kwargs = {}
    hints = {f.name: f.type for f in cls.__dataclass_fields__.values()}
    for key, val in data.items():
        if key not in hints:
            continue
        hint = hints[key]
        if key in ("timeout", "ready_timeout", "retry_interval") and isinstance(val, str):
            kwargs[key] = parse_duration(val)
        elif hint == "CacheConfig" or (isinstance(hint, str) and "CacheConfig" in str(hint)):
            kwargs[key] = _build(CacheConfig, val) if val else CacheConfig()
        elif "ResourceConfig" in str(hint):
            kwargs[key] = _build(ResourceConfig, val) if val else ResourceConfig()
        elif "ParallelismConfig" in str(hint):
            kwargs[key] = _build(ParallelismConfig, val) if val else None
        elif "PrefillConfig" in str(hint):
            kwargs[key] = _build(PrefillConfig, val) if val else None
        elif "MetricsCheck" in str(hint):
            kwargs[key] = _build(MetricsCheck, val) if val else MetricsCheck()
        elif "MultiPoolCheck" in str(hint):
            kwargs[key] = _build(MultiPoolCheck, val) if val else None
        elif "ModelConfig" in str(hint):
            kwargs[key] = _build(ModelConfig, val) if val else ModelConfig()
        elif "DeployConfig" in str(hint):
            kwargs[key] = _build(DeployConfig, val) if val else DeployConfig()
        elif "ValidateConfig" in str(hint):
            kwargs[key] = _build(ValidateConfig, val) if val else ValidateConfig()
        elif "BenchmarkConfig" in str(hint):
            kwargs[key] = _build(BenchmarkConfig, val) if val else None
        elif "SLOConfig" in str(hint):
            kwargs[key] = _build(SLOConfig, val) if val else None
        elif "AccuracyConfig" in str(hint):
            kwargs[key] = _build(AccuracyConfig, val) if val else None
        else:
            kwargs[key] = val
    return cls(**kwargs)


def load_testcase(path: str | Path) -> TestCase:
    with open(path) as f:
        data = yaml.safe_load(f)
    return _build(TestCase, data)


def load_profile(path: str | Path) -> TestProfile:
    with open(path) as f:
        data = yaml.safe_load(f)
    profile = TestProfile(
        name=data.get("name", ""),
        description=data.get("description", ""),
        platform=data.get("platform", "any"),
        labels=data.get("labels", []),
        test_cases=data.get("test_cases", []),
        parallel=data.get("parallel", False),
    )
    if "timeout" in data:
        profile.timeout = parse_duration(data["timeout"])
    return profile


def load_testcases_from_dir(directory: str | Path) -> list[TestCase]:
    directory = Path(directory)
    cases = []
    for f in sorted(directory.glob("*.yaml")):
        cases.append(load_testcase(f))
    return cases


def resolve_profile(profile: TestProfile, testcase_dir: str | Path) -> list[TestCase]:
    all_cases = load_testcases_from_dir(testcase_dir)
    by_name = {tc.name: tc for tc in all_cases}
    return [by_name[name] for name in profile.test_cases if name in by_name]


def filter_by_names(cases: list[TestCase], names: list[str]) -> list[TestCase]:
    name_set = set(names)
    return [tc for tc in cases if tc.name in name_set]
