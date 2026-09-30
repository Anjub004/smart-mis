# SmartMIS

**SmartMIS — Intelligent MIS Automation & Analytics Platform.** Upload Excel/CSV data, validate and clean it, calculate KPIs and SLA/TAT, detect potential anomalies, and publish formatted MIS reports, all from one configurable pipeline.

> Status: **Phase 2 of 15 complete** (architecture + foundation). The full README ships in Phase 15.

## Quick start (foundation)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
pytest
```

## Documentation

- [Architecture](docs/architecture.md): system design, data flow, database schema, roadmap

## License

MIT. See [LICENSE](LICENSE).
