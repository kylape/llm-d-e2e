# Framework changes for the remaining ODH llm-d tests

Date: 2026-09-29. Proposal only; the lifecycle changes below are not implemented
on `port/odh-llmd-tests`.

The [coverage analysis](odh-migration.md) and
[per-variant inventory](odh-migration-inventory.json) define the acceptance set:
45 unported variants, 61 partial variants, and shared platform/accelerator
fixtures. Existing generic conformance overlap does not close these gaps.

## Current execution boundary

The runner owns one service per testcase, one namespace per Deployer, one
gateway client and one direct-pod client. Deployment reads a single YAML document
and cleanup targets the service and known metrics RBAC. Although some resource
count helpers parse multiple YAML documents, `deploy()` still uses
`yaml.safe_load()` and patches one service. A multi-document manifest is not a
substitute for a scenario resource lifecycle.

Ordered methods assume that every workload should become Running and the
service should become Ready before validating inference. `tc` parametrization
reuses the same method set; it does not express multi-service operations,
intentional Pending pods, independent identities or a durable upgrade boundary.
`--nocleanup` retains resources but supplies neither a baseline nor a resume
protocol. Discover mode preserves an existing service after the cleanup fix,
but does not establish its pre-upgrade identity or previous configuration.

The current HTTP client can supply one bearer token and disables TLS verification.
It cannot reproduce two users, resource-scoped RBAC, trusted gateway certificates,
token rotation or the absence of credentials alongside a credentialed request.
The new compatibility validator consumes credentials; it does not own them.

The existing `ModelDownloader` handles explicit model-to-PVC download jobs. The
product LocalModelNamespaceCache tests exercise a separate controller, node
cache status and service mutation. A successful PVC download cannot prove those
behaviors. Likewise, the current positive inference/metric checks cannot prove
queue admission or tenant isolation.

## Proposed implementation sequence

| Step | Capability | Coverage unlocked | Completion gate |
|---|---|---|---|
| 1 | Owned scenario resources and credential providers | S3 CPU/GPU, S3 no-scheduler, estimated cache storage parity | Positive inference through source URI with fresh owned credentials and cleanup on failure |
| 2 | Named services, identities and trusted endpoint clients | 2 auth tests and authenticated setup for 60 cache/API variants | Owner succeeds; cross-user and absent credentials fail; trusted TLS verified |
| 3 | Mutation steps and explicit expected readiness | Kueue scale-up test | One running and one gated replica after scale-up, with successful inference |
| 4 | Local model cache lifecycle | 2 LocalModelCache tests | NodeDownloaded/copy status, actual PVC rewrite and service back-reference |
| 5 | Durable prepare/verify upgrade sessions | 38 upgrade variants | Baseline survives process restart and real platform upgrade; every source invariant checked |
| 6 | Platform/accelerator capability adapters | AMD, disconnected skips, source health/diagnostics parity | Profile selection reflects detected capabilities and preserves diagnostic evidence |

Steps 1–2 provide the ownership foundation reused by steps 3–5. Step 6 can be
implemented alongside those steps; hardware-specific parity requires suitable
clusters. Each completed scenario should run alongside its source counterpart
before its inventory disposition changes. Source tests should remain until that
comparison has been reviewed.

## 1. Own dependent resources and credentials

Add a scenario context with a registry of owned resources and a teardown stack.
Represent each object with API version, kind, namespace, name and observed UID.
Explicitly distinguish pre-existing references from newly created objects.
Rollback should remove only resources created by the run, in reverse dependency
order. Finalization must run when a test fails or `-x` ends the session; relying
on reaching phase 99 leaves intermediate resources behind.

Allow testcase manifests to reference staged Secrets, ServiceAccounts and other
objects through named bindings. Keep secrets out of testcase YAML and reports;
resolve secret data from supplied references or the execution environment.
Use the existing kubectl adapter for object operations rather than bringing the
entire OpenShift wrapper fixture tree into this package. YAML should describe
resources and scenario selection; Python scenario implementations should express
the tested operations rather than introducing arbitrary shell execution in YAML.

For S3, create the same endpoint/credential Secret annotations and
ServiceAccount binding used by source `llmd/conftest.py`. Preserve the original
TinyLlama S3 URI. Teardown must retain caller-owned secrets/accounts and remove
run-owned objects even if model initialization fails. Add explicit storage
capability selection for external access and disconnected environments.

Acceptance includes the two deferred `test_llmd_connection_*[s3]` variants,
restoration of S3 in `test_llmd_no_scheduler`, and the S3 setup of estimated
prefix-cache tests. Testing an already provisioned account can be supported as
an additional mode, but must not count as validating credential provisioning.

## 2. Model a service pair and independent identities

Support named service instances within one scenario and a client factory keyed
by service and identity. Add credential providers for no credentials, supplied
bearer token and ServiceAccount tokens with refresh. Add configurable TLS
verification with a CA bundle; preserve endpoint hostnames where certificate
validation requires them. Gateway discovery must be configurable independently
of the service namespace and must expose the actual authenticated product route.

Port the source auth fixture: create two services, distinct service accounts and
resource-scoped roles/bindings; obtain owner tokens and CA material. Preserve
both source assertions:

* Each owner's token receives HTTP 200 and a Rome answer from its service.
* User B's token against service A, and no token against service A, return
  HTTP 401 or 403. Network failure or arbitrary 5xx must never count as denial.

Run requests through the gateway enforcing the auth policy, with certificate
verification enabled. Direct-pod port forwarding does not prove this policy.
Expose HTTP status/body for negative assertions without conflating failed
requests with exception-only control flow. Check the denial rule after policy
readiness, not while the endpoint is generally unavailable.

