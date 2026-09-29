# ODH llm-d coverage and migration analysis

Date: 2026-09-29. Branch: `port/odh-llmd-tests`.

## Scope and accounting

Source: `/opt/workspace/src/rhai/opendatahub-tests`, clean `main` at
`92a93ed7188391ec70f4c522d72ea7bbf244b4e2`. The request's `opendathaub-tests`
path was resolved to this checkout. Target baseline: `llm-d-e2e` at
`60bf011743e48ad75e3ef23d8e9ef5a4a75e26c2`, before this branch's changes.
The target's existing local commits were preserved.

The inventory covers all 14 `llmd/test_*.py` files and both
`upgrade/test*llmd*.py` files: **76 test methods, 233 expanded variants**.
Expansion includes class configuration variants and all 19 registered API
verifications; 152 variants are API checks across eight deployments.
These are static AST counts from the pinned source, not a successful collection
or execution of the source's OpenShift fixture graph. Hardware, platform health,
optional image templates, and disconnected checks can change actual run outcomes.

[The machine-readable inventory](odh-migration-inventory.json) records each
source method, configuration ID, compatibility verification, disposition,
target testcase, target phase, and reason. Source IDs are descriptive identifiers
assembled from source parameters; pytest may order parameter IDs differently.

| Disposition | Source variants | Meaning |
|---|---:|---|
| Ported | 127 | Behavioral assertions and deployment scenario retained for the supported CPU/NVIDIA path |
| Partial | 61 | Assertions implemented, with source storage and/or authentication fixture differences |
| Not ported | 45 | No equivalent complete scenario in this runner; listed below |

There are **16 new deployment scenarios**, four profiles, and 720 collected
conformance phase items for the complete `odh` profile. Target item count includes
disabled phases and therefore is not a coverage count. Several source topology
tests share a target phase; every assertion is retained, but failures in that
phase can prevent later assertions within the same phase from executing.
No source tests have been deleted or changed. “Ported” describes implementation,
not cluster validation or authorization to retire the source tests.

A wider source search for `llmd`, `llmisvc`, and `LLMInferenceService` in test
modules also found the MaaS upgrade test. It creates a `MaaSModelRef` referencing
an LLMInferenceService kind; its assertions test MaaS control-plane behavior,
not deployment or inference of llm-d. It is outside this llm-d inventory.

## Context reviewed

Scratchpad inputs were `repo-analyses/opendatahub-tests/repo-analysis.md`,
`repo-analyses/llm-d-e2e/overview.md`, `test-suite-summary.md`, and
`proposals/llm-d-e2e-opendatahub-tests-consolidation/architecture-baseline.md`.
Their architecture descriptions were checked against the local source. Earlier
24-case inventories predate the API verification matrix, MoE, KV offload and
current upgrade coverage. The earlier consolidation baseline describes Tekton
and rendering modules that are absent from this target checkout. The source
code and pinned commits above determine this analysis.

## Source execution model

The source is a pytest monorepo. Global and model-serving fixtures create
privileged/unprivileged Kubernetes clients, namespaces, credentials and product
resources through `openshift-python-wrapper`. The llm-d session health gate
checks DSC `KserveLLMInferenceServiceDependencies`, controller deployments,
LeaderWorkerSet operator and Kuadrant; unhealthy dependencies cause xfail.
A shared Gateway and product routing/TLS fixtures precede inference.

Each `llmd_configs` class builds a service CR, including model URI, runtime
arguments, pod probes, resources, router, workers and prefill configuration.
CPU setup rejects ARM64 and discovers a CPU `LLMInferenceServiceConfig`.
GPU setup detects NVIDIA/AMD resources and required node/GPU distribution.
Standard NVIDIA uses controller defaults; optional fast variants select an
installed baseRef by accelerator, topology annotations and name regex, or skip.
The fast tests assert semver availability, not a particular vLLM version or image
digest. Source HF cases skip disconnected clusters.

Fixtures manage service ownership and cleanup, S3 Secrets/ServiceAccounts,
per-service RBAC and tokens, trusted CA resolution, and the product's local
model cache resources. Advanced routing tests query OpenShift Prometheus;
precise-cache tests also inspect scheduler JSON logs. API tests use the OpenAI
SDK and run every verification repeatedly for `SOAK_TEST_DURATION` (10 seconds
by default). The upgrade suites use an external upgrade boundary and persist
pre-upgrade snapshots in a ConfigMap.

## Coverage by source file

