"""Tests for configuration loading and validation."""

from __future__ import annotations

from datetime import time
from pathlib import Path

import pytest
import yaml

from smartmis.core.config import AppConfig, load_config
from smartmis.core.exceptions import ConfigurationError


def _edit_yaml(path: Path, mutate) -> None:  # type: ignore[no-untyped-def]
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


class TestRepositoryConfig:
    """The shipped configuration files must always load cleanly."""

    def test_loads_all_sections(self, app_config: AppConfig) -> None:
        assert app_config.settings.application.name == "SmartMIS"
        assert app_config.settings.working_hours.start == time(8, 0)
        assert app_config.settings.working_hours.end == time(17, 0)
        assert app_config.settings.working_hours.hours_per_day == 9
        assert app_config.business_rules.margin.minimum_margin_percent == 10
        assert app_config.business_rules.order.high_value_threshold == 100_000

    def test_weekmask_is_monday_first(self, app_config: AppConfig) -> None:
        assert app_config.settings.working_days.weekmask == [True] * 6 + [False]

    def test_sla_lookup_is_case_insensitive(self, app_config: AppConfig) -> None:
        assert app_config.settings.sla.target_for("Critical") == 2
        assert app_config.settings.sla.target_for(" low ") == 24
        assert app_config.settings.sla.target_for("unknown") is None

    def test_dataset_schemas_present(self, app_config: AppConfig) -> None:
        datasets = app_config.validation_rules.datasets
        assert {"sales", "inventory", "orders", "employees", "branches", "holidays"} <= set(
            datasets
        )
        sales = app_config.validation_rules.schema_for("sales")
        assert sales.business_key == ("invoice_no", "sku")
        assert "quantity" in sales.required_columns
        assert sales.columns["quantity"].is_numeric

    def test_kpi_catalogue_is_complete(self, app_config: AppConfig) -> None:
        codes = app_config.kpis.by_code()
        expected = {
            "total_sales",
            "net_sales",
            "sales_growth_pct",
            "avg_order_value",
            "order_count",
            "units_sold",
            "avg_selling_price",
            "stock_quantity",
            "stock_value",
            "stock_days",
            "out_of_stock_pct",
            "slow_moving_items",
            "inventory_turnover",
            "total_cost",
            "gross_profit",
            "gross_margin_pct",
            "avg_margin_pct",
            "total_requests",
            "completed_requests",
            "pending_requests",
            "completion_pct",
            "avg_tat_hours",
            "sla_compliance_pct",
        }
        assert expected <= set(codes)

    def test_unknown_dataset_type_raises_friendly_error(self, app_config: AppConfig) -> None:
        with pytest.raises(ConfigurationError) as excinfo:
            app_config.validation_rules.schema_for("payroll")
        assert "Available types" in excinfo.value.user_message


class TestValidationFailures:
    def test_typo_in_key_is_rejected(self, config_dir: Path, tmp_path: Path) -> None:
        _edit_yaml(
            config_dir / "business_rules.yaml",
            lambda d: d["rules"]["inventory"].update({"stockout_treshold": 1}),
        )
        with pytest.raises(ConfigurationError, match="stockout_treshold"):
            load_config(config_dir, env_file=tmp_path / "none.env")

    def test_end_before_start_rejected(self, config_dir: Path, tmp_path: Path) -> None:
        _edit_yaml(
            config_dir / "settings.yaml",
            lambda d: d["working_hours"].update({"start": "18:00", "end": "09:00"}),
        )
        with pytest.raises(ConfigurationError, match="later than"):
            load_config(config_dir, env_file=tmp_path / "none.env")

    def test_invalid_time_format_rejected(self, config_dir: Path, tmp_path: Path) -> None:
        _edit_yaml(
            config_dir / "settings.yaml", lambda d: d["working_hours"].update({"start": "8am"})
        )
        with pytest.raises(ConfigurationError, match="HH:MM"):
            load_config(config_dir, env_file=tmp_path / "none.env")

    def test_no_working_days_rejected(self, config_dir: Path, tmp_path: Path) -> None:
        _edit_yaml(
            config_dir / "settings.yaml",
            lambda d: d.update({"working_days": dict.fromkeys(d["working_days"], False)}),
        )
        with pytest.raises(ConfigurationError, match="at least one working day"):
            load_config(config_dir, env_file=tmp_path / "none.env")

    def test_quality_weights_must_sum_to_one(self, config_dir: Path, tmp_path: Path) -> None:
        _edit_yaml(
            config_dir / "validation_rules.yaml",
            lambda d: d["quality_score_weights"].update({"completeness": 0.9}),
        )
        with pytest.raises(ConfigurationError, match=r"sum to 1\.0"):
            load_config(config_dir, env_file=tmp_path / "none.env")

    def test_business_key_must_reference_defined_column(
        self, config_dir: Path, tmp_path: Path
    ) -> None:
        _edit_yaml(
            config_dir / "validation_rules.yaml",
            lambda d: d["datasets"]["sales"].update({"business_key": ["invoice_no", "nope"]}),
        )
        with pytest.raises(ConfigurationError, match="business_key"):
            load_config(config_dir, env_file=tmp_path / "none.env")

    def test_duplicate_kpi_codes_rejected(self, config_dir: Path, tmp_path: Path) -> None:
        _edit_yaml(config_dir / "kpi_definitions.yaml", lambda d: d["kpis"].append(d["kpis"][0]))
        with pytest.raises(ConfigurationError, match="duplicate KPI codes"):
            load_config(config_dir, env_file=tmp_path / "none.env")

    def test_malformed_yaml_gives_readable_error(self, config_dir: Path, tmp_path: Path) -> None:
        (config_dir / "settings.yaml").write_text("application: [unclosed\n", encoding="utf-8")
        with pytest.raises(ConfigurationError) as excinfo:
            load_config(config_dir, env_file=tmp_path / "none.env")
        assert "not valid YAML" in excinfo.value.user_message

    def test_missing_file(self, config_dir: Path, tmp_path: Path) -> None:
        (config_dir / "kpi_definitions.yaml").unlink()
        with pytest.raises(ConfigurationError, match="not found"):
            load_config(config_dir, env_file=tmp_path / "none.env")

    def test_missing_directory(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigurationError, match="directory not found"):
            load_config(tmp_path / "does-not-exist")

    def test_empty_optional_file_uses_defaults(self, config_dir: Path, tmp_path: Path) -> None:
        (config_dir / "business_rules.yaml").write_text("", encoding="utf-8")
        config = load_config(config_dir, env_file=tmp_path / "none.env")
        assert config.business_rules.inventory.stockout_threshold == 0


