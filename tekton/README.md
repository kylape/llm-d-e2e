# Tekton smoke pipeline

This directory contains the proof-of-concept Tekton pipeline for running the
`smoke` profile against an existing RHAII installation.

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

The `llm-d-e2e` namespace must contain `rhai-pull-secret`. The pipeline service
account also needs access to the inference gateway service in
`redhat-ods-applications`, as described in `namespace-rbac.yaml`.

## Runner image build

The repository includes a separate image-build Pipeline. It builds
`Dockerfile.tekton` from the personal fork and publishes the Red Hat UBI9
Python 3.11-based runner image to Quay. The image includes Python dependencies,
kubectl, the e2e suite, and the pinned smoke manifest.

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

The only PipelineRun parameters are:

* `vllm_image` — an immutable vLLM image reference. It is assigned to
  `spec.template.containers[name=main].image`.
* `model` — a model URI such as `hf://Qwen/Qwen3-0.6B`.

The deploy task renders the service from the pinned
`llm-d-conformance-manifests` smoke resource. The smoke task then invokes
`llm-d-e2e` in discover mode, and the finally task removes the unique service
and its generated workloads.
