"""ODH topology and exact traffic assertions for the existing conformance phases.

Adapted from opendatahub-tests llmd tests/utils.py at 92a93ed7. Cluster reads
use Deployer; metric samples use Scraper instead of OpenShift Prometheus.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Callable

import httpx
import yaml

from conformance.client import LLMClient
from conformance.deployer import Deployer
from conformance.metrics import ScrapeResult, Scraper

COMPONENT = "app.kubernetes.io/component"
ROLE = "llm-d.ai/role"
POOL = "kserve.io/component"
PD_PLUGINS = ("prefill-filter", "decode-filter", "always-disagg-pd-decider", "disagg-profile-handler")
PREFIX_PROMPT = (
    "Explain in detail the fundamental principles of quantum mechanics including "
    "wave-particle duality, superposition, and entanglement in simple terms. "
    "Additionally, describe how these quantum phenomena differ from classical physics "
    "and why they are important for understanding the nature of reality at the atomic scale."
)


def service_pods(deployer: Deployer, name: str) -> list[dict]:
    """Read all service pods, including LWS workers and the scheduler."""
    return json.loads(
        deployer.kubectl(
            "get",
            "pods",
            "-n",
            deployer.namespace,
            "-l",
            f"app.kubernetes.io/part-of=llminferenceservice,app.kubernetes.io/name={name}",
            "-o",
            "json",
        )
    )["items"]


def labels(pod: dict) -> dict:
    return pod["metadata"].get("labels", {})


def workload_pods(pods: list[dict]) -> list[dict]:
    return [pod for pod in pods if labels(pod).get(COMPONENT, "").startswith("llminferenceservice-workload")]


def pool_pods(pods: list[dict]) -> list[dict]:
    # This is the source test's selector, not a claim about the live InferencePool spec.
    return [pod for pod in pods if labels(pod).get(POOL) == "workload"]


def scheduler_pod(pods: list[dict]) -> dict:
    schedulers = [pod for pod in pods if labels(pod).get(COMPONENT) == "llminferenceservice-router-scheduler"]
    assert len(schedulers) == 1, f"Expected one scheduler, found {len(schedulers)}"
    assert schedulers[0].get("status", {}).get("phase") == "Running", "Scheduler is not Running"
    return schedulers[0]


def validate_topology(pods: list[dict], config: dict) -> None:
    """Preserve source pod counts, role labels, worker exclusion and sidecar checks."""
    workloads = workload_pods(pods)
    pool = pool_pods(pods)
    assert len(workloads) == config["pods"], f"Expected {config['pods']} vLLM pods, got {len(workloads)}"
    assert len(pool) == config["pool"], f"Expected {config['pool']} pool pods, got {len(pool)}"
    scheduler_pod(pods)
    if config.get("workers"):
        workers = [pod for pod in workloads if pod not in pool]
        assert workers, "Expected headless workers outside the pool"
        for pod in workers:
            assert POOL not in labels(pod), f"Worker {pod['metadata']['name']} has pool component label"
            assert ROLE not in labels(pod), f"Worker {pod['metadata']['name']} has a routing role"
        if not config.get("pd"):
            assert all(labels(pod).get(ROLE) == "both" for pod in pool), "Leader role must be 'both'"
    else:
        assert {pod["metadata"]["name"] for pod in workloads} == {pod["metadata"]["name"] for pod in pool}
    if config.get("minNodes"):
        nodes = {pod["spec"].get("nodeName") for pod in workloads}
        assert None not in nodes and "" not in nodes, "Workload pods must be scheduled"
        assert len(nodes) >= config["minNodes"], f"Expected >= {config['minNodes']} nodes, got {nodes}"
    if config.get("pd"):
        roles = Counter(labels(pod).get(ROLE) for pod in pool)
        assert set(roles) == {"decode", "prefill"}, f"Unexpected P/D roles: {roles}"
        if not config.get("workers"):
            assert roles == {"decode": 1, "prefill": 1}, f"Unexpected single-node P/D counts: {roles}"
        for pod in pool:
            sidecars = [container["name"] for container in pod["spec"].get("initContainers", [])]
            assert ("llm-d-routing-sidecar" in sidecars) == (labels(pod)[ROLE] == "decode"), (
                f"Unexpected sidecar placement on {pod['metadata']['name']}: {sidecars}"
            )
        validate_pd_plugins(scheduler_pod(pods))


def validate_pd_plugins(pod: dict) -> None:
    configs = []
    for container in pod["spec"]["containers"]:
        args = container.get("args", [])
        for index, arg in enumerate(args):
            if arg == "--config-text" and index + 1 < len(args):
                configs.append(yaml.safe_load(args[index + 1]))
            elif arg.startswith("--config-text="):
                configs.append(yaml.safe_load(arg.split("=", 1)[1]))
    assert configs, "Scheduler has no --config-text"
    plugins = {plugin.get("type") for config in configs for plugin in config.get("plugins", [])}
    assert set(PD_PLUGINS) <= plugins, f"Missing P/D plugins: {set(PD_PLUGINS) - plugins}"


def validate_lws(items: list[dict], name: str) -> None:
    expected = {f"{name}-kserve-mn", f"{name}-kserve-mn-prefill"}
    # Other test cases can share the namespace. Restrict the source namespace-wide check to this service.
    owned = [
        item
        for item in items
        if item["metadata"]["name"] in expected
        or any(
            ref.get("kind") == "LLMInferenceService" and ref.get("name") == name
            for ref in item["metadata"].get("ownerReferences", [])
        )
    ]
    assert len(owned) == 2 and {item["metadata"]["name"] for item in owned} == expected, (
        f"Expected decode and prefill LWS: {expected}"
    )
    for item in owned:
        assert item.get("status", {}).get("readyReplicas", 0) == item["spec"].get("replicas", 1), (
            f"LWS {item['metadata']['name']} is not ready"
        )


def validate_disk_volume(pods: list[dict]) -> None:
    pool = pool_pods(pods)
    assert pool, "No workload pods found"
    for pod in pool:
        volumes = {volume["name"]: volume for volume in pod["spec"].get("volumes", [])}
        volume = volumes.get("kv-cache-secondary-0", {})
        assert "emptyDir" in volume or "ephemeral" in volume, f"Missing local KV volume on {pod['metadata']['name']}"
        main = next((c for c in pod["spec"]["containers"] if c["name"] == "main"), None)
        assert main is not None, "Missing main container"
        assert any(
            m["mountPath"] == "/mnt/kv-cache-0" and m["name"] == "kv-cache-secondary-0"
            for m in main.get("volumeMounts", [])
        ), "Missing KV cache volume mount"
        assert "ephemeral-storage" in main.get("resources", {}).get("requests", {}), "No ephemeral-storage request"


def metric_value(sample: ScrapeResult, name: str, **filters: str) -> float:
    value = sample.get(f"vllm:{name}", fallback=f"kserve_vllm:{name}", **filters)
    # Unused counters may have no series yet (the source PromQL helper also returns zero).
    return value if value is not None else 0.0


def scrape_workloads(scraper: Scraper, pods: list[dict]) -> dict[str, ScrapeResult]:
    # Fail on a scrape failure rather than dropping an idle or missing pod.
    return {pod["metadata"]["name"]: scraper.scrape_pod(pod["metadata"]["name"]) for pod in workload_pods(pods)}


def validate_prefix_samples(
    before: dict[str, ScrapeResult], after: dict[str, ScrapeResult], count: int, block_size: int
):
    assert before.keys() == after.keys() and after, "Workload set changed during traffic"
    requests = {
        name: metric_value(sample, "request_success_total") - metric_value(before[name], "request_success_total")
        for name, sample in after.items()
    }
    assert all(value >= 0 for value in requests.values()), f"Request counters reset: {requests}"
    active = [name for name, value in requests.items() if value > 0]
    assert len(active) == 1, f"Expected traffic on exactly one pod, got {requests}"
    name = active[0]
    assert requests[name] == count, f"Expected {count} requests, got {requests}"
    hits = metric_value(after[name], "prefix_cache_hits_total") - metric_value(before[name], "prefix_cache_hits_total")
    assert hits == (count - 1) * block_size, f"Expected {(count - 1) * block_size} cache hits, got {hits}"


def validate_kv_samples(before: dict[str, ScrapeResult], after: dict[str, ScrapeResult], pods: list[dict], tokens: int):
    assert tokens > 0, "Responses did not report prompt tokens"
    assert before.keys() == after.keys() and after, "Workload set changed during traffic"
    expected = {
        ("decode", "external_kv_transfer"): tokens,
        ("decode", "local_compute"): 0,
        ("prefill", "external_kv_transfer"): 0,
        ("prefill", "local_compute"): tokens,
    }
    roles = {labels(pod).get(ROLE): pod["metadata"]["name"] for pod in workload_pods(pods)}
    assert set(roles) == {"decode", "prefill"}, f"Unexpected roles: {roles}"
    for (role, source), target in expected.items():
        name = roles[role]
        value = metric_value(after[name], "prompt_tokens_by_source_total", source=source) - metric_value(
            before[name], "prompt_tokens_by_source_total", source=source
        )
        assert value == target, f"{role}.{source}={value}, expected {target}"


def eventually(assertion: Callable[[], None], timeout: float = 120, interval: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while True:
        try:
            assertion()
            return
        except AssertionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(interval, max(0, deadline - time.monotonic())))


def prefix_traffic(client: LLMClient, model: str, delay: float, count: int = 12) -> None:
    successful = 0
    failures = 0
    for _ in range(count + 5):
        try:
            client.chat(model, PREFIX_PROMPT, max_tokens=50, temperature=0)
        except (httpx.HTTPError, ValueError):
            failures += 1
            assert failures < 5, f"Too many prefix request failures: {failures}"
            continue
        successful += 1
        if successful == 1 and delay:
            time.sleep(delay)
        if successful == count:
            return
    raise AssertionError(f"Only {successful}/{count} prefix requests succeeded")
