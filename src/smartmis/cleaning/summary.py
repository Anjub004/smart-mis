"""Change tracking and the cleaning summary.

Every cleaning step reports what it touched through :class:`ChangeTracker`,
which keeps

* per-step counts (rows and cells affected),
* a row-level "modified" flag, and
* a capped, cell-level change log (row, column, old value, new value, step).

This is what lets the UI show *Before / After / Removed / Modified* and lets an
analyst audit every change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

SOURCE_ROW = "_source_row"
REASON = "_reason"
ISSUES = "_dq_issues"

_LOG_COLUMNS = ["source_row", "column", "step", "old_value", "new_value"]


@dataclass(frozen=True)
class CleaningStep:
    step: str
    description: str
    rows_affected: int
    cells_affected: int = 0


class ChangeTracker:
    """Collects the effects of cleaning operations."""

    def __init__(self, index: pd.Index, source_rows: pd.Series, log_limit: int) -> None:
        self.steps: list[CleaningStep] = []
        self.modified = pd.Series(False, index=index)
        self._source_rows = source_rows
        self._log_parts: list[pd.DataFrame] = []
        self._log_size = 0
        self._log_limit = log_limit
        self.cells_modified = 0

    def record_step(self, step: str, description: str, rows: int, cells: int = 0) -> None:
        self.steps.append(CleaningStep(step, description, rows, cells))

    def record_cells(
        self,
        step: str,
        column: str,
        mask: pd.Series,
        old: pd.Series,
        new: pd.Series | Any,
    ) -> int:
        """Register cell changes in ``column`` where ``mask`` is True. Returns the count."""
        mask = mask.fillna(False).astype(bool)
        count = int(mask.sum())
        if not count:
            return 0
        self.cells_modified += count
        self.modified.loc[mask[mask].index] = True
        room = self._log_limit - self._log_size
        if room > 0:
            idx = mask[mask].index[:room]
            new_values = new.loc[idx] if isinstance(new, pd.Series) else [new] * len(idx)
            self._log_parts.append(
                pd.DataFrame(
                    {
                        "source_row": self._source_rows.loc[idx].to_numpy(),
                        "column": column,
                        "step": step,
                        "old_value": pd.Series(old.loc[idx]).astype("string").to_numpy(),
                        "new_value": pd.Series(new_values, index=idx).astype("string").to_numpy(),
                    }
                )
            )
            self._log_size += len(idx)
        return count

    def change_log(self) -> pd.DataFrame:
        if not self._log_parts:
            return pd.DataFrame(columns=_LOG_COLUMNS)
        return pd.concat(self._log_parts, ignore_index=True)


@dataclass(frozen=True)
class CleaningSummary:
    """Before/after view of a cleaning run."""

    dataset_type: str
    rows_before: int
    rows_after: int
    rows_removed: int
    rows_modified: int
    cells_modified: int
    removed_by_reason: dict[str, int]
    columns_renamed: dict[str, str]
    steps: tuple[CleaningStep, ...]
    column_changes: pd.DataFrame = field(repr=False)
    change_log: pd.DataFrame = field(repr=False)
    duration_seconds: float = 0.0

    def steps_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "Step": s.step,
                    "What happened": s.description,
                    "Rows affected": s.rows_affected,
                    "Cells affected": s.cells_affected,
                }
                for s in self.steps
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_type": self.dataset_type,
            "rows_before": self.rows_before,
            "rows_after": self.rows_after,
            "rows_removed": self.rows_removed,
            "rows_modified": self.rows_modified,
            "cells_modified": self.cells_modified,
            "removed_by_reason": dict(self.removed_by_reason),
            "columns_renamed": dict(self.columns_renamed),
            "steps": [s.__dict__ for s in self.steps],
            "duration_seconds": self.duration_seconds,
        }

    def render_text(self, width: int = 44) -> str:
        def line(label: str, value: str) -> str:
            return f"{label + ':':<{width - 12}}{value:>12}"

        rows = [
            f"CLEANING SUMMARY — {self.dataset_type.upper()}",
            "=" * width,
            line("Before Cleaning", f"{self.rows_before:,}"),
            line("After Cleaning", f"{self.rows_after:,}"),
            line("Records Removed", f"{self.rows_removed:,}"),
            line("Records Modified", f"{self.rows_modified:,}"),
            line("Cells Modified", f"{self.cells_modified:,}"),
        ]
        if self.removed_by_reason:
            rows += ["-" * width, "REMOVED (moved to quarantine):"]
            rows += [f"  • {reason}: {count:,}" for reason, count in self.removed_by_reason.items()]
        active = [s for s in self.steps if s.rows_affected or s.cells_affected]
        if active:
            rows += ["-" * width, "STEPS:"]
            rows += [f"  • {s.step}: {s.description}" for s in active]
        return "\n".join(rows)


def column_changes(
    before: pd.DataFrame, after: pd.DataFrame, renamed: dict[str, str]
) -> pd.DataFrame:
    """Per-column dtype and missing-count comparison."""
    reverse = {v: k for k, v in renamed.items()}
    records = []
    for column in after.columns:
        if column in (SOURCE_ROW, ISSUES):
            continue
        original = reverse.get(column, column)
        before_series = before[original] if original in before.columns else None
        records.append(
            {
                "Column": column,
                "Original name": original,
                "Type before": str(before_series.dtype) if before_series is not None else "—",
                "Type after": str(after[column].dtype),
                "Missing before": (
                    int(
                        before_series.isna().sum()
                        + (before_series.astype("string").str.strip() == "").fillna(False).sum()
                    )
                    if before_series is not None
                    else np.nan
                ),
                "Missing after": int(after[column].isna().sum()),
            }
        )
    return pd.DataFrame(records)
