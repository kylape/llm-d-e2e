# Extending llm-d-e2e to run the remaining ODH tests

Date: 2026-09-29. This document describes capabilities needed to share the
remaining ODH coverage across llm-d-e2e and product test harnesses.
The changes described below have not been implemented on `port/odh-llmd-tests`.
Follow-up review: 2026-10-05, aligned with the draft Testing SIG plans cited
below. These capabilities may be implemented by shared checks and product
harnesses; the proposal does not require moving every ODH scenario into this
repository.

The remaining tests need the runner to set up more things around a model and
follow what happens to them over time. Examples include two users accessing
different models, a second model replica waiting for permission to run, and a
model service surviving a platform upgrade. This introduction explains what the
runner does today before describing those additions.

## Architectural direction and scope

The draft Testing SIG roadmap already proposes a portable `llm-d-e2e` core
with product-specific deployment and lifecycle code in `opendatahub-tests`
or the responsible product harness. Its milestones call for every ODH llm-d
test to be mapped to shared assertions or a documented product responsibility,
then for ODH to consume a pinned llm-d-e2e interface. They explicitly do not
require duplicating ODH authentication, storage or lifecycle setup in this runner.

This proposal follows that division. The six capability sections describe what
the remaining tests need. Existing ODH fixtures can supply much of that setup;
new standalone setup is warranted where another execution path, such as AKS,
needs the same product coverage. Keeping a product scenario in ODH with shared
assertions is a valid outcome. The inventory's “not ported” status describes
this branch's implementation, not an obligation to relocate every scenario.

Before expanding standalone scenario setup, define a versioned interface for
profiles, validators and structured results, and integrate one ODH product test
through it. Product fixtures prepare the service and supply endpoints,
credentials and observations to shared checks. The runner's current YAML
profiles select testcase lists; they do not yet provide the roadmap's full
versioned configuration and evidence contract.

The operating model places deployment, upgrade and cleanup coordination
logically outside the validators, while retaining simple `deploy` mode for
standalone use. Results need a common run ID, resolved image/model/test
revisions, deployment path, simulator identity, ownership and failure details.
A separate collector can preserve diagnostics after the test process fails.
Passing a direct or simulated run must not be reported as validation of a
supported product path.