| Source file | Variants | Ported / partial / deferred | Coverage and migration |
|---|---:|---|---|
| `test_llmd_auth.py` | 2 | 0 / 0 / 2 | Two CPU services; owner tokens succeed with Rome; cross-user and absent tokens return 401/403, with trusted TLS. Deferred. |
| `test_llmd_connection_cpu.py` | 2 | 1 / 0 / 1 | TinyLlama CPU loading via S3/HF, HTTP success and Rome. HF ported; S3 credential lifecycle deferred. |
| `test_llmd_connection_gpu.py` | 2 | 1 / 0 / 1 | TinyLlama GPU loading via S3/HF, HTTP success and Rome. NVIDIA/HF ported; S3 and AMD execution remain gaps. |
| `test_llmd_fast_image.py` | 42 | 42 / 0 / 0 | fast-1/fast-2: Rome, gateway /version semver, 19 API soak verifications per image. Ported with optional template discovery. |
| `test_llmd_kueue_integration.py` | 1 | 0 / 0 / 1 | Quota permits one CPU replica; scale to two, verify one running and one gated, then Rome. Deferred. |
| `test_llmd_local_model_cache.py` | 2 | 0 / 0 / 2 | All cache nodes NodeDownloaded; complete successful copies; rewritten PVC usage; inference; status back-reference. Deferred. |
| `test_llmd_multinode_moe_dp_ep.py` | 7 | 7 / 0 / 0 | Dummy Qwen3 MoE: 2 vLLM pods, 1 pool member, scheduler Running, leader role=both, workers excluded/unlabelled, >=2 nodes, non-empty inference. Ported. |
| `test_llmd_multinode_moe_dp_ep_pd.py` | 9 | 9 / 0 / 0 | Dummy MoE P/D: 4 vLLM pods, 2 pool leaders, 2 ready/named LWS groups, scheduler Running, P/D roles/sidecars/plugins, workers excluded, >=2 nodes, inference. Ported. |
| `test_llmd_no_scheduler.py` | 1 | 0 / 1 / 0 | S3 GPU inference with only router.route configured, expecting Rome. Same assertion/topology ported using HF; S3 combination remains deferred. |
| `test_llmd_singlenode_estimated_prefix_cache.py` | 20 | 0 / 20 / 0 | 2 workloads/pool members, scheduler Running, 12 identical requests on exactly one pod, exact 704 cached tokens, 19 API checks. HF and auth-disabled adaptation. |
| `test_llmd_singlenode_kv_cache_offload.py` | 3 | 3 / 0 / 0 | CPU KV tier inference; disk tier local volume, main-container mount, ephemeral-storage request and inference. Ported; these source tests do not prove actual offloaded bytes. |
| `test_llmd_singlenode_precise_prefix_cache.py` | 40 | 0 / 40 / 0 | Legacy scorer and current producer: topology, 12 requests, exact routing/cache accounting, >=12 scheduler decisions, 19 API checks each. Auth-disabled adaptation. |
| `test_llmd_singlenode_prefill_decode.py` | 63 | 63 / 0 / 0 | Standard/fast-1/fast-2: exact roles/counts, sidecar placement, 4 scheduler plugins, 20-request KV accounting, 19 API checks each. Ported. |
| `test_llmd_smoke.py` | 1 | 1 / 0 / 0 | TinyLlama CPU OCI image, successful chat and Rome. Ported with the original modelcar digest. |
| `test_upgrade_llmd.py` | 18 | 0 / 0 / 18 | 2 pre-upgrade and 16 post-upgrade checks: no-auth inference, readiness, Gateway/controller health, identity/config preservation and child resources. Deferred. |
| `test_upgrade_llmd_auth_kueue.py` | 20 | 0 / 0 / 20 | 3 pre-upgrade and 17 post-upgrade checks: auth, queue gating/admission, preserved integration settings, identity/config and child resources. Deferred. |

## Assertion details and limitations

### API compatibility

The port preserves the 19 verification bodies and assertion helpers:

* `verify_models_endpoint`
* `verify_chat_completion`
* `verify_chat_completion_usage`
* `verify_streaming`
* `verify_streaming_sse_integrity`
* `verify_system_prompt`
* `verify_multi_turn`
* `verify_json_mode`
* `verify_stop_sequences`
* `verify_sampling_params`
* `verify_max_tokens`
* `verify_logprobs`
* `verify_n_completions`
* `verify_seed`
* `verify_error_forwarding`
* `verify_tool_calling`
* `verify_tool_calling_streaming`
* `verify_parallel_tool_calls`
* `verify_multi_turn_tool_use`

