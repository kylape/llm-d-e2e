"""Render a parameterized LLMInferenceService from a conformance manifest."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml


def model_name_from_uri(uri: str) -> str:
    value = uri.strip()
    if "://" in value:
        value = value.split("://", 1)[1]
    return value.rstrip("/")


def _serving_pod_specs(spec: dict):
    """Yield explicit vLLM-serving PodSpecs, excluding router scheduler specs."""
    template = spec.get("template")
    if isinstance(template, dict):
        yield template

    prefill = spec.get("prefill")
    if isinstance(prefill, dict) and isinstance(prefill.get("template"), dict):
        yield prefill["template"]

    worker = spec.get("worker")
    if isinstance(worker, dict):
        yield worker


def _set_vllm_image(spec: dict, image: str) -> None:
    """Set the image on every explicit serving container named ``main``."""
    for serving_spec in _serving_pod_specs(spec):
        for container in serving_spec.get("containers", []):
            if container.get("name") == "main":
                container["image"] = image


def _apply_model_spec(spec: dict, model_spec: dict) -> None:
    """Apply resource and vLLM settings from a complete model specification."""
    try:
        gpu_count = int(model_spec.get("gpu_count", 1))
        tensor_parallel_size = int(model_spec.get("tensor_parallel_size", 1))
    except (TypeError, ValueError) as exc:
        raise ValueError("model_spec GPU and tensor parallel values must be integers") from exc

    if gpu_count < 1 or tensor_parallel_size < 1:
        raise ValueError("model_spec GPU and tensor parallel values must be positive")

    vllm_args = [str(value) for value in model_spec.get("vllm_args", [])]
    for serving_spec in _serving_pod_specs(spec):
        for container in serving_spec.get("containers", []):
            if container.get("name") != "main":
                continue
            resources = container.setdefault("resources", {})
            for resource_type in ("requests", "limits"):
                resources.setdefault(resource_type, {})["nvidia.com/gpu"] = str(gpu_count)
            args = [arg for arg in container.get("args", []) if "tensor-parallel-size" not in str(arg)]
            args.extend(["--tensor-parallel-size", str(tensor_parallel_size)])
            args.extend(vllm_args)
            container["args"] = args


def render_manifest(
    source: str | Path,
    destination: str | Path,
    *,
    name: str,
    namespace: str,
    model_uri: str = "",
    vllm_image: str = "",
    model_spec: str | None = None,
    pull_secret: str = "rhai-pull-secret",
) -> None:
    if not all(value.strip() for value in (name, namespace, model_uri, vllm_image)):
        raise ValueError("name, namespace, model_uri, and vllm_image are required")

    with open(source) as stream:
        manifest = yaml.safe_load(stream)

    if manifest.get("kind") != "LLMInferenceService":
        raise ValueError("base manifest must be an LLMInferenceService")

    metadata = manifest.setdefault("metadata", {})
    metadata["name"] = name
    metadata["namespace"] = namespace

    spec = manifest.setdefault("spec", {})
    if model_spec:
        try:
            parsed_model_spec = json.loads(model_spec)
        except json.JSONDecodeError as exc:
            raise ValueError("model_spec must be valid JSON") from exc
        if not isinstance(parsed_model_spec, dict) or not parsed_model_spec.get("uri"):
            raise ValueError("model_spec must contain a uri")
        model_uri = str(parsed_model_spec["uri"])
        _apply_model_spec(spec, parsed_model_spec)
    model = spec.setdefault("model", {})
    model["uri"] = model_uri
    model["name"] = model_name_from_uri(model_uri)

    serving_specs = list(_serving_pod_specs(spec))
    if not serving_specs:
        raise ValueError("base manifest must contain an explicit serving PodSpec")

    found_main = False
    for serving_spec in serving_specs:
        if pull_secret:
            serving_spec["imagePullSecrets"] = [{"name": pull_secret}]
        found_main = found_main or any(
            container.get("name") == "main" for container in serving_spec.get("containers", [])
        )

    if not found_main:
        raise ValueError("base manifest must contain a serving container named main")
    _set_vllm_image(spec, vllm_image)

    with open(destination, "w") as stream:
        yaml.safe_dump(manifest, stream, sort_keys=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--model-uri", required=True)
    parser.add_argument("--vllm-image", required=True)
    parser.add_argument("--model-spec", default="")
    parser.add_argument("--pull-secret", default="rhai-pull-secret")
    args = parser.parse_args()
    render_manifest(**vars(args))


if __name__ == "__main__":
    main()
