"""Validation report: what was checked, what failed, and the resulting score."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

from smartmis.core.exceptions import DataValidationError
from smartmis.validation.checks import Dimension, Severity
from smartmis.validation.scoring import DimensionScore

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class CheckResult:
    """Public, serialisable view of one check outcome."""

    name: str
    dimension: Dimension
    severity: Severity
    message: str
    checked: int
    failed: int
    column: str | None = None
    sample_rows: tuple[int, ...] = ()
    blocking: bool = False

    @property
    def failed_pct(self) -> float:
        return round(self.failed / self.checked * 100, 2) if self.checked else 0.0


@dataclass(frozen=True)
class ValidationReport:
    """Everything the UI, reports and audit trail need about a validation run."""

    dataset_type: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    quality_score: float
    dimensions: dict[Dimension, DimensionScore]
    checks: tuple[CheckResult, ...]
    column_mapping: dict[str, str]
    issues: pd.DataFrame = field(repr=False)
    invalid_row_mask: pd.Series = field(repr=False)
    blocking_reasons: tuple[str, ...] = ()
    metrics: dict[str, float] = field(default_factory=dict)
    validated_at: datetime = field(default_factory=lambda: datetime.now().replace(microsecond=0))
    duration_seconds: float = 0.0

    # ------------------------------------------------------------------ status
    @property
    def is_blocking(self) -> bool:
        return bool(self.blocking_reasons)

    @property
    def status(self) -> str:
        if self.is_blocking:
            return "FAILED"
        return "PASSED WITH WARNINGS" if self.invalid_rows or self.failed_checks else "PASSED"

    @property
    def failed_checks(self) -> tuple[CheckResult, ...]:
        return tuple(
            sorted(
                (c for c in self.checks if c.failed and c.severity != "info"),
                key=lambda c: (_SEVERITY_ORDER[c.severity], -c.failed),
            )
        )

    def raise_if_blocking(self) -> None:
        """Raise :class:`DataValidationError` when processing must stop."""
        if self.is_blocking:
            reasons = " ".join(self.blocking_reasons)
            raise DataValidationError(
                f"Validation failed for {self.dataset_type}: {reasons}",
                user_message=f"The data cannot be processed: {reasons}",
                details={"dataset_type": self.dataset_type, "quality_score": self.quality_score},
            )

    # ----------------------------------------------------------------- output
    def checks_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "Check": c.name,
                    "Dimension": c.dimension.title(),
                    "Column": c.column or "—",
                    "Severity": c.severity.upper(),
                    "Checked": c.checked,
                    "Failed": c.failed,
                    "Failed %": c.failed_pct,
                    "Message": c.message,
                }
                for c in sorted(self.checks, key=lambda c: (_SEVERITY_ORDER[c.severity], -c.failed))
            ]
        )

    def dimensions_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "Dimension": d.dimension.title(),
                    "Weight": d.weight,
                    "Checked": d.checked,
                    "Failed": d.failed,
                    "Score %": d.score,
                }
                for d in self.dimensions.values()
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_type": self.dataset_type,
            "status": self.status,
            "total_rows": self.total_rows,
            "valid_rows": self.valid_rows,
            "invalid_rows": self.invalid_rows,
            "quality_score": self.quality_score,
            "dimensions": {k: v.score for k, v in self.dimensions.items()},
            "metrics": dict(self.metrics),
            "blocking_reasons": list(self.blocking_reasons),
            "column_mapping": dict(self.column_mapping),
            "validated_at": self.validated_at.isoformat(timespec="seconds"),
            "duration_seconds": self.duration_seconds,
            "failed_checks": [
                {
                    "name": c.name,
                    "column": c.column,
                    "severity": c.severity,
                    "failed": c.failed,
                    "message": c.message,
                }
                for c in self.failed_checks
            ],
        }

    def render_text(self, width: int = 44) -> str:
        """Plain-text summary for logs, CLI output and e-mails."""

        def line(label: str, value: str) -> str:
            return f"{label + ':':<{width - 14}}{value:>14}"

        m = self.metrics
        rows = [
            f"DATA QUALITY REPORT — {self.dataset_type.upper()}",
            "=" * width,
            line("Total Rows", f"{self.total_rows:,}"),
            "",
            line("Valid Rows", f"{self.valid_rows:,}"),
            line("Invalid Rows", f"{self.invalid_rows:,}"),
            "",
            line("Missing Values", f"{m.get('missing_values_pct', 0):.1f}%"),
            line("Duplicate Records", f"{m.get('duplicate_pct', 0):.1f}%"),
            line("Invalid Dates", f"{m.get('invalid_dates_pct', 0):.1f}%"),
            line("Invalid Numbers", f"{m.get('invalid_numbers_pct', 0):.1f}%"),
            "",
            line("Data Quality Score", f"{self.quality_score:.1f}%"),
            line("Status", self.status),
            "-" * width,
        ]
        for dim in self.dimensions.values():
            score = "n/a" if dim.score is None else f"{dim.score:.1f}%"
            rows.append(line(f"  {dim.dimension.title()} (w={dim.weight:g})", score))
        if self.blocking_reasons:
            rows += ["-" * width, "BLOCKING:"] + [f"  • {r}" for r in self.blocking_reasons]
        if self.failed_checks:
            rows += ["-" * width, "TOP ISSUES:"]
            rows += [f"  • [{c.severity.upper()}] {c.message}" for c in self.failed_checks[:8]]
        return "\n".join(rows)