These check model list shape; completion shape/usage arithmetic; streaming text,
IDs, choice ordering and terminal reason; system and multi-turn messages; JSON
objects; stop sequences; sampling parameters; output token limits; logprobs;
multiple choices; seed acceptance; error forwarding; and tool-call shapes and
conversation continuation. They test the same gateway endpoint as the source,
including `/v1/models` and `/version`; a gateway that cannot route those paths
will fail these source checks even if the runner's existing direct-pod checks pass.

Tool production remains conditional, as in the source. A model returning text
without tool calls can pass the tool acceptance checks; the second agentic turn
runs only if the first turn produced a tool call. Seed acceptance does not prove
determinism. Error forwarding rejects gateway errors 502/503/504 but retains the
source's allowance for other API error statuses. Large-payload integrity is a
source TODO and is not counted as implemented coverage.

`OpenAICompatibilityValidator` now receives the runner endpoint, bearer token
and timeout. Kubernetes auth/diagnostic fixtures were removed from that class;
no token creation or refresh is claimed. Its SDK uses `DefaultHttpxClient`, which
matches the pinned source SDK's transport (3.3.0), instead of passing an
incompatible transport client. SDK retries are disabled so repeated transient
failures do not become a passing soak. Duration zero executes once. Assertions
retain the source's ordinary SDK parsing behavior, without enabling strict
schema validation. Local tests exercise the real SDK against HTTP/SSE fixtures.

### Exact cache and transfer accounting

The prefix tests retain the original shared prompt, block size 64, 12 successful
requests and expected `(12 - 1) * 64 = 704` hits on exactly one pod. Precise
variants wait 15 seconds after the first request for index propagation and check
scheduler decision logs. Up to five request failures abort the workload, as in
the source. The old scorer and new producer plugin configurations are both
bundled; their availability depends on the product version.

P/D retains the original 20 prompts: ten completion calls followed by ten chat
calls, temperature zero and 50 output tokens. Response prompt-token sums must
match decode external KV and prefill local compute exactly; decode local compute
and prefill external KV must remain zero. The zero decode expectation preserves
the source's vLLM 0.21+ metric behavior; it may require a version-specific rule
when upstream counts the recomputed token correctly.

These tests scrape each vLLM pod directly, accepting raw `vllm:` or recorded
`kserve_vllm:` names. Before/after deltas isolate their traffic from earlier
requests; positive activity must still be present and exact. A failed pod scrape
fails the check. No Prometheus server/operator integration is covered by this
adaptation. Polling retains bounded metric/log propagation waits. Exact prefix
accounting needs fresh pods/cold test prompts and no concurrent traffic;
`--mode discover` cannot guarantee that isolation.

### Topology, storage and images

The port preserves source label-based pool membership rather than silently
claiming to validate the actual InferencePool selector. The MoE P/D source
comment says only decode belongs to the pool, but its executable configuration
expects two leaders (decode and prefill); the port follows the executable
assertion. LWS checks are scoped to this service's names/owners because the
target shares a namespace between cases. Source volume/mount assertions remain
in the disk-offload case. Extra filesystem-byte and offload metrics in the
existing target suite remain distinct from these source smoke assertions.

Bundled manifests preserve source runtime arguments, resource requests, probes,
NIXL roles, P/D affinity, router plugins, modelcar digest and pinned dummy-MoE HF
revision. Standard NVIDIA defaults and CPU/fast template discovery are supported.
AMD auto-selection, product health gates, disconnected detection, per-user RBAC,
CA verification and generated S3/cache credentials are not ported fixtures.
CPU cases are real CPU deployments; every ODH case skips simulator execution,
since simulated completions cannot establish these source assertions.

## Target integration and changes made

The original CLI/pytest execution model is retained: one testcase selects one
service manifest and runs ordered phases in deploy or discover mode. New
`validation.odh` settings select assertion groups. Phases 06a–06c run topology,
prefix traffic and exact KV traffic before ordinary inference can warm caches.
Phases 09c–09e run source inference, version and individually parametrized API
checks. Existing profiles retain their selected testcase lists.

`bundled/` manifest paths resolve to tracked `configs/manifests/`; other paths
continue to use the external manifest checkout. Bundled cases do not need
`--setup` or changes to the external manifests repository. Container builds
already copy these files. CPU/fast template selectors are small deployment
adapters over existing kubectl calls; they do not add a new execution mode.
Ambiguous matches fail with guidance to narrow `baseRefRegex`. Missing optional
fast templates skip; missing required CPU templates fail.

Collection now explicitly orders every service's phases and all function
parameters before cleanup. Pytest's fixture grouping alone was insufficient
once 19 function parameters were combined with class-scoped cases. A real
collection regression test protects this ordering. Direct pod forwarding now
accepts LWS leaders, and GPU accounting includes data-parallel workers. Discover
cleanup was corrected to preserve pre-existing services.

