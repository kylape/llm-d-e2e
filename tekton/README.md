# Tekton smoke pipeline

This directory contains the proof-of-concept Tekton pipeline for running the
`smoke` profile against an existing RHAII installation.

The original `pipeline.yaml` is the direct-deployment fallback. The
`pipeline-matrix.yaml` variant runs each image/model leg through Burrito, which
wraps the RHAII service and e2e Job in an AppWrapper for Kueue admission.

## Prerequisites

Install Tekton Pipelines at a pinned version. For a PoC, install only the
Pipelines component; Triggers, Dashboard, Chains, and the Tekton Operator are
not required for manual `PipelineRun` execution.

For example, use the pinned official release manifest:

```bash
kubectl apply --filename https://infra.tekton.dev/tekton-releases/pipeline/previous/v1.15.0/release.yaml
```

Before installing the resources in this directory, verify that the
`tekton-pipelines` controllers are ready and that the cluster has the RHAII
`LLMInferenceService` CRD and a ready provider/KServe installation.

The `klape-llm-d-e2e` namespace must contain `rhai-pull-secret`. The pipeline service
account also needs access to the inference gateway service in
`redhat-ods-applications`, as described in `namespace-rbac.yaml`.

## Runner image build

The repository includes a separate image-build Pipeline. It builds
`Dockerfile.tekton` from the personal fork and publishes the Red Hat UBI9
Python 3.11-based runner image to Quay. The image includes Python dependencies,
kubectl, Burrito built from the pinned personal fork revision, the e2e suite,
and the pinned smoke manifest.

Create a Quay repository and a registry Secret named
`klape-tekton-pull-secret`. The
Secret must contain a `.dockerconfigjson` key and must not be committed to Git.
For a manual run, install `image-build-rbac.yaml`,
`image-build-task.yaml`, and `image-build-pipeline.yaml`, then adapt
`image-build-pipelinerun.example.yaml` with the actual Quay namespace. The
example builds:

```text
quay.io/klape/llm-d-e2e:tekton-poc
```

The Buildah step requires a privileged pod and uses the `vfs` storage driver.
The cluster's admission policy must explicitly permit this for the builder
service account. If that is not allowed, use an approved image-build service.

The PipelineRun publishes `image-digest`. After a successful build, update the
three qualification Task files to use the resulting immutable reference:

```text
quay.io/klape/llm-d-e2e@sha256:<digest>
```

The tag is only a bootstrap reference and should not be used for a shared or
long-lived qualification environment.

## Install the pipeline resources

Apply `namespace-rbac.yaml`, the image-build resources, the three files under
`tasks/`, and `pipeline.yaml`. These manifests create cluster and namespace
resources, so review them and obtain explicit approval before running any
mutating `kubectl` command. Build the runner image before creating a
qualification PipelineRun.

Run the example with:

```bash
kubectl create -f pipelinerun.example.yaml
```

The direct-deployment PipelineRun parameters are:

* `vllm_image` — an immutable vLLM image reference. It is assigned to
  `spec.template.containers[name=main].image`.
* `model` — a model URI such as `hf://Qwen/Qwen3-0.6B`.

## Burrito matrix pipeline

The matrix example is currently intentionally small: it contains one known
good RHAII 3.5 image/model pair. Add explicit entries to
`pipeline-matrix.yaml` as compatibility pairs are approved. Tekton expands the
matrix into one TaskRun per pair; the TaskRun invokes Burrito with a unique
AppWrapper, Service, and completion Job name.

Kueue controls GPU admission. Tekton only creates the matrix TaskRuns, so
additional legs can remain pending in the LocalQueue until GPU quota is
available. The target namespace is shared for this initial implementation and
resource names are made unique from the TaskRun name.

Each Burrito matrix leg creates a uniquely named results PVC and mounts it at
`/results` in the completion Job. AppWrapper cleanup removes the service and
Job while preserving the result PVC for report inspection.

The Burrito path requires the target namespace to already contain
`llm-d-e2e-runner`, `rhai-pull-secret`, and the AppWrapper permissions in
`namespace-rbac.yaml`. It also requires the named LocalQueue and a compatible
Kueue/AppWrapper installation.

## Fozzie queue

`kueue/fozzie-queue.yaml` defines a deliberately namespace-scoped queue for
this PoC. It accounts for `nvidia.com/gpu` rather than the unrelated
`benchflow.io/remote-gpu` resource used by the existing `local` queue. The
initial quota is four GPUs, so four one-GPU legs may be admitted concurrently;
additional matrix legs remain pending until quota is released.

Review this manifest and obtain approval before applying it to Fozzie. The
ClusterQueue is cluster-scoped even though its namespace selector restricts
workloads to `klape-llm-d-e2e`.

The cleanup Task publishes `qualification-status` as a Tekton Task result and
writes the same value to the results workspace. Tekton does not permit a
Pipeline-level result to reference a Task in `finally`, so consumers should
read that TaskRun result or workspace artifact. The deploy task renders the
service from the pinned
`llm-d-conformance-manifests` smoke resource. The smoke task then invokes
`llm-d-e2e` in discover mode, and the finally task removes the unique service
and its generated workloads.

## Negative-path validation

The repository includes two validation PipelineRuns:

* `pipelinerun.failure-infrastructure.example.yaml` uses a nonexistent vLLM
  image and a two-minute readiness timeout. It should produce
  `INFRASTRUCTURE_FAILURE` while still running cleanup.
* `pipelinerun.failure-test.example.yaml` deploys the known-good service but
  overrides the model name used by the smoke test. The service becomes Ready,
  the inference test fails, and cleanup should produce `TEST_FAILURE`.

These manifests validate failure handling and are not normal pipeline inputs.
