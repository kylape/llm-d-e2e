"""GuideLLM benchmark runner wrapper."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .config import BenchmarkConfig

try:
    from guidellm.benchmark import (
        BenchmarkScenario,
        GenerativeBenchmarksReport,
        benchmark_generative_text,
    )

    GUIDELLM_AVAILABLE = True
except ImportError:
    GUIDELLM_AVAILABLE = False
    BenchmarkScenario = None
    GenerativeBenchmarksReport = None


@dataclass
class BenchmarkResult:
    """Wrapper around GuideLLM report with convenience accessors."""

    report: GenerativeBenchmarksReport | None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.report is not None and self.error is None

    @property
    def ttft_p95_ms(self) -> float | None:
        if not self.report or not self.report.benchmarks:
            return None
        metrics = self.report.benchmarks[0].metrics
        if metrics.time_to_first_token_ms and metrics.time_to_first_token_ms.successful:
            return metrics.time_to_first_token_ms.successful.percentiles.p95
        return None

    @property
    def ttft_p99_ms(self) -> float | None:
        if not self.report or not self.report.benchmarks:
            return None
        metrics = self.report.benchmarks[0].metrics
        if metrics.time_to_first_token_ms and metrics.time_to_first_token_ms.successful:
            return metrics.time_to_first_token_ms.successful.percentiles.p99
        return None

    @property
    def itl_p95_ms(self) -> float | None:
        if not self.report or not self.report.benchmarks:
            return None
        metrics = self.report.benchmarks[0].metrics
        if metrics.inter_token_latency_ms and metrics.inter_token_latency_ms.successful:
            return metrics.inter_token_latency_ms.successful.percentiles.p95
        return None

    @property
    def itl_p99_ms(self) -> float | None:
        if not self.report or not self.report.benchmarks:
            return None
        metrics = self.report.benchmarks[0].metrics
        if metrics.inter_token_latency_ms and metrics.inter_token_latency_ms.successful:
            return metrics.inter_token_latency_ms.successful.percentiles.p99
        return None

    @property
    def throughput_tok_s(self) -> float | None:
        if not self.report or not self.report.benchmarks:
            return None
        metrics = self.report.benchmarks[0].metrics
        if metrics.output_tokens_per_second and metrics.output_tokens_per_second.successful:
            return metrics.output_tokens_per_second.successful.mean
        return None

    @property
    def error_rate(self) -> float | None:
        if not self.report or not self.report.benchmarks:
            return None
        metrics = self.report.benchmarks[0].metrics
        totals = metrics.request_totals
        if totals is None:
            return None
        total = totals.successful + totals.errored + totals.incomplete
        if total == 0:
            return 0.0
        return (totals.errored + totals.incomplete) / total

    @property
    def requests_successful(self) -> int:
        if not self.report or not self.report.benchmarks:
            return 0
        metrics = self.report.benchmarks[0].metrics
        return metrics.request_totals.successful if metrics.request_totals else 0

    @property
    def requests_errored(self) -> int:
        if not self.report or not self.report.benchmarks:
            return 0
        metrics = self.report.benchmarks[0].metrics
        return metrics.request_totals.errored if metrics.request_totals else 0


async def _run_benchmark_async(
    endpoint: str,
    config: BenchmarkConfig,
    model: str,
) -> BenchmarkResult:
    """Run GuideLLM benchmark asynchronously."""
    if not GUIDELLM_AVAILABLE:
        return BenchmarkResult(report=None, error="GuideLLM not installed")

    try:
        scenario = BenchmarkScenario.create(
            scenario=None,
            spec={
                "backend": {
                    "kind": "openai_http",
                    "target": endpoint,
                    "model": model,
                },
                "profile": {
                    "kind": config.profile,
                    "rate": config.concurrency,
                },
                "constraints": [
                    {"kind": "max_duration", "seconds": config.duration},
                ],
                "data": [
                    {
                        "kind": "synthetic_text",
                        "prompt_tokens_min": config.input_tokens_min,
                        "prompt_tokens_max": config.input_tokens_max,
                        "output_tokens_min": config.output_tokens_min,
                        "output_tokens_max": config.output_tokens_max,
                    }
                ],
            },
        )

        report, _ = await benchmark_generative_text(scenario)
        return BenchmarkResult(report=report)

    except Exception as e:
        return BenchmarkResult(report=None, error=str(e))


def run_benchmark(
    endpoint: str,
    config: BenchmarkConfig,
    model: str,
) -> BenchmarkResult:
    """Run GuideLLM benchmark synchronously."""
    return asyncio.run(_run_benchmark_async(endpoint, config, model))
