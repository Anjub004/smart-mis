"""Cleaning engine.

Runs the configured operations in a fixed, documented order::

    1. standardise column names      (schema names + aliases, snake_case)
    2. trim whitespace
    3. placeholders → missing        ("N/A", "-", "#REF!" …)
    4. remove exact duplicate rows   → quarantine
    5. standardise categories
    6. convert data types            (unconvertible values → missing, logged)
    7. fill defaults                 (optional columns only)
    8. quarantine unusable rows      (missing required values, invalid values,
                                      out-of-range values, rule violations,
                                      duplicate business keys)

Removed rows are **never discarded**: they are returned in
``CleaningResult.quarantine`` in their original form, with a ``_reason`` column
and ``_source_row`` (1-based row number in the loaded data).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import pandas as pd

from smartmis.cleaning import operations as ops
from smartmis.cleaning.summary import (
    ISSUES,
    REASON,
    SOURCE_ROW,
    ChangeTracker,
    CleaningSummary,
    column_changes,
)
from smartmis.core.config import AppConfig, CleaningConfig, DatasetSchema, ValidationRulesFile
from smartmis.core.exceptions import DataProcessingError, SmartMISError
from smartmis.core.logging import get_logger
from smartmis.core.timing import timed

log = get_logger(__name__)

DUPLICATE_REASON = "exact duplicate of an earlier row"


@dataclass(frozen=True)
class CleaningResult:
    data: pd.DataFrame
    quarantine: pd.DataFrame
    summary: CleaningSummary


class Cleaner:
    """Applies cleaning operations according to ``settings.cleaning``."""

    def __init__(
        self, rules: ValidationRulesFile, settings: CleaningConfig, *, dayfirst: bool = True
    ) -> None:
        self.rules = rules
        self.settings = settings
        self.dayfirst = dayfirst

    @classmethod
    def from_config(cls, config: AppConfig) -> Cleaner:
        return cls(
            config.validation_rules,
            config.settings.cleaning,
            dayfirst=config.settings.validation.dayfirst,
        )

    def clean(self, frame: pd.DataFrame, dataset_type: str) -> CleaningResult:
        """Clean ``frame`` (which is not modified) for the given dataset type.

        Raises:
            ConfigurationError: unknown dataset type.
            DataProcessingError: unexpected failure in a cleaning step.
        """
        schema = self.rules.schema_for(dataset_type)
        try:
            with timed(f"clean:{dataset_type}", log) as timer:
                result = self._run(frame, dataset_type, schema)
        except SmartMISError:
            raise
        except Exception as exc:  # surface as a friendly processing error, keep traceback in log
            log.exception("Cleaning failed for %s", dataset_type)
            raise DataProcessingError(
                f"Cleaning failed for {dataset_type}: {exc}",
                user_message="The data could not be cleaned. See the execution log for details.",
                details={"dataset_type": dataset_type},
            ) from exc
        object.__setattr__(result.summary, "duration_seconds", round(timer.elapsed, 3))
        log.info(
            "Cleaned %s: %d → %d rows (%d quarantined, %d modified)",
            dataset_type,
            result.summary.rows_before,
            result.summary.rows_after,
            result.summary.rows_removed,
            result.summary.rows_modified,
        )
        return result

    # ------------------------------------------------------------------ steps
    def _run(self, frame: pd.DataFrame, dataset_type: str, schema: DatasetSchema) -> CleaningResult:
        cfg = self.settings
        original = frame.reset_index(drop=True).copy()
        original.insert(0, SOURCE_ROW, range(1, len(original) + 1))
        tracker = ChangeTracker(original.index, original[SOURCE_ROW], cfg.change_log_limit)

        work = original
        renamed: dict[str, str] = {}
        if cfg.standardize_column_names:
            work, renamed = ops.standardize_column_names(work, schema, tracker)
        if cfg.trim_whitespace:
            work = ops.trim_whitespace(work, tracker)
        work = ops.replace_placeholders(work, cfg.placeholder_values, tracker)

        removed_parts: list[pd.DataFrame] = []
        if cfg.remove_duplicate_rows:
            work, duplicates = ops.remove_duplicate_rows(work)
            if len(duplicates):
                removed_parts.append(
                    self._quarantine_rows(original, duplicates.index, DUPLICATE_REASON)
                )
            tracker.record_step(
                "Remove duplicate rows",
                f"Moved {len(duplicates):,} exact duplicate row(s) to quarantine.",
                rows=len(duplicates),
            )

        if cfg.standardize_categories:
            work = ops.standardize_categories(work, schema, cfg.category_case, tracker)
        work, invalid_cells = ops.convert_types(work, schema, tracker, dayfirst=self.dayfirst)
        work = ops.fill_defaults(work, schema, tracker)

        reasons = ops.row_problems(
            work,
            schema,
            invalid_cells,
            quarantine_duplicate_keys=cfg.duplicate_keys == "quarantine",
        )
        bad = reasons != ""
        if cfg.invalid_rows == "quarantine":
            if bad.any():
                removed_parts.append(self._quarantine_rows(original, bad[bad].index, reasons[bad]))
            work = work[~bad]
            description = f"Moved {int(bad.sum()):,} unusable row(s) to quarantine."
        else:
            work = work.assign(**{ISSUES: reasons.where(bad, None)})
            description = f"Flagged {int(bad.sum()):,} row(s) in '{ISSUES}' (kept)."
        tracker.record_step("Quarantine unusable rows", description, rows=int(bad.sum()))

        quarantine = (
            pd.concat(removed_parts).sort_values(SOURCE_ROW, ignore_index=True)
            if removed_parts
            else pd.DataFrame(columns=[SOURCE_ROW, *frame.columns, REASON])
        )
        data = work.reset_index(drop=True)
        kept_index = work.index
        rows_modified = int(tracker.modified.loc[kept_index].sum())

        removed_by_reason = self._count_reasons(quarantine)
        summary = CleaningSummary(
            dataset_type=dataset_type,
            rows_before=len(frame),
            rows_after=len(data),
            rows_removed=len(quarantine),
            rows_modified=rows_modified,
            cells_modified=tracker.cells_modified,
            removed_by_reason=removed_by_reason,
            columns_renamed=renamed,
            steps=tuple(tracker.steps),
            column_changes=column_changes(frame, data, renamed),
            change_log=tracker.change_log(),
        )
        return CleaningResult(data=data, quarantine=quarantine, summary=summary)

    @staticmethod
    def _quarantine_rows(
        original: pd.DataFrame, index: pd.Index, reason: pd.Series | str
    ) -> pd.DataFrame:
        rows = original.loc[index].copy()
        rows[REASON] = reason if isinstance(reason, str) else reason.loc[index].to_numpy()
        return rows

    @staticmethod
    def _count_reasons(quarantine: pd.DataFrame) -> dict[str, int]:
        if quarantine.empty:
            return {}
        counter: Counter[str] = Counter()
        for text in quarantine[REASON]:
            for part in str(text).split("; "):
                counter[part] += 1
        return dict(counter.most_common())
