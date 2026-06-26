"""SLO validation logic for benchmark results."""

from __future__ import annotations

from dataclasses import dataclass, field

from .benchmark import BenchmarkResult
from .config import SLOConfig


@dataclass
class SLOCheckResult:
    """Result of a single SLO check."""

    name: str
    threshold: float
    actual: float | None
    passed: bool
    unit: str

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        actual_str = f"{self.actual:.2f}" if self.actual is not None else "N/A"
        return f"{self.name}: {actual_str}{self.unit} (threshold: {self.threshold}{self.unit}) [{status}]"


@dataclass
class SLOReport:
    """Aggregate report of all SLO checks."""

    results: list[SLOCheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def summary(self) -> str:
        lines = []
        passed_count = sum(1 for r in self.results if r.passed)
        total_count = len(self.results)
        lines.append(f"SLO Validation: {passed_count}/{total_count} passed")
        lines.append("")
        for result in self.results:
            lines.append(f"  {result}")
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.summary


def validate_slos(result: BenchmarkResult, slos: SLOConfig) -> SLOReport:
    """
    Validate benchmark results against SLO thresholds.

    Args:
        result: BenchmarkResult from GuideLLM run
        slos: SLOConfig with threshold values

    Returns:
        SLOReport with pass/fail status for each SLO
    """
    checks: list[SLOCheckResult] = []

    if slos.ttft_p95_ms is not None:
        actual = result.ttft_p95_ms
        passed = actual is not None and actual <= slos.ttft_p95_ms
        checks.append(
            SLOCheckResult(
                name="ttft_p95",
                threshold=slos.ttft_p95_ms,
                actual=actual,
                passed=passed,
                unit="ms",
            )
        )

    if slos.ttft_p99_ms is not None:
        actual = result.ttft_p99_ms
        passed = actual is not None and actual <= slos.ttft_p99_ms
        checks.append(
            SLOCheckResult(
                name="ttft_p99",
                threshold=slos.ttft_p99_ms,
                actual=actual,
                passed=passed,
                unit="ms",
            )
        )

    if slos.itl_p95_ms is not None:
        actual = result.itl_p95_ms
        passed = actual is not None and actual <= slos.itl_p95_ms
        checks.append(
            SLOCheckResult(
                name="itl_p95",
                threshold=slos.itl_p95_ms,
                actual=actual,
                passed=passed,
                unit="ms",
            )
        )

    if slos.itl_p99_ms is not None:
        actual = result.itl_p99_ms
        passed = actual is not None and actual <= slos.itl_p99_ms
        checks.append(
            SLOCheckResult(
                name="itl_p99",
                threshold=slos.itl_p99_ms,
                actual=actual,
                passed=passed,
                unit="ms",
            )
        )

    if slos.throughput_tok_s is not None:
        actual = result.throughput_tok_s
        # Throughput: higher is better, so check actual >= threshold
        passed = actual is not None and actual >= slos.throughput_tok_s
        checks.append(
            SLOCheckResult(
                name="throughput",
                threshold=slos.throughput_tok_s,
                actual=actual,
                passed=passed,
                unit="tok/s",
            )
        )

    if slos.error_rate is not None:
        actual = result.error_rate
        passed = actual is not None and actual <= slos.error_rate
        checks.append(
            SLOCheckResult(
                name="error_rate",
                threshold=slos.error_rate,
                actual=actual,
                passed=passed,
                unit="",
            )
        )

    return SLOReport(results=checks)