The SDK is the only new direct Python dependency. Unit checks run in CI and
`make unittest`. Adapted source code is attributed in module headers, and its
Apache 2.0 license is retained in `third_party/opendatahub-tests/LICENSE`.

## Scenarios and execution

| Profile | Contents |
|---|---|
| `configs/profiles/odh.yaml` | All 16 scenarios |
| `configs/profiles/odh-core.yaml` | 11 scenarios excluding optional fast images and legacy precise scorer |
| `configs/profiles/odh-fast.yaml` | fast-1/fast-2 ordinary and P/D scenarios |
| `configs/profiles/odh-legacy-cache.yaml` | Legacy precise-prefix-cache scorer |

After cluster validation is authorized, examples from the repository root are:

```bash
uv run llm-d-e2e -p configs/profiles/odh-core.yaml
uv run llm-d-e2e -t odh-cpu-oci
uv run llm-d-e2e -t odh-pd --namespace llm-conformance-test
uv run llm-d-e2e -p configs/profiles/odh-fast.yaml
```

The runner still assumes its configured RHOAI gateway/service layout. CPU and
fast template lookup defaults to `redhat-ods-applications`; adjust
`deployment.baseRefNamespace`, `baseRefRegex`, `baseRefAccelerator`, and
`baseRefTopology` in a copied testcase for other installations. Fast images are
selected by baseRef, not pinned as arbitrary replacement images. Legacy scorer
compatibility must be selected for a suitable installed scheduler version.

`validation.odh.soakSeconds` controls each API verification (default 10).
Original `SOAK_TEST_DURATION` environment-variable wiring is replaced by this
YAML setting. Existing `--bearer-token` is usable for discover mode against an
authenticated service. Resource assertions still need Kubernetes access even
with `--endpoint`; this is not an endpoint-only validation mode. CPU manifests
assume supported x86 runtime templates. GPU cases are NVIDIA-specific; per-node
prerequisites and multinode spread assertions are retained.

For locally checking code and collection without cluster access:

```bash
uv sync --extra dev
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run pytest tests/test_smoke.py tests/test_odh.py
uv run pytest tests/test_conformance.py --collect-only --profile configs/profiles/odh.yaml
```

## Tests not ported

The following **45 variants** have no complete runnable counterpart. Each row
names the original class/method; the file column distinguishes identical method
names in the two upgrade suites. These are proposed work, not skipped placeholder
tests added to the target suite.

