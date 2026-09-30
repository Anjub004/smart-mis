"""Data Quality Score.

Methodology (also documented in ``docs/validation-rules.md``)::

    dimension score  sᵢ = 1 − failedᵢ / checkedᵢ
    DQ Score         = Σ wᵢ·sᵢ / Σ wᵢ × 100      (over dimensions with checkedᵢ > 0)

=============  ======================================  ================================
Dimension      checked                                 failed
=============  ======================================  ================================
Completeness   rows × required columns present         blank required cells
Validity       filled cells in typed/listed columns    unparseable dates/numbers, values
                                                       outside an allowed list
Uniqueness     rows                                    exact duplicates + repeated keys
Consistency    valid values under range rules, rows    out-of-range values, cross-column
               evaluated by cross-column rules         rule violations
Conformity     required columns (+ all columns when    missing required (+ unexpected)
               unexpected columns are not allowed)
=============  ======================================  ================================

Weights come from ``quality_score_weights`` in ``validation_rules.yaml``.
A dimension with nothing to check is left out and the remaining weights are
re-normalised, so a dataset is never rewarded or penalised for a rule that does
not apply to it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from smartmis.core.config import QualityScoreWeights
from smartmis.validation.checks import CheckOutcome, Dimension

DIMENSIONS: tuple[Dimension, ...] = (
    "completeness",
    "validity",
    "uniqueness",
    "consistency",
    "conformity",
)


@dataclass(frozen=True)
class DimensionScore:
    dimension: Dimension
    weight: float
    checked: int
    failed: int

    @property
    def applicable(self) -> bool:
        return self.checked > 0

    @property
    def score(self) -> float | None:
        """0–100, or ``None`` if nothing was checked."""
        if not self.applicable:
            return None
        return round(max(0.0, 1 - self.failed / self.checked) * 100, 2)


def dimension_scores(
    outcomes: Iterable[CheckOutcome], weights: QualityScoreWeights
) -> dict[Dimension, DimensionScore]:
    totals: dict[Dimension, list[int]] = {d: [0, 0] for d in DIMENSIONS}
    for outcome in outcomes:
        totals[outcome.dimension][0] += outcome.checked
        totals[outcome.dimension][1] += outcome.failed
    weight_map = weights.model_dump()
    return {
        d: DimensionScore(
            d, float(weight_map[d]), checked, min(failed, checked) if checked else failed
        )
        for d, (checked, failed) in totals.items()
    }


def quality_score(scores: dict[Dimension, DimensionScore]) -> float:
    """Weighted DQ score in percent, rounded to one decimal."""
    applicable = [s for s in scores.values() if s.applicable and s.weight > 0]
    total_weight = sum(s.weight for s in applicable)
    if not total_weight:
        return 0.0
    weighted = sum(s.weight * (s.score or 0.0) for s in applicable)
    return round(weighted / total_weight, 1)
