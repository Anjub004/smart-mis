# Validation Rules & Data Quality Score

SmartMIS validates every dataset **before** it is cleaned or used for KPIs. Validation never changes the data. It produces a `ValidationReport` with a score, a valid/invalid row split, and a row-level issue log.

## 1. Where the rules live

| File | What it controls |
|---|---|
| `config/validation_rules.yaml` | Dataset schemas: columns, types, required flags, min/max, allowed values, aliases, defaults, business keys, cross-column rules, and the score weights |
| `config/settings.yaml` → `validation:` | Duplicate checking on/off, missing-value warning threshold, blocking score, date order (`dayfirst`), how many sample rows and issues to keep |

A column definition looks like this:

```yaml
quantity: { type: integer, required: true, min: 0, aliases: [qty] }
priority: { type: category, required: true, allowed: [critical, high, medium, low] }
discount_pct: { type: percent, required: false, min: 0, max: 100, default: 0 }
```

Supported types: `string`, `integer`, `float`, `percent`, `date`, `datetime`, `category`.

**Column matching.** Headers are compared after conversion to `snake_case`, so `"Invoice No."`, `" invoice_no "` and `"InvoiceNo"` all match `invoice_no`. `aliases` add other accepted names (`Qty` → `quantity`).

**Cross-column rules.**

```yaml
consistency_rules:
  - type: not_before          # or not_after
    column: completed_at
    reference: created_at
    description: A request cannot be completed before it was created
```

## 2. Checks

| Check | Dimension | Flags a row as invalid? | Notes |
|---|---|---|---|
| `required_columns` | Conformity | — | **Blocking** if any required column is missing |
| `unexpected_columns` | Conformity | — | Info only unless `allow_unexpected_columns: false` |
| `column_mapping` | — | — | Info: which headers matched by name or alias |
| `empty_rows` | Completeness | yes | Rows with every cell blank |
| `missing_values` | Completeness | yes (required columns) | Optional columns are reported but never invalidate a row |
| `invalid_dates` | Validity | yes | Impossible dates (`2026-02-30`, `31/13/2026`, `TBD`) are invalid, never guessed |
| `invalid_numbers` | Validity | yes | Accepts `1,200.50`, `12%`, `₹ 99`, `(10)` |
| `invalid_categories` | Validity | yes | Case- and space-insensitive against `allowed` |
| `negative_values` / `below_minimum` / `above_maximum` | Consistency | yes | From `min` / `max` |
| `invalid_percentages` | Consistency | yes | `percent` columns outside their range |
| `consistency_rule` | Consistency | yes | From `consistency_rules` |
| `duplicate_rows` | Uniqueness | yes (copies only) | Identical across all columns, ignoring surrounding spaces |
| `duplicate_keys` | Uniqueness | yes (repeats only) | Same business key, different values |

Custom checks subclass `smartmis.validation.checks.BaseCheck` and are passed to `Validator(checks=[...])`.

## 3. Data Quality Score methodology

Each dimension gets a pass rate, and the overall score is their weighted average:

```
dimension score  sᵢ = 1 − failedᵢ / checkedᵢ
DQ Score         = Σ wᵢ·sᵢ / Σ wᵢ × 100        (only dimensions with checkedᵢ > 0)
```

| Dimension | Weight | Checked | Failed |
|---|---|---|---|
| Completeness | 0.30 | rows × required columns present | blank required cells |
| Validity | 0.25 | filled cells in date, number and allowed-list columns | cells that cannot be parsed or are not in the list |
| Uniqueness | 0.20 | rows | exact duplicate copies + repeated business keys |
| Consistency | 0.15 | valid values under a min/max rule + rows evaluated by cross-column rules | out-of-range values + rule violations |
| Conformity | 0.10 | required columns (+ all columns when unexpected columns are not allowed) | missing required (+ unexpected) columns |

Weights are set in `quality_score_weights` and must add up to 1.0. A dimension with nothing to check (for example, no min/max rules) is left out and the other weights are re-normalised, so a dataset is never rewarded or penalised for a rule that does not apply to it.

Dimension scores are rounded to 2 decimals and the overall score to 1 decimal.

### Blocking rules

Processing stops (`report.raise_if_blocking()` raises `DataValidationError`) when:

1. a required column is missing,
2. the dataset has no data rows, or
3. the score is below `validation.blocking_quality_score` (default 50%).

Everything else is reported as a warning. The rows involved are quarantined by the cleaning stage.

## 4. Worked example

The 10-row sales fixture in `tests/unit/test_validation.py`:

| Row | Issue |
|---|---|
| 1–3 | none |
| 4 | exact duplicate of row 3 |
| 5 | invoice date `2026-07-32` |
| 6 | branch blank |
| 7 | quantity `-2` |
| 8 | unit price `abc` |
| 9 | discount `150` % |
| 10 | repeats business key `INV1 + S1` from row 1 with different values |

| Dimension | Checked | Failed | Score |
|---|---|---|---|
| Completeness | 7 required columns × 10 rows = 70 | 1 | 98.57 |
| Validity | 10 dates + 10 qty + 10 prices + 10 discounts = 40 | 2 | 95.00 |
| Uniqueness | 10 | 2 | 80.00 |
| Consistency | 10 qty + 9 parseable prices + 10 discounts = 29 | 2 | 93.10 |
| Conformity | 7 | 0 | 100.00 |

```
0.30 × 98.57 + 0.25 × 95 + 0.20 × 80 + 0.15 × 93.10 + 0.10 × 100
= 29.571 + 23.75 + 16 + 13.965 + 10 = 93.286  →  93.3 %
```

Result: 10 rows, 3 valid, 7 invalid, score **93.3%**, status *PASSED WITH WARNINGS*.

## 5. Cleaning (what happens to invalid rows)

Cleaning (`smartmis.cleaning.Cleaner`, configured under `cleaning:` in `settings.yaml`) runs in this order:

1. Standardise column names (schema names, aliases, then `snake_case` for the rest)
2. Trim whitespace
3. Placeholders → missing (`N/A`, `-`, `#REF!`, …; list is configurable)
4. Remove exact duplicate rows
5. Standardise categories: allowed-list columns map onto the list; other category columns map every variant onto the most common spelling (ties prefer mixed case, e.g. `Powai` over `POWAI`)
6. Convert data types; values that cannot be converted become missing (logged)
7. Fill `default` values in optional columns
8. Quarantine unusable rows (same rules as validation)

Nothing is silently deleted:

- **Removed rows** go to `result.quarantine` in their *original* form, with `_reason` and `_source_row`.
- **Every changed cell** is recorded in `summary.change_log` (row, column, step, old value, new value), up to `change_log_limit` entries.
- **`invalid_rows: keep`** keeps the rows and adds a `_dq_issues` column instead of quarantining them.

After cleaning, re-validating the output scores 100% with 0 invalid rows. The test suite checks this on the sample data.