The same provider closes per-service token/TLS setup gaps in the estimated and
precise cache scenarios. Retain the new raw SDK verification implementation;
replace its constant token wiring with the provider at the scenario boundary.

## 3. Express expected Pending state and controlled mutations

Add a scenario method for applying a service update and waiting for the observed
resource generation/replica state. Expectations must allow an intentional
Pending/scheduling-gated pod alongside a Ready serving replica. This is a
scenario-specific readiness rule, not a relaxation of generic readiness checks.

Create and own the source ResourceFlavor, ClusterQueue and LocalQueue, plus the
required namespace and workload labels. Configure 3 CPU and 20Gi quota against
a workload requesting 2 CPU/6Gi and limiting 3 CPU/20Gi. Observe one deployment
with one replica, scale the service to two, then require one running and one
gated pod within the source timeouts. Inspect scheduling-gate and admission
state rather than interpreting any Pending pod as a successful quota gate.
Require HTTP 200 and Rome after scaling.

Kueue integration settings belong to the platform adapter. If they must be
changed to run a scenario, snapshot and restore them and make that mutation
explicit in the run plan. Do not alter a shared cluster queue to manufacture a
passing result.

## 4. Exercise LocalModelNamespaceCache as a product feature

Stage the source's cache dependencies: storage credentials, LocalModelNodeGroup,
node storage/PVC prerequisites and LocalModelNamespaceCache. Wait for
`status.nodeStatus` to exist and for every state to be `NodeDownloaded`.
Require `copies.failed == 0`, `available == total` and at least one available
copy. Empty status must fail rather than passing vacuously.

Deploy the service with its original cacheable URI and prove that the product
controller rewrites it to use the cached PVC. A testcase that manually starts
from a `pvc://` URI would bypass the behavior being tested. Then require
successful non-empty inference and a namespace/name reference under cache
`status.llmInferenceServices`. Cleanup ownership should include dependent cache
objects while preserving any caller-owned storage.

Keep this capability separate from `--model-source pvc` and the ordinary
model-downloader helper. Source cache fixtures require more than an endpoint
and cannot be ported faithfully as an inference-only assertion.

## 5. Persist an upgrade session across processes

Introduce explicit `prepare-upgrade` and `verify-upgrade` operations with a
stable run ID and a versioned baseline format, stored in a ConfigMap or durable
artifact. Preparation creates the no-auth service and the auth+Kueue service,
runs pre-upgrade assertions, and records the source baselines. An external job
performs the operator/platform upgrade; the test runner should not infer or
start an upgrade from a version flag.

Record resource UIDs, generation, URL, replica count, model URI, container images,
restart counts, configRefs and associated resource identities. The auth+Kueue
baseline must also capture queue and integration settings used by the source.
Record namespace, service names, product versions and source commit so a later
process can reject a mismatched or incomplete baseline.

Verification must reconnect to the same objects without deploying replacements.
Preserve the source's 38 checks: basic existence and Ready state; single and
repeated inference; unauthorized access; Gateway and controller health; local
queue and Kueue admission conditions; unchanged integration settings; unchanged
generation, URL, replicas, model URI, images, restart counts and configRefs;
configRef existence; InferencePool and HTTPRoute existence. The detailed method
list is in the coverage report and JSON inventory.

Do not silently replace these exact invariants with generic readiness. In
particular, review the source's unchanged image/restart expectations against the
intended upgrade contract before relaxing them. Upgrade verification should
preserve evidence after failure and support an explicit final cleanup step.

## 6. Capabilities, diagnostics and reporting

Separate capability discovery from test expectations. Add platform providers
for applications/gateway namespaces, dependency health, installed CRDs/templates,
CA material and disconnected status. Add accelerator discovery that selects
NVIDIA or AMD resource names and enforces node count, GPUs per node and topology
requirements. Do not count all vendors' GPUs together against NVIDIA manifests.
The current branch intentionally uses NVIDIA manifests and source-like per-node
checks rather than claiming AMD coverage.

Retain source health checks as reported prerequisite outcomes, including the
reason for skip/xfail. Improve cleanup ownership, pod/events/conditions/log
capture on failure, and token redaction. Store metric snapshots and traffic
counts with source-test mapping so exact-counter failures can be diagnosed.
Report independently whether assertion code ran, a capability was absent, setup
failed, or the scenario was only partially equivalent to the source.

For generic resource discovery, eventually validate actual InferencePool
selectors and support schema/version differences explicitly. The migrated tests
currently preserve the source's workload-label approximation. Version the exact
P/D local-compute expectation and legacy/current cache plugin requirements;
capability detection must not turn genuine runtime regressions into skips.

## Validation plan after user direction

First validate the bundled CRs against the intended product version and run CPU
OCI/HF, NVIDIA HF/no-scheduler and both KV offload cases. Then run standard P/D
and exact cache cases with isolated traffic, followed by MoE and optional fast
variants. Compare source and target assertions on the same product version,
recording hardware, runtime/template versions and known metric behavior.

Check both passing and failing behavior: broken sidecar/plugin topology, traffic
spread across cache replicas, missing/wrong KV counters, malformed streaming
chunks, and forwarded gateway errors. Confirm cleanup after success/failure and
preservation in discover mode. The local regression suite already covers these
assertion classes without proving actual product behavior.

Only after adding steps 1–5 should validation include owned S3 credentials,
trusted two-user auth, quota gating, controller-managed model caching and a real
external platform upgrade. Track completion by updating individual inventory
rows; do not infer full parity from aggregate pytest pass counts.