| Source file | Source class and method | Variant |
|---|---|---|
| `test_llmd_auth.py` | `TestLLMISVCAuth.test_llmisvc_authorized` | auth |
| `test_llmd_auth.py` | `TestLLMISVCAuth.test_llmisvc_unauthorized` | auth |
| `test_llmd_connection_cpu.py` | `TestLlmdConnectionCpu.test_llmd_connection_cpu` | s3 |
| `test_llmd_connection_gpu.py` | `TestLlmdConnectionGpu.test_llmd_connection_gpu` | s3 |
| `test_llmd_kueue_integration.py` | `TestLlmdKueueIntegration.test_kueue_llmd_scaleup` | kueue |
| `test_llmd_local_model_cache.py` | `TestLLMDModelCacheSmoke.test_llmd_local_model_cache_reaches_node_downloaded` | model-cache |
| `test_llmd_local_model_cache.py` | `TestLLMDModelCacheSmoke.test_cached_llmisvc_inference_succeeds` | model-cache |
| `test_upgrade_llmd.py` | `TestLlmdPreUpgrade.test_llmisvc_no_auth_exists` | — |
| `test_upgrade_llmd.py` | `TestLlmdPreUpgrade.test_no_auth_inference` | — |
| `test_upgrade_llmd.py` | `TestLlmdPostUpgrade.test_llmisvc_exists_post_upgrade` | — |
| `test_upgrade_llmd.py` | `TestLlmdPostUpgrade.test_ready_post_upgrade` | — |
| `test_upgrade_llmd.py` | `TestLlmdPostUpgrade.test_no_auth_inference_post_upgrade` | — |
| `test_upgrade_llmd.py` | `TestLlmdPostUpgrade.test_no_auth_repeated_inference_post_upgrade` | — |
| `test_upgrade_llmd.py` | `TestLlmdPostUpgrade.test_gateway_post_upgrade` | — |
| `test_upgrade_llmd.py` | `TestLlmdPostUpgrade.test_generation_unchanged_post_upgrade` | — |
| `test_upgrade_llmd.py` | `TestLlmdPostUpgrade.test_url_unchanged_post_upgrade` | — |
| `test_upgrade_llmd.py` | `TestLlmdPostUpgrade.test_replicas_unchanged_post_upgrade` | — |
| `test_upgrade_llmd.py` | `TestLlmdPostUpgrade.test_model_uri_unchanged_post_upgrade` | — |
| `test_upgrade_llmd.py` | `TestLlmdPostUpgrade.test_container_images_unchanged_post_upgrade` | — |
| `test_upgrade_llmd.py` | `TestLlmdPostUpgrade.test_restart_counts_unchanged_post_upgrade` | — |
| `test_upgrade_llmd.py` | `TestLlmdPostUpgrade.test_config_refs_exist_post_upgrade` | — |
| `test_upgrade_llmd.py` | `TestLlmdPostUpgrade.test_config_refs_unchanged_post_upgrade` | — |
| `test_upgrade_llmd.py` | `TestLlmdPostUpgrade.test_inference_pool_exists_post_upgrade` | — |
| `test_upgrade_llmd.py` | `TestLlmdPostUpgrade.test_httproute_exists_post_upgrade` | — |
| `test_upgrade_llmd.py` | `TestLlmdPostUpgrade.test_controller_healthy_post_upgrade` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePreUpgrade.test_llmisvc_auth_and_kueue_exists` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePreUpgrade.test_auth_inference` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePreUpgrade.test_kueue_scale_and_gate` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePostUpgrade.test_llmisvc_exists_post_upgrade` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePostUpgrade.test_kueue_local_queue_exists_post_upgrade` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePostUpgrade.test_kueue_conditions_post_upgrade` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePostUpgrade.test_kueue_integration_stats_unchanged_post_upgrade` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePostUpgrade.test_auth_inference_post_upgrade` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePostUpgrade.test_auth_repeated_inference_post_upgrade` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePostUpgrade.test_auth_unauthorized_post_upgrade` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePostUpgrade.test_generation_unchanged_post_upgrade` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePostUpgrade.test_url_unchanged_post_upgrade` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePostUpgrade.test_replicas_unchanged_post_upgrade` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePostUpgrade.test_model_uri_unchanged_post_upgrade` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePostUpgrade.test_container_images_unchanged_post_upgrade` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePostUpgrade.test_restart_counts_unchanged_post_upgrade` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePostUpgrade.test_config_refs_exist_post_upgrade` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePostUpgrade.test_config_refs_unchanged_post_upgrade` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePostUpgrade.test_inference_pool_exists_post_upgrade` | — |
| `test_upgrade_llmd_auth_kueue.py` | `TestLlmdAuthKueuePostUpgrade.test_httproute_exists_post_upgrade` | — |

The **61 partial variants** also remain on the parity backlog:

* `TestLlmdNoScheduler.test_llmd_no_scheduler[no-scheduler]`: HF replaces S3.
* `TestSingleNodeEstimatedPrefixCache.test_singlenode_estimated_prefix_cache`
  and its 19 `test_openai_api_compat_soak` verifications: HF replaces S3 and
  per-service authenticated access is not provisioned.
* `TestSingleNodePrecisePrefixCache.test_singlenode_precise_prefix_cache` and
  its 19 API verifications for each of `scorer` and `producer`: model sources
  are preserved, but per-service authenticated/trusted-TLS fixture execution
  remains absent.

Every expanded partial ID is in the JSON inventory. Thus **188 variants have
ported assertion logic**, while **106 variants still need full parity work**
(45 deferred plus 61 partial). Fixture-level AMD/platform/disconnected gaps also
apply outside these counts. The [framework proposal](odh-framework-proposal.md)
orders the work needed to close them.

## Validation status

On 2026-09-29, Python 3.14.7 local checks completed successfully:

* `pytest tests/test_smoke.py tests/test_odh.py`: **89 passed** (50 existing,
  39 migration regressions).
* `ruff check src/ tests/` and `ruff format --check src/ tests/`: passed.
* Complete `odh` profile: **720 phase items collected** in service lifecycle
  order, including all 19 compatibility verifications before each cleanup.
* `git diff --check` and CLI testcase listing: passed.

Local checks cover source-equivalent assertions, intentional failure cases,
actual SDK HTTP/SSE decoding, all bundled manifests/profile references,
collection order, GPU accounting and discover cleanup. Cluster deployment,
server-side CRD validation, real inference, routing metrics and platform upgrades
remain unvalidated. This branch must not be treated as a passing product
qualification run. The user requested a pause before further validation; no
cluster commands or deployments were run during this migration.
