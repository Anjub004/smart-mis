# SmartMIS

**SmartMIS — Intelligent MIS Automation & Analytics Platform.** Upload Excel/CSV data, validate and clean it, calculate KPIs and SLA/TAT, detect potential anomalies, and publish formatted MIS reports, all from one configurable pipeline.

> Status: **Phase 3 of 15 complete** (architecture, foundation, data ingestion, sample data). The full README ships in Phase 15.

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

## License

MIT. See [LICENSE](LICENSE).