Sources: the draft [roadmap](https://github.com/kylape/llm-d-inference-engineering-docs/blob/4ac6b274fe24f174e498c5b10257b11bcb30ff82/content/sigs/testing/roadmap/_index.md),
[milestones](https://github.com/kylape/llm-d-inference-engineering-docs/blob/4ac6b274fe24f174e498c5b10257b11bcb30ff82/content/sigs/testing/roadmap/milestones.md),
and [operating model](https://github.com/kylape/llm-d-inference-engineering-docs/blob/4ac6b274fe24f174e498c5b10257b11bcb30ff82/content/sigs/testing/roadmap/operating-model.md),
reviewed at `4ac6b27` on `docs/testing-sig`. These are planned boundaries and
requirements; they are not all implemented in the current framework.

## How llm-d-e2e works today

**llm-d-e2e is the program that sets up and checks a model deployment.** It sends
requests to the deployed model and examines Kubernetes objects and metrics to
see whether the deployment behaves as expected.

A model deployment starts with an `LLMInferenceService`: a Kubernetes object
that describes the model to serve and how to run it. KServe's controller reads
that object and creates the pods and routing configuration needed to serve the
model. Pods run the containers; the gateway is the entry point through which
clients send inference requests. One `LLMInferenceService` can involve several
pods, including separate prefill and decode pods or workers on multiple nodes.

The repository separates a test's instructions into three kinds of YAML files:

| File | What it tells the runner | Example from the migration |
|---|---|---|
| Testcase | Which deployment to use and which checks to run | `configs/testcases/odh-pd.yaml` |
| Deployment manifest | The actual `LLMInferenceService` Kubernetes should create: model, containers, resources and routing settings | `configs/manifests/odh-pd.yaml` |
| Profile | A list of testcases to run together | `configs/profiles/odh-core.yaml` |

For example, running `llm-d-e2e -t odh-pd` selects the prefill/decode testcase.
The command starts pytest, the Python test runner, which runs that testcase
through a sequence of phases:

1. Check prerequisites, such as the service API being installed and enough GPUs
   being available.
2. Create the `LLMInferenceService` described by the manifest.
3. Wait for its pods, routing and service to become ready.
4. Run the selected checks: inspect the deployment, send model requests and
   check the responses and metrics. A check may verify that prompt processing
   happened on the prefill pod and that its computed cache reached the decode pod.
5. Delete the deployed service and the supporting access configuration that the
   runner knows how to clean up.

The Python test methods implement those phases. A helper called `Deployer`
performs Kubernetes operations through `kubectl`. HTTP clients send requests,
and a metrics helper reads counters from the pods. Pytest supplies these helpers
to tests through **fixtures**: functions that prepare something a test needs and
can clean it up afterward. The source ODH suite uses fixtures extensively for
accounts, credentials, queues and other setup.

The target runner's usual unit of work is **one model service through one
create → check → delete sequence**. A profile repeats that sequence for several
testcases. Running a profile with two cases does not by itself arrange for two
services to coexist while a test checks their interaction.

Two existing options change parts of that sequence. `--mode discover` checks a
service that already exists; the migration branch also fixes cleanup so this
mode preserves that service afterward. `--nocleanup` leaves a
newly deployed service in place. Neither option saves a record of the service's
original state for a later test run to compare after an upgrade.

## What the existing Jenkins upgrade pipeline does

Jenkins already runs llm-d-e2e around a real RHAII platform upgrade on Azure
Kubernetes Service (AKS). RHAII is installed as a Helm chart, a package of
Kubernetes components. The checked-in upgrade pipeline performs this sequence:

1. Provision an AKS cluster and install the starting RHAII version.
2. Run llm-d-e2e with the starting version's profile and deployment manifests.
3. Upgrade the RHAII Helm release in place on the same cluster.
4. Run llm-d-e2e with the target version's profile and manifests.
5. Archive the pre/post test reports and chart values, and delete the cluster
   when the job's cleanup setting requests it.

The job defaults to mock inference. Enabling its GPU option provisions GPU
capacity and runs without `--mock`. Separate profile and manifest settings allow
coverage to differ between the starting and target versions. The job definitions
schedule upgrade runs daily; this source review does not establish which live
builds have run or passed.

This provides upgrade coverage for the installed platform and its ability to run
conformance tests afterward. Both llm-d-e2e invocations use the normal deployment
mode. That mode creates services and normally deletes them at the end; it also
deletes any existing service of the same name before redeploying. The pipeline
does not request retained services or save their pre-upgrade state.

The ODH upgrade tests additionally check that the **original model services**
keep their configuration and behavior across the upgrade. Section 5 extends the
existing Jenkins stages with that comparison. Cluster provisioning, chart
upgrades and report archiving are already available to reuse. The source ODH
suites also depend on OpenShift authentication and Kueue setup; running their
checks on AKS requires verifying those product capabilities and their setup.

Sources reviewed on 2026-09-29: Jenkins
[`62ad0ac`, upgrade pipeline](https://gitlab.cee.redhat.com/ods/jenkins/-/blob/62ad0ac23ecd3d8ff40ac7b8b553bda7bcd5f7cd/jenkinsfiles/Jenkinsfile_rhaii_on_xks_upgrade_pipeline.groovy),
[test invocation helper](https://gitlab.cee.redhat.com/ods/jenkins/-/blob/62ad0ac23ecd3d8ff40ac7b8b553bda7bcd5f7cd/vars/runLlmDe2e.groovy),
and [job definitions](https://gitlab.cee.redhat.com/ods/jenkins/-/blob/62ad0ac23ecd3d8ff40ac7b8b553bda7bcd5f7cd/src/io/ods/jenkins/dsl/jobs/devops/rhaii_on_xks.groovy).
The default upstream runner was `60bf011`, also this migration's baseline.

## Why some source tests need more support

Consider a test that loads model files from S3 storage. It needs the storage
address and credentials before it can start the model. In Kubernetes, those
credentials can be stored in a **Secret**, and a **ServiceAccount** gives the
workload an identity through which the required configuration can be attached.
These are additional Kubernetes **resources**, meaning objects such as Secrets,
ServiceAccounts, model services and queues.

The test therefore needs to create its Secret and ServiceAccount, connect them
to the model deployment, check inference, and delete the objects it created.
If the user instead supplies an existing account or Secret, cleanup should leave
it in place. The runner needs to remember which objects it created to make that
distinction, including when a test fails halfway through setup.

A **test scenario** includes the full sequence of setup, requests, changes and
cleanup. Supporting these scenarios requires the runner to track what each test
creates and delete those objects when the test finishes.

Other remaining tests need additional sequences. An access-control test creates
two services and uses different users' credentials against them. A queue test
starts one replica, asks for a second, and expects the second to wait. An upgrade
test records the service's state, stops while an external job upgrades the
platform, then checks that the original service survived.

The migrated code adds checks that fit the existing single-service sequence.
The [coverage analysis](odh-migration.md) and
[complete test inventory](odh-migration-inventory.json) identify **45 test
variants still unported** and **61 with their checks ported but setup differences
remaining**. A variant is one source test with a particular configuration or API
check selected; several variants can depend on the same missing setup feature.
The capabilities below close those differences through shared checks and
appropriate product setup, including other hardware and platform configurations.

## Where the remaining capabilities belong

| Section | Capability | Implementation boundary | Tests enabled or completed |
|---|---|---|---|
| 1 | Track and clean up test-created objects, including storage credentials | Product fixtures or a standalone deployment adapter | S3 loading, no-scheduler and estimated prefix caching |
| 2 | Keep two services running and send requests as different users | Product auth/service setup with reusable request checks | Access-control tests and authenticated setup for 60 cache/API variants |
| 3 | Change a deployment and check a deliberately waiting replica | Product Kueue scenario with reusable state/inference checks | Replica quota test |
| 4 | Create and check the product's model-file cache | Product cache fixtures and integration assertions | Two LocalModelCache tests |
| 5 | Save service state and compare it after an upgrade | Existing upgrade harness with retained services and shared comparisons | 38 upgrade variants |
| 6 | Detect platform/hardware and retain failure evidence | Deployment adapters, shared result metadata and external evidence collection | AMD execution, disconnected environments, prerequisites and diagnostics |

Sections 1 and 2 describe setup reused by later scenarios. They do not require
moving that setup out of ODH. For any newly implemented standalone adapter,
compare source and target on the same product version before marking a complete
scenario equivalent or retiring its existing implementation.

## 1. Track what each test creates and deletes

For a standalone deployment adapter that needs additional resources, add a
per-test record of created Kubernetes objects and a cleanup function for each
one. ODH callers can continue to use their existing resource fixtures. For the S3 example, the record would include the Secret,
ServiceAccount and model service. Cleanup would delete the model service before
removing the account and credentials it used.

Record each object's type, namespace, name and unique Kubernetes identifier
(UID), together with whether the test created it or was given an existing object
to use. This lets cleanup preserve user-supplied objects. Run cleanup even if
setup fails or pytest stops at the first failure with `-x`; reaching the final
numbered test phase should not be the only way to release resources.

Testcase configuration should let the manifest refer to the prepared account
and Secret by name. Obtain credential values from existing Secret references
or the execution environment, and keep the values out of testcase files and
reports. Use the current `kubectl` helper to create and inspect these objects.
Keep the test's sequence of actions in Python, with YAML describing the desired
configuration.

For the S3 tests, reproduce the Secret annotations and ServiceAccount connection
in source `llmd/conftest.py`, using the original TinyLlama S3 model address.
Delete test-created objects even when the model fails to start. Record whether
the environment can reach the required storage, including on clusters with no
external network access.

This completes the two deferred `test_llmd_connection_*[s3]` variants and restores
S3 setup in the no-scheduler and estimated-prefix-cache tests. A mode using
previously configured credentials can also be useful; its results should state
that credential setup was supplied rather than exercised by the test.

## 2. Test two services with different users' credentials

Use the product harness to create service A and service B and keep both
running. Shared request checks must accept both the destination service and
the credentials for each request. The HTTP client
needs to support requests with no token, a supplied bearer token (the credential
sent with an HTTP request), or a ServiceAccount token that can be renewed when
it expires. The current runner exposes one supplied bearer token for its clients.

Also support checking the gateway's TLS certificate using a trusted certificate
authority (CA) bundle. The current clients disable certificate verification.
Allow the gateway address and namespace to be configured, and use the hostname
that the certificate covers.

Reproduce the source test's two accounts and permissions. Kubernetes
role-based access control (RBAC) should grant each account access to its own
service. Then make these requests through the gateway:

* Each user calls their own service. Require HTTP 200 and a completion containing
  “Rome” when asked “What is the capital of Italy?” This is the source suite's
  simple factual inference check.
* User B calls service A, and an unauthenticated client calls service A. Require
  HTTP 401 or 403, the expected denial responses.

Check denial only after the service and its access policy are ready. A network
failure or a server error does not show that access control is working. Preserve
the HTTP status and response body so tests can make that distinction. These
requests must pass through the gateway where access control is enforced;
connecting directly to a model pod would bypass that part of the test.

Reuse the token and certificate handling for the estimated and precise cache
tests, which use authenticated requests in the source suite. The migrated API
checks can continue to test request/response behavior, with their credentials
supplied by this setup code.

## 3. Change a running service and check which replicas may run

Kueue controls when workloads may start based on available quota. The source
test gives it enough quota for one model replica, then scales the service from
one replica to two. Success means the first replica keeps serving while Kueue
holds the second back.

Keep the update-and-wait sequence in the product scenario or deployment
adapter, and expose the resulting pod state to its assertions. The usual runner waits for all pods to be Running. This particular
test needs an explicit expectation of one running pod and one pod held by
Kueue's scheduling gate. A pod waiting because its image cannot be downloaded
must fail this check.

Create the three queue objects used by the source: a `ResourceFlavor` describing
the resource type, a `ClusterQueue` defining the quota, and a `LocalQueue` through
which the test namespace uses that quota. Add the namespace/workload labels that
connect the service to Kueue, and track these objects for cleanup.

Preserve the source's resource settings: 3 CPU and 20Gi memory of quota; each
replica requests 2 CPU/6Gi and has limits of 3 CPU/20Gi. Verify one deployment
with one replica, scale the service to two, and require one running and one
Kueue-gated pod within the source timeouts. Then repeat the successful factual
inference check to show the running replica still serves requests.

If the test needs to change shared platform settings to enable Kueue integration,
record the old values, make the planned changes explicit and restore them
later. Use a test-specific queue so another workload's quota is preserved.

## 4. Test the product's model-file cache

This feature caches **model files on cluster nodes**, so services can reuse them
instead of downloading them again. It is different from the **KV cache** tested
by the migrated prefix/P/D tests, which stores intermediate inference results.

The source tests use `LocalModelNamespaceCache`, a Kubernetes object managed by
a product controller. The product fixtures or deployment adapter create its prerequisites: storage credentials, a
`LocalModelNodeGroup` defining the participating nodes/storage, and any required
persistent volume claims (PVCs), which are Kubernetes requests for storage.
Then create the cache object and wait for the controller to download the model.

Check its reported status: every node must be `NodeDownloaded`; the number of
failed copies must be zero; available copies must equal total copies; and at
least one copy must exist. An empty status is not a successful download.

Next, deploy a model service with the original model address. Verify that the
controller changes the service's storage configuration to use the cached PVC.
Send an inference request and require a successful, non-empty completion. Finally,
check that the cache object's `status.llmInferenceServices` lists the service by
namespace and name. Track the test-created cache objects for cleanup and retain
any storage supplied by the user.

The repository also contains a `ModelDownloader` helper that can explicitly
download a model to a PVC. That helper alone does not test the controller's
choice to use a cached copy. Starting the service with an already configured
PVC would bypass the automatic change that these two source tests need to prove.

## 5. Check the same services before and after an upgrade

Extend the existing Jenkins pre/post test stages with preparation and
verification coordinated by the product harness. Proposed `prepare-upgrade`
and `verify-upgrade` operations can expose this sequence while reusing portable
checks. Connect them with a saved run ID and a record of the original service
state. Store that record in a Kubernetes
ConfigMap or another artifact that survives the first test process ending.
This saved record is the **baseline** against which the post-upgrade state is
compared.

Preparation creates the service without authentication and the service with
authentication plus Kueue. It runs the source's pre-upgrade checks and saves their
state, leaving the services and their supporting objects in place. Jenkins then
performs its existing in-place chart upgrade. Verification loads the record and
reconnects to those same services to check what survived. An OpenShift job could
use the same runner operations around its own platform upgrade mechanism.

`--nocleanup` is useful for preparation, but the post-upgrade operation must also
avoid deployment and its deletion of existing services. Build verification on
the existing-service discovery path and preserve the migration branch's fix
that prevents discover mode from deleting the service during cleanup. Add the
saved-state comparisons and prerequisite handling; two ordinary profile runs
do not perform them.

Keep results for preserved services separate from the current post-upgrade
conformance run, which can still exercise fresh deployments. Run preserved
service checks before any fresh case that could reuse their names, and use
separate names or namespaces to prevent interference. Preserve the existing
pre/post report artifacts and add the saved baseline and comparison results.

The current Jenkins pipeline continues after pre-upgrade test failures, marking
the build unstable. The new verification step must require a complete,
successful preparation record so failed setup cannot produce a passing survival
check. Record and reuse one runner commit across both stages; the current helper
clones the configured branch separately for each run. Record the resolved
manifest revisions as well as the selected product versions.

Save the unique object IDs, configuration revision (`generation`), URL, replica
count, model address, container images, restart counts and referenced runtime
configurations (`configRefs`). Include associated resources and the queue and
integration settings used by the authenticated service. Record the namespaces,
service names, product versions and source commit, with a version number for
the saved record's format so the later run can reject incompatible data.

Preserve all 38 source upgrade checks. These cover service existence and
readiness; single and repeated inference; unauthorized access; gateway and
controller health; queue state; unchanged integration settings; unchanged
service configuration and restart counts; and continued existence of referenced
configurations and routing objects. Those routing objects include the
`InferencePool` (the group of serving endpoints) and `HTTPRoute` (the gateway's
route to the service). The coverage report lists every method individually.

Verification must check the original objects. Creating replacement services
would lose the evidence of whether they survived the upgrade. Review the
source's exact expectations, particularly unchanged images and restart counts,
against the intended upgrade behavior before changing those assertions.
Preserve failure evidence and provide an explicit cleanup operation afterward.

## 6. Detect the environment and explain failures

Add reusable helpers that identify where the platform installed its gateway and
controllers, which Kubernetes API types and runtime templates are available,
which certificates requests should trust, and whether external storage/network
access is possible. Tests can use that information to choose valid configuration
and explain missing prerequisites.

Detect NVIDIA and AMD separately, including the correct Kubernetes GPU resource
name, number of nodes and GPUs per node. The migration branch currently supplies
NVIDIA manifests and checks per-node GPU counts. Supporting AMD requires selecting
matching runtime templates and resource requests, then validating on AMD hardware.

Keep the source's dependency health checks, with a reported reason when an
unhealthy platform prevents a test from running. On failures, collect pod state,
Kubernetes events, service conditions, logs and relevant metric samples. Keep
tokens out of the output. Reports should distinguish a failed assertion, failed
setup, an unavailable prerequisite and a test run with known setup differences
from the source.

Some checks also depend on the installed software version. The exact P/D token
accounting expectation may change when vLLM changes how it reports recomputed
tokens. The old and new precise-cache scheduler plugins support different
configurations. Make those requirements explicit so an unsupported version can
be distinguished from a regression in a supported one.

Finally, improve how tests find the pods that receive traffic. The source and
migrated topology checks identify them using known pod labels. Reading the
actual InferencePool selector would allow a later check to verify which pods
the routing configuration really selects.

## How to validate these changes

Begin cluster validation by checking that the bundled manifests are accepted
by the installed Kubernetes APIs. Run the CPU
OCI/Hugging Face tests, NVIDIA Hugging Face/no-scheduler tests and both KV offload
cases. Follow with separate prefill/decode (P/D), exact prefix-cache checks,
multinode Mixture of Experts (MoE), and optional fast-image variants. Isolate
traffic for exact-counter checks and record the product, runtime and hardware
versions so source and target results can be compared.

Confirm that failures are detected: a missing routing sidecar or scheduler plugin,
cache requests spread across the wrong pods, incorrect KV transfer counters,
malformed streaming responses and gateway errors. Check that test-created
resources are cleaned up after failures and that discover mode preserves
existing services. Local regression tests already exercise the assertion code
with controlled inputs; they do not establish that the deployed product passes.

As the shared checks and required adapters are implemented, extend validation to fresh S3 credential setup,
two-user access control with certificate verification, queue gating, automatic
model-file caching and a real platform upgrade. Update the inventory for each
source variant whose full setup and checks have been reproduced and validated.
