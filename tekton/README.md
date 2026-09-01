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

## Runner image

Build `Dockerfile.tekton` and publish it from the personal fork. The image is
based on Red Hat UBI9 Python 3.11 and includes Python dependencies, kubectl,
the e2e suite, and the pinned smoke manifest:

```bash
podman build -f Dockerfile.tekton -t ghcr.io/kylape/llm-d-e2e:tekton-poc .
```

Before applying the pipeline, replace the example image tag in the Task files
with the immutable digest of the published image.

## Install the pipeline resources

Apply `namespace-rbac.yaml`, the three files under `tasks/`, and
`pipeline.yaml`. These manifests create cluster and namespace resources, so
review them and obtain explicit approval before running any mutating
`kubectl` command.

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
