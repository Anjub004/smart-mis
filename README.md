# SmartMIS

**SmartMIS — Intelligent MIS Automation & Analytics Platform.** Upload Excel/CSV data, validate and clean it, calculate KPIs and SLA/TAT, detect potential anomalies, and publish formatted MIS reports, all from one configurable pipeline.

> Status: **Phase 4 of 15 complete** (architecture, foundation, data ingestion, sample data, validation and cleaning). The full README ships in Phase 15.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
pytest
```

## Data ingestion

| Source | Class | Notes |
|---|---|---|
| CSV | `CSVDataSource` | Delimiter sniffing (`,` `;` tab `\|`), encoding fallback (UTF-8, UTF-8-BOM, UTF-16, cp1252, latin-1) |
| Excel `.xlsx` | `ExcelDataSource` | Sheet listing and selection, `load_all_sheets()` |
| SQL | `SQLDataSource` | Any SQLAlchemy URL; table or a single read-only `SELECT` |
| Google Sheets | `GoogleSheetsDataSource` | Optional: `pip install -e ".[gsheets]"` + service-account key |

Every load:

- **Checks uploads first:** allowed extension, size limit, content matches extension (a fake `.xlsx` or an executable renamed `.csv` is rejected), and a sanitised file name. Files are parsed, never executed.
- **Detects the header row**, skipping report titles and blank lines above it.
- **Keeps values exactly as loaded.** Type conversion happens later, in validation and cleaning.
- **Profiles every column:** detected type, missing %, unique count and examples, plus row/column counts, file size, SHA-256 hash, timestamp and load time.

```python
from smartmis.core.config import load_config
from smartmis.ingestion import ExcelDataSource

config = load_config()
source = ExcelDataSource.from_path("data/sample/demomart_mis_sample.xlsx",
                                   config.settings.upload, sheet_name="Sales_Extract")
data = source.load()
print(data.profile.columns_frame())
```

New connectors register with `@register_source("name", extensions=(...))`.

## Validation & cleaning

```python
from smartmis.validation import Validator
from smartmis.cleaning import Cleaner

report = Validator.from_config(config).validate(data.dataframe, "sales")
print(report.render_text())      # DQ report: rows, missing %, duplicates, invalid dates/numbers, score
report.raise_if_blocking()       # stops on missing required columns / score below threshold

result = Cleaner.from_config(config).clean(data.dataframe, "sales")
print(result.summary.render_text())   # Before / After / Removed / Modified
result.data          # typed, clean rows
result.quarantine    # removed rows, original values + _reason
result.summary.change_log   # every modified cell: old → new
```

- **Validation** runs 12 configurable checks across five quality dimensions and computes a documented, weighted **Data Quality Score**. The method and a worked example are in [docs/validation-rules.md](docs/validation-rules.md).
- **Cleaning** standardises headers, whitespace, placeholders, category spellings and data types. Rows that can't be fixed safely go to a quarantine table with the reason; nothing is silently deleted.
- On the sample sales data, validation flags 330 invalid rows (score 99.9%). Cleaning quarantines exactly those 330, and the cleaned output re-validates at 100%.
- At 500k rows, validation takes about 2 s and cleaning about 4 s.

## Sample data

Fictional **DemoMart Retail** (5 Mumbai-area branches, 6 categories, 102 SKUs, Jul–Sep 2026). No real personal data.

| File | Rows | Contents |
|---|---|---|
| `sales.csv` | ~31k | Invoice lines: branch, category, SKU, qty, price, discount, cost |
| `inventory.csv` | 6.6k | Weekly stock snapshots per branch × SKU |
| `orders.csv` | 3.2k | Operational requests with priority, timestamps and status (for TAT/SLA) |
| `employees.csv` | 240 | Fictional staff directory |
| `branches.csv`, `holidays.csv` | 5, 6 | Masters |
| `demomart_mis_sample.xlsx` | — | Multi-sheet workbook, including a report-style sheet with title rows |

The data deliberately contains documented quality issues (duplicates, missing values, invalid dates and numbers, negative quantities, inconsistent text) and potential anomalies (bulk purchases, a branch outage day) for the later stages to find.

```bash
python scripts/generate_sample_data.py                 # regenerate data/sample
python scripts/generate_sample_data.py --clean         # without injected issues
python scripts/generate_sample_data.py --scale 16 --no-excel --output data/perf   # ~500k rows
```

On the ~500k-row CSV (42 MB), load plus profiling takes about 3.5 s.

## Documentation

- [Architecture](docs/architecture.md): system design, data flow, database schema, roadmap
- [Validation rules](docs/validation-rules.md): checks, Data Quality Score methodology, cleaning steps

## License

MIT. See [LICENSE](LICENSE).
