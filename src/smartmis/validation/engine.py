"""Validation engine.

Usage::

    validator = Validator.from_config(config)
    report = validator.validate(loaded.dataframe, "sales")
    print(report.render_text())
    report.raise_if_blocking()

The validator never changes the DataFrame it is given.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

import pandas as pd

from smartmis.core.config import AppConfig, ValidationRulesFile, ValidationSettings
from smartmis.core.logging import get_logger
from smartmis.core.timing import timed
from smartmis.utils.columns import resolve_columns
from smartmis.validation.checks import DEFAULT_CHECKS, BaseCheck, CheckContext, CheckOutcome
from smartmis.validation.report import CheckResult, ValidationReport
from smartmis.validation.scoring import dimension_scores, quality_score

log = get_logger(__name__)


def detect_dataset_type(columns: Iterable[object], rules: ValidationRulesFile) -> str | None:
    """Guess the dataset type from its columns (share of required columns matched).

    Returns ``None`` if no schema matches at least half of its required columns.
    """
    columns = list(columns)
    best: tuple[float, int, str] | None = None
    for name, schema in rules.datasets.items():
        required = schema.required_columns
        if not required:
            continue
        resolution = resolve_columns(columns, schema)
        share = 1 - len(resolution.missing_required) / len(required)
        candidate = (share, len(required), name)
        if best is None or candidate > best:
            best = candidate
    return best[2] if best and best[0] >= 0.5 else None


class Validator:
    """Runs checks against a dataset schema and produces a :class:`ValidationReport`."""

    def __init__(
        self,
        rules: ValidationRulesFile,
        settings: ValidationSettings,
        checks: Sequence[BaseCheck] = DEFAULT_CHECKS,
    ) -> None:
        self.rules = rules
        self.settings = settings
        self.checks = tuple(checks)

    @classmethod
    def from_config(
        cls, config: AppConfig, checks: Sequence[BaseCheck] = DEFAULT_CHECKS
    ) -> Validator:
        return cls(config.validation_rules, config.settings.validation, checks)

    def validate(self, frame: pd.DataFrame, dataset_type: str) -> ValidationReport:
        """Validate ``frame`` against the schema for ``dataset_type``.

        Raises:
            ConfigurationError: if ``dataset_type`` has no schema.
        """
        schema = self.rules.schema_for(dataset_type)
        with timed(f"validate:{dataset_type}", log) as timer:
            resolution = resolve_columns(frame.columns, schema)
            ctx = CheckContext(frame, schema, resolution, self.settings)
            outcomes: list[CheckOutcome] = []
            for check in self.checks:
                outcomes.extend(check.run(ctx))
            report = self._build_report(frame, dataset_type, outcomes, resolution.mapping)
        object.__setattr__(report, "duration_seconds", round(timer.elapsed, 3))
        log.info(
            "Validated %s: %d rows, %d invalid, score %.1f%% (%s)",
            dataset_type,
            report.total_rows,
            report.invalid_rows,
            report.quality_score,
            report.status,
        )
        return report

    # ------------------------------------------------------------------ build
    def _build_report(
        self,
        frame: pd.DataFrame,
        dataset_type: str,
        outcomes: list[CheckOutcome],
        mapping: dict[str, str],
    ) -> ValidationReport:
        total = len(frame)
        invalid_mask = pd.Series(False, index=frame.index)
        for outcome in outcomes:
            if outcome.invalidates_rows and outcome.row_mask is not None and outcome.failed:
                invalid_mask |= outcome.row_mask.reindex(frame.index, fill_value=False)
        invalid_rows = int(invalid_mask.sum())

        scores = dimension_scores(outcomes, self.rules.quality_score_weights)
        score = quality_score(scores)

        blocking = [o.message for o in outcomes if o.blocking]
        if total == 0:
            blocking.append("The dataset has no data rows.")
        elif score < self.settings.blocking_quality_score:
            blocking.append(
                f"Data Quality Score {score:.1f}% is below the minimum of "
                f"{self.settings.blocking_quality_score:g}%."
            )

        return ValidationReport(
            dataset_type=dataset_type,
            total_rows=total,
            valid_rows=total - invalid_rows,
            invalid_rows=invalid_rows,
            quality_score=score,
            dimensions=scores,
            checks=tuple(self._to_result(o) for o in outcomes),
            column_mapping=dict(mapping),
            issues=self._issue_log(frame, outcomes, mapping),
            invalid_row_mask=invalid_mask,
            blocking_reasons=tuple(blocking),
            metrics=self._metrics(outcomes, total),
        )

    def _to_result(self, outcome: CheckOutcome) -> CheckResult:
        sample: tuple[int, ...] = ()
        if outcome.row_mask is not None and outcome.failed:
            positions = outcome.row_mask.to_numpy().nonzero()[0][: self.settings.sample_issue_rows]
            sample = tuple(int(p) + 1 for p in positions)
        return CheckResult(
            name=outcome.name,
            dimension=outcome.dimension,
            severity=outcome.severity,
            message=outcome.message,
            checked=outcome.checked,
            failed=outcome.failed,
            column=outcome.column,
            sample_rows=sample,
            blocking=outcome.blocking,
        )

    def _issue_log(
        self, frame: pd.DataFrame, outcomes: list[CheckOutcome], mapping: dict[str, str]
    ) -> pd.DataFrame:
        """One line per (row, issue), capped at ``issue_log_limit``. Row numbers are 1-based."""
        limit = self.settings.issue_log_limit
        parts: list[pd.DataFrame] = []
        remaining = limit
        for outcome in sorted(outcomes, key=lambda o: o.severity != "error"):
            if remaining <= 0:
                break
            if outcome.row_mask is None or not outcome.failed or outcome.severity == "info":
                continue
            positions = outcome.row_mask.to_numpy().nonzero()[0][:remaining]
            column = outcome.column
            values: Any
            if column and column in mapping:
                values = frame[mapping[column]].iloc[positions].astype("string").to_numpy()
            else:
                values = [None] * len(positions)
            parts.append(
                pd.DataFrame(
                    {
                        "row": positions + 1,
                        "column": column or "(row)",
                        "check": outcome.name,
                        "severity": outcome.severity,
                        "value": values,
                        "message": outcome.message,
                    }
                )
            )
            remaining -= len(positions)
        if not parts:
            return pd.DataFrame(columns=["row", "column", "check", "severity", "value", "message"])
        return pd.concat(parts, ignore_index=True).sort_values(["row", "check"], ignore_index=True)

    @staticmethod
    def _metrics(outcomes: list[CheckOutcome], total: int) -> dict[str, float]:
        def ratio(names: set[str], *, required_only: bool = False) -> float:
            chosen = [
                o for o in outcomes if o.name in names and (not required_only or o.checked > 0)
            ]
            checked = sum(o.checked for o in chosen)
            failed = sum(o.failed for o in chosen)
            return round(failed / checked * 100, 2) if checked else 0.0

        duplicates = sum(
            o.failed for o in outcomes if o.name in {"duplicate_rows", "duplicate_keys"}
        )
        return {
            "missing_values_pct": ratio({"missing_values"}, required_only=True),
            "duplicate_pct": round(duplicates / total * 100, 2) if total else 0.0,
            "invalid_dates_pct": ratio({"invalid_dates"}),
            "invalid_numbers_pct": ratio({"invalid_numbers"}),
            "invalid_categories_pct": ratio({"invalid_categories"}),
        }
