"""Render a parameterized LLMInferenceService from a conformance manifest."""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def model_name_from_uri(uri: str) -> str:
    value = uri.strip()
    if "://" in value:
        value = value.split("://", 1)[1]
    return value.rstrip("/")


def render_manifest(
    source: str | Path,
    destination: str | Path,
    *,
    name: str,
    namespace: str,
    model_uri: str,
    vllm_image: str,
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
    model = spec.setdefault("model", {})
    model["uri"] = model_uri
    model["name"] = model_name_from_uri(model_uri)

    template = spec.setdefault("template", {})
    if pull_secret:
        template["imagePullSecrets"] = [{"name": pull_secret}]
    containers = template.setdefault("containers", [])
    main = next((container for container in containers if container.get("name") == "main"), None)
    if main is None:
        raise ValueError("base manifest must contain spec.template.containers[name=main]")
    main["image"] = vllm_image

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
    parser.add_argument("--pull-secret", default="rhai-pull-secret")
    args = parser.parse_args()
    render_manifest(**vars(args))


if __name__ == "__main__":
    main()
