"""Local regression checks for migrated assertions and service lifecycle ordering."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest
import yaml

from conformance import odh
from conformance.config import load_profile, resolve_manifest, resolve_profile
from conformance.deployer import Deployer
from conformance.metrics import ScrapeResult, parse_prometheus
from conformance.openai_compat import OpenAICompatibilityValidator


def pod(name, component="workload", role="both", pool=True, node="node-a", sidecar=False):
    labels = {odh.COMPONENT: f"llminferenceservice-{component}"}
    if role:
        labels[odh.ROLE] = role
    if pool:
        labels[odh.POOL] = "workload"
    return {
        "metadata": {"name": name, "labels": labels},
        "status": {"phase": "Running"},
        "spec": {
            "nodeName": node,
            "containers": [{"name": "main"}],
            "initContainers": [{"name": "llm-d-routing-sidecar"}] if sidecar else [],
        },
    }


def pd_pods():
    scheduler = pod("scheduler", "router-scheduler", role=None, pool=False)
    scheduler["spec"]["containers"][0]["args"] = [
        "--config-text",
        yaml.safe_dump({"plugins": [{"type": plugin} for plugin in odh.PD_PLUGINS]}),
    ]
    return [pod("decode", role="decode", sidecar=True), pod("prefill", role="prefill"), scheduler]


@pytest.mark.parametrize("corruption", [None, "sidecar", "role", "plugin", "pool"])
def test_pd_topology_detects_controller_regressions(corruption):
    pods = pd_pods()
    if corruption == "sidecar":
        pods[0]["spec"]["initContainers"] = []
    elif corruption == "role":
        pods[1]["metadata"]["labels"][odh.ROLE] = "both"
    elif corruption == "plugin":
        pods[2]["spec"]["containers"][0]["args"] = ["--config-text", "plugins: []"]
    elif corruption == "pool":
        del pods[1]["metadata"]["labels"][odh.POOL]
    if corruption:
        with pytest.raises(AssertionError):
            odh.validate_topology(pods, {"pods": 2, "pool": 2, "pd": True})
    else:
        odh.validate_topology(pods, {"pods": 2, "pool": 2, "pd": True})


def test_moe_worker_routing_and_spread():
    pods = [
        pod("leader", "workload-leader"),
        pod("worker", "workload-worker", role=None, pool=False, node="node-b"),
        pod("scheduler", "router-scheduler", role=None, pool=False),
    ]
    checks = {"pods": 2, "pool": 1, "workers": True, "minNodes": 2}
    odh.validate_topology(pods, checks)
    pods[1]["metadata"]["labels"][odh.ROLE] = "both"
    with pytest.raises(AssertionError, match="routing role"):
        odh.validate_topology(pods, checks)
    del pods[1]["metadata"]["labels"][odh.ROLE]
    pods[1]["spec"]["nodeName"] = "node-a"
    with pytest.raises(AssertionError, match="nodes"):
        odh.validate_topology(pods, checks)


def test_lws_counts_and_readiness_are_service_scoped():
    sets = [
        {"metadata": {"name": name}, "spec": {"replicas": 1}, "status": {"readyReplicas": 1}}
        for name in ["case-kserve-mn", "case-kserve-mn-prefill", "unrelated-kserve-mn"]
    ]
    odh.validate_lws(sets, "case")
    sets[0]["status"]["readyReplicas"] = 0
    with pytest.raises(AssertionError, match="not ready"):
        odh.validate_lws(sets, "case")


def test_disk_volume_must_be_mounted_by_main_container():
    item = pod("disk")
    item["spec"]["volumes"] = [{"name": "kv-cache-secondary-0", "emptyDir": {}}]
    main = item["spec"]["containers"][0]
    main["volumeMounts"] = [{"name": "kv-cache-secondary-0", "mountPath": "/mnt/kv-cache-0"}]
    main["resources"] = {"requests": {"ephemeral-storage": "20Gi"}}
    odh.validate_disk_volume([item])
    main["volumeMounts"][0]["name"] = "unrelated"
    with pytest.raises(AssertionError, match="volume mount"):
        odh.validate_disk_volume([item])


def sample(name, text):
    return ScrapeResult(name, parse_prometheus(text))


@pytest.mark.parametrize("prefix", ["vllm:", "kserve_vllm:"])
def test_exact_prefix_deltas_reject_split_traffic_and_wrong_hits(prefix):
    before = {
        "a": sample("a", f"{prefix}request_success_total 7\n{prefix}prefix_cache_hits_total 128"),
        "b": sample("b", ""),
    }
    after = {
        "a": sample("a", f"{prefix}request_success_total 19\n{prefix}prefix_cache_hits_total 832"),
        "b": sample("b", ""),
    }
    odh.validate_prefix_samples(before, after, 12, 64)
    after["b"] = sample("b", f"{prefix}request_success_total 1")
    with pytest.raises(AssertionError, match="exactly one"):
        odh.validate_prefix_samples(before, after, 12, 64)
    after["b"] = sample("b", "")
    after["a"] = sample("a", f"{prefix}request_success_total 19\n{prefix}prefix_cache_hits_total 833")
    with pytest.raises(AssertionError, match="cache hits"):
        odh.validate_prefix_samples(before, after, 12, 64)


def test_exact_kv_accounting_rejects_local_decode_compute_and_missing_transfers():
    before = {name: sample(name, "") for name in ("decode", "prefill")}
    after = {
        "decode": sample("decode", 'vllm:prompt_tokens_by_source_total{source="external_kv_transfer"} 400'),
        "prefill": sample("prefill", 'vllm:prompt_tokens_by_source_total{source="local_compute"} 400'),
    }
    odh.validate_kv_samples(before, after, pd_pods(), 400)
    invalid = copy.deepcopy(after)
    invalid["decode"] = sample(
        "decode",
        'vllm:prompt_tokens_by_source_total{source="local_compute"} 1\n'
        'vllm:prompt_tokens_by_source_total{source="external_kv_transfer"} 400',
    )
    with pytest.raises(AssertionError, match="decode.local_compute"):
        odh.validate_kv_samples(before, invalid, pd_pods(), 400)
    with pytest.raises(AssertionError, match="external_kv_transfer"):
        odh.validate_kv_samples(before, before, pd_pods(), 400)


def test_bundled_profiles_and_manifest_identity():
    cases = resolve_profile(load_profile("configs/profiles/odh.yaml"), "configs/testcases")
    assert len(cases) == 16
    for case in cases:
        path = resolve_manifest(case, "/nonexistent-external-checkout")
        manifest = yaml.safe_load(path.read_text())
        assert manifest["metadata"]["name"] == case.name
        assert manifest["spec"]["model"]["uri"] == case.model.uri
        assert manifest["spec"]["model"]["name"] == case.model.name
        assert not case.model.cache.enabled
        if case.validation.odh.get("compatibility"):
            env = manifest["spec"]["template"]["containers"][0]["env"]
            assert any("--enable-auto-tool-choice" in entry.get("value", "") for entry in env)


def test_base_ref_selection_filters_annotations_and_rejects_ambiguity(monkeypatch):
    case = resolve_profile(load_profile("configs/profiles/odh-fast.yaml"), "configs/testcases")[0]
    deployer = Deployer()
    template = {
        "metadata": {
            "name": "v1-fast-1",
            "annotations": {
                "opendatahub.io/recommended-accelerators": '["nvidia.com/gpu"]',
                "opendatahub.io/supported-topologies": '["workload-single-node"]',
            },
        }
    }
    items = [template]
    monkeypatch.setattr(deployer, "kubectl", lambda *args: json.dumps({"items": items}))
    assert deployer.find_base_ref(case) == "v1-fast-1"
    items.append(copy.deepcopy(template))
    items[1]["metadata"]["name"] = "v2-fast-1"
    with pytest.raises(ValueError, match="Ambiguous"):
        deployer.find_base_ref(case)
    items.clear()
    assert deployer.find_base_ref(case) is None


def test_collection_keeps_all_compat_checks_before_cleanup(tmp_path):
    """Collect through the real pytest hooks: no phase may move across a service lifecycle."""
    plugin = tmp_path / "record_collection.py"
    output = tmp_path / "nodes.json"
    plugin.write_text(
        "import json\nfrom pathlib import Path\n"
        "def pytest_collection_finish(session):\n"
        f'    Path({str(output)!r}).write_text(json.dumps([(i.callspec.params["tc"].name, i.originalname) '
        "for i in session.items]))\n"
    )
    import os

    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_conformance.py",
            "--collect-only",
            "--testcase",
            "odh-fast-1,odh-pd",
            "-p",
            "record_collection",
            "-q",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    nodes = json.loads(output.read_text())
    cases = list(dict.fromkeys(case for case, _ in nodes))
    assert len(cases) == 2
    assert [case for case, _ in nodes] == [case for case in cases for _ in range(45)]
    for case in cases:
        methods = [method for name, method in nodes if name == case]
        assert methods[0] == "test_01_prereq"
        assert methods[-1] == "test_99_cleanup"
        assert methods.count("test_09e_odh_openai_compat") == 19
        assert methods.index("test_06c_odh_kv_transfer") < methods.index("test_09c_odh_inference")


@pytest.fixture
def compat_server():
    """Exercise the actual SDK and SSE decoding against a local HTTP endpoint."""
    state = SimpleNamespace(requests=[], bad_usage=False, bad_sse=False, error_status=404)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, body, status=200, content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            assert self.path == "/ns/service/v1/models"
            self.reply(
                json.dumps(
                    {"object": "list", "data": [{"id": "model", "object": "model", "created": 1, "owned_by": "test"}]}
                )
            )

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append((self.path, self.headers.get("Authorization"), body))
            if body["model"].startswith("nonexistent"):
                self.reply(
                    json.dumps({"error": {"message": "No such model", "type": "NotFoundError"}}), state.error_status
                )
                return
            base = {"id": "chat-1", "model": "model", "created": 1, "object": "chat.completion"}
            if body.get("stream"):
                base["object"] = "chat.completion.chunk"
                chunks = []
                for index, delta in enumerate([{"role": "assistant", "content": "hello"}, {}]):
                    chunk = dict(
                        base, choices=[{"index": 0, "delta": delta, "finish_reason": "stop" if index else None}]
                    )
                    if state.bad_sse and index:
                        chunk["id"] = "wrong-id"
                    chunks.append("data: " + json.dumps(chunk) + "\n\n")
                self.reply("".join(chunks) + "data: [DONE]\n\n", content_type="text/event-stream")
                return
            text = "Alice" if len(body["messages"]) > 1 else "hello"
            if body.get("response_format"):
                text = '{"color":"blue"}'
            finish = "length" if body["max_tokens"] == 5 else "stop"
            choices = [
                {"index": index, "message": {"role": "assistant", "content": text}, "finish_reason": finish}
                for index in range(body.get("n", 1))
            ]
            if body.get("logprobs"):
                choices[0]["logprobs"] = {"content": [{"token": "hello", "logprob": -0.1, "top_logprobs": []}]}
            if body.get("tools") and body["messages"][-1]["role"] != "tool":
                choices[0]["message"]["tool_calls"] = [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"location":"Tokyo"}'},
                    }
                ]
            self.reply(
                json.dumps(
                    dict(
                        base,
                        choices=choices,
                        usage={
                            "prompt_tokens": 3,
                            "completion_tokens": 1,
                            "total_tokens": 99 if state.bad_usage else 4,
                        },
                    )
                )
            )

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/ns/service", state
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("verification", OpenAICompatibilityValidator.ALL_VERIFICATIONS)
def test_source_compatibility_verifications_use_sdk(compat_server, verification):
    endpoint, state = compat_server
    with OpenAICompatibilityValidator(endpoint, "model", "test-token", timeout=3) as validator:
        getattr(validator, verification)(duration=0)
    for path, auth, _ in state.requests:
        assert path == "/ns/service/v1/chat/completions"
        assert auth == "Bearer test-token"


@pytest.mark.parametrize(
    "broken,verification",
    [
        ("bad_usage", "verify_chat_completion_usage"),
        ("bad_sse", "verify_streaming_sse_integrity"),
        ("error_status", "verify_error_forwarding"),
    ],
)
def test_compatibility_failures_survive_soak(compat_server, broken, verification):
    endpoint, state = compat_server
    setattr(state, broken, 503 if broken == "error_status" else True)
    with OpenAICompatibilityValidator(endpoint, "model", timeout=3) as validator:
        with pytest.raises(AssertionError, match="iterations failed"):
            getattr(validator, verification)(duration=0)
