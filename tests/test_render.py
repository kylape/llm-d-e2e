from pathlib import Path

import pytest
import yaml

from conformance.deployer import Deployer
from conformance.render import (
    _set_vllm_image,
    model_name_for_variant,
    model_name_from_uri,
    render_manifest,
)


MANIFEST = Path(__file__).parents[1] / "../llm-d-conformance-manifests/single-gpu-smoke.yaml"


def test_model_name_from_uri():
    assert model_name_from_uri("hf://Qwen/Qwen3-0.6B") == "Qwen/Qwen3-0.6B"
    assert model_name_from_uri("Qwen/Qwen3-0.6B") == "Qwen/Qwen3-0.6B"


def test_model_name_for_variant():
    assert model_name_for_variant(
        "hf://Qwen/Qwen3-0.6B", "quay.io/aipcc/rhaiis/cuda-ubi9:3.6.0-fast.1"
    ) == "Qwen/Qwen3-0.6B--cuda-ubi9-3-6-0-fast-1"


def test_render_manifest_updates_only_run_fields(tmp_path):
    destination = tmp_path / "llmisvc.yaml"
    render_manifest(
        MANIFEST,
        destination,
        name="run-123",
        namespace="llm-d-e2e",
        model_uri="hf://Qwen/Qwen3-0.6B",
        vllm_image="quay.io/example/vllm@sha256:abc",
    )

    manifest = yaml.safe_load(destination.read_text())
    assert manifest["metadata"] == {"name": "run-123", "namespace": "llm-d-e2e"}
    assert manifest["spec"]["model"] == {
        "uri": "hf://Qwen/Qwen3-0.6B",
        "name": "Qwen/Qwen3-0.6B",
    }
    main = next(c for c in manifest["spec"]["template"]["containers"] if c["name"] == "main")
    assert main["image"] == "quay.io/example/vllm@sha256:abc"
    assert manifest["spec"]["template"]["imagePullSecrets"] == [{"name": "rhai-pull-secret"}]


def test_render_manifest_requires_all_parameters(tmp_path):
    with pytest.raises(ValueError, match="required"):
        render_manifest(
            MANIFEST,
            tmp_path / "llmisvc.yaml",
            name="run-123",
            namespace="llm-d-e2e",
            model_uri="",
            vllm_image="quay.io/example/vllm:latest",
        )


def test_real_vllm_image_override_preserves_entrypoint():
    spec = {
        "template": {"containers": [{"name": "main", "command": ["vllm"], "args": ["serve"]}]},
    }
    Deployer._set_vllm_image(spec, "quay.io/example/vllm@sha256:abc")
    container = spec["template"]["containers"][0]
    assert container["image"] == "quay.io/example/vllm@sha256:abc"
    assert container["command"] == ["vllm"]
    assert container["args"] == ["serve"]


def test_real_vllm_image_override_covers_prefill_and_worker_not_scheduler():
    spec = {
        "template": {"containers": [{"name": "main"}]},
        "prefill": {"template": {"containers": [{"name": "main"}]}},
        "worker": {"containers": [{"name": "main"}]},
        "router": {
            "scheduler": {
                "template": {"containers": [{"name": "main"}, {"name": "tokenizer"}]}
            }
        },
    }

    _set_vllm_image(spec, "quay.io/example/vllm@sha256:abc")

    assert spec["template"]["containers"][0]["image"].endswith("abc")
    assert spec["prefill"]["template"]["containers"][0]["image"].endswith("abc")
    assert spec["worker"]["containers"][0]["image"].endswith("abc")
    assert "image" not in spec["router"]["scheduler"]["template"]["containers"][0]
    assert "image" not in spec["router"]["scheduler"]["template"]["containers"][1]