class TestEnvironment:
    def test_defaults_without_env(self, app_config: AppConfig, tmp_path: Path) -> None:
        assert app_config.env.database_url is None
        assert app_config.database_url.startswith("sqlite:///")
        assert app_config.database_url.endswith("data/smartmis.db")
        assert not app_config.email_enabled
        assert not app_config.ai_enabled
        assert not app_config.google_sheets_enabled
        assert app_config.feature_status()["AI Analysis"] == "Disabled"

    def test_env_file_values(self, config_dir: Path, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text(
            "\n".join(
                [
                    "DATABASE_URL=postgresql+psycopg://u:p@db:5432/smartmis",
                    "SMTP_HOST=smtp.example.com",
                    "SMTP_FROM=mis@example.com",
                    "SMTP_PASSWORD=super-secret",
                    "REPORT_RECIPIENTS=a@example.com, b@example.com ,",
                    "AI_API_KEY=sk-test",
                    "log_level=debug",
                ]
            ),
            encoding="utf-8",
        )
        config = load_config(config_dir, env_file=env_file)
        assert config.database_url.startswith("postgresql+psycopg://")
        assert config.email_enabled
        assert config.ai_enabled
        assert config.env.recipients == ["a@example.com", "b@example.com"]
        assert config.env.log_level == "DEBUG"
        assert "super-secret" not in repr(config.env)
        assert config.env.smtp_password is not None
        assert config.env.smtp_password.get_secret_value() == "super-secret"

    def test_blank_values_are_treated_as_unset(self, config_dir: Path, tmp_path: Path) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("SMTP_HOST=\nAI_API_KEY=\nDATABASE_URL=\n", encoding="utf-8")
        config = load_config(config_dir, env_file=env_file)
        assert config.env.smtp_host is None
        assert not config.ai_enabled
        assert config.database_url.startswith("sqlite:///")

    def test_process_env_overrides_file(
        self, config_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("LOG_LEVEL=INFO\n", encoding="utf-8")
        monkeypatch.setenv("LOG_LEVEL", "WARNING")
        assert load_config(config_dir, env_file=env_file).env.log_level == "WARNING"

    def test_feature_flag_can_disable_ai(self, config_dir: Path, tmp_path: Path) -> None:
        _edit_yaml(
            config_dir / "settings.yaml", lambda d: d["features"].update({"ai_analysis": False})
        )
        env_file = tmp_path / ".env"
        env_file.write_text("AI_API_KEY=sk-test\n", encoding="utf-8")
        assert not load_config(config_dir, env_file=env_file).ai_enabled

    def test_google_sheets_requires_existing_credentials(
        self, config_dir: Path, tmp_path: Path
    ) -> None:
        env_file = tmp_path / ".env"
        creds = tmp_path / "sa.json"
        env_file.write_text(f"GOOGLE_SERVICE_ACCOUNT_FILE={creds}\n", encoding="utf-8")
        assert not load_config(config_dir, env_file=env_file).google_sheets_enabled
        creds.write_text("{}", encoding="utf-8")
        assert load_config(config_dir, env_file=env_file).google_sheets_enabled


def test_paths_resolve_against_project_root(app_config: AppConfig, tmp_path: Path) -> None:
    assert app_config.path("reports") == (tmp_path / "reports").resolve()
    with pytest.raises(ConfigurationError):
        app_config.path("nonexistent")


def test_upload_extensions_are_normalised(config_dir: Path, tmp_path: Path) -> None:
    _edit_yaml(
        config_dir / "settings.yaml",
        lambda d: d["upload"].update({"allowed_extensions": ["CSV", ".XLSX"]}),
    )
    config = load_config(config_dir, env_file=tmp_path / "none.env")
    assert config.settings.upload.allowed_extensions == (".csv", ".xlsx")
    assert config.settings.upload.max_file_size_bytes == 200 * 1024 * 1024
