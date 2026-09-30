"""Configuration loading and validation.

SmartMIS reads four YAML files from the ``config/`` directory plus environment
variables (optionally from ``.env``):

=========================  ==================================================
File                       Model
=========================  ==================================================
``settings.yaml``          :class:`SettingsFile`
``business_rules.yaml``    :class:`BusinessRulesFile`
``validation_rules.yaml``  :class:`ValidationRulesFile`
``kpi_definitions.yaml``   :class:`KPIDefinitionsFile`
environment / ``.env``     :class:`EnvSettings` (secrets only)
=========================  ==================================================

All YAML models use ``extra="forbid"`` so a typo such as ``stockout_treshold``
fails fast with a clear message instead of being silently ignored.

Use :func:`get_config` for the cached application-wide configuration and
:func:`load_config` when you need a fresh or custom-location load (tests, CLI).
"""

from __future__ import annotations

import os
from datetime import datetime, time
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, TypeVar

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from smartmis.core.exceptions import ConfigurationError
from smartmis.core.paths import project_root, resolve_path

SETTINGS_FILE = "settings.yaml"
BUSINESS_RULES_FILE = "business_rules.yaml"
VALIDATION_RULES_FILE = "validation_rules.yaml"
KPI_DEFINITIONS_FILE = "kpi_definitions.yaml"

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class _StrictModel(BaseModel):
    """Base for YAML-backed models: unknown keys are errors, instances are immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# =============================================================================
# settings.yaml
# =============================================================================


class ApplicationConfig(_StrictModel):
    name: str = "SmartMIS"
    title: str = "SmartMIS — Intelligent MIS Automation & Analytics Platform"
    company: str = "DemoMart Retail"
    currency_symbol: str = "₹"
    timezone: str = "Asia/Kolkata"
    date_format: str = "%d %b %Y"
    datetime_format: str = "%d %b %Y %H:%M"


class PathsConfig(_StrictModel):
    sample_data: Path = Path("data/sample")
    processed_data: Path = Path("data/processed")
    reports: Path = Path("reports")
    logs: Path = Path("logs")
    database_file: Path = Path("data/smartmis.db")


class UploadConfig(_StrictModel):
    allowed_extensions: tuple[str, ...] = (".csv", ".xlsx")
    max_file_size_mb: float = Field(default=200, gt=0)
    csv_encoding_fallbacks: tuple[str, ...] = ("utf-8", "utf-8-sig", "cp1252", "latin-1")

    @field_validator("allowed_extensions")
    @classmethod
    def _normalise_extensions(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalised = tuple(
            ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in value
        )
        if not normalised:
            raise ValueError("at least one file extension must be allowed")
        return normalised

    @property
    def max_file_size_bytes(self) -> int:
        return int(self.max_file_size_mb * 1024 * 1024)


class LoggingConfig(_StrictModel):
    file_name: str = "smartmis.log"
    max_bytes: int = Field(default=5 * 1024 * 1024, gt=0)
    backup_count: int = Field(default=5, ge=0)
    format: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"


class WorkingHoursConfig(_StrictModel):
    start: time = time(8, 0)
    end: time = time(17, 0)

    @field_validator("start", "end", mode="before")
    @classmethod
    def _parse_hhmm(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return datetime.strptime(value.strip(), "%H:%M").time()
            except ValueError as exc:
                raise ValueError(f"'{value}' is not a valid HH:MM time") from exc
        if isinstance(value, int):
            # YAML 1.1 parses unquoted 08:00 as a sexagesimal integer (480).
            hours, minutes = divmod(value, 60)
            return time(hours, minutes)
        return value

    @model_validator(mode="after")
    def _end_after_start(self) -> WorkingHoursConfig:
        if self.end <= self.start:
            raise ValueError("working_hours.end must be later than working_hours.start")
        return self

    @property
    def hours_per_day(self) -> float:
        start = self.start.hour * 60 + self.start.minute
        end = self.end.hour * 60 + self.end.minute
        return (end - start) / 60


class WorkingDaysConfig(_StrictModel):
    monday: bool = True
    tuesday: bool = True
    wednesday: bool = True
    thursday: bool = True
    friday: bool = True
    saturday: bool = True
    sunday: bool = False

    @model_validator(mode="after")
    def _at_least_one_day(self) -> WorkingDaysConfig:
        if not any(getattr(self, day) for day in WEEKDAYS):
            raise ValueError("at least one working day must be enabled")
        return self

    @property
    def weekmask(self) -> list[bool]:
        """Monday-first mask compatible with ``numpy.busday_count``."""
        return [getattr(self, day) for day in WEEKDAYS]


class SLAConfig(_StrictModel):
    """SLA targets in *working hours* per priority."""

    critical: float = Field(default=2, gt=0)
    high: float = Field(default=4, gt=0)
    medium: float = Field(default=8, gt=0)
    low: float = Field(default=24, gt=0)

    def as_dict(self) -> dict[str, float]:
        return self.model_dump()

    def target_for(self, priority: str) -> float | None:
        return self.as_dict().get(str(priority).strip().lower())


class ValidationSettings(_StrictModel):
    duplicate_check: bool = True
    missing_value_threshold: float = Field(default=5, ge=0, le=100)
    blocking_quality_score: float = Field(default=50, ge=0, le=100)
    sample_issue_rows: int = Field(default=20, ge=0)


AnomalyMethod = Literal["iqr", "zscore", "rolling", "pct_change"]


class AnomalyConfig(_StrictModel):
    method: AnomalyMethod = "iqr"
    iqr_multiplier: float = Field(default=1.5, gt=0)
    zscore_threshold: float = Field(default=3.0, gt=0)
    rolling_window: int = Field(default=7, ge=2)
    rolling_threshold_pct: float = Field(default=50, gt=0)
    pct_change_threshold: float = Field(default=100, gt=0)
    min_observations: int = Field(default=5, ge=2)


class ReportingConfig(_StrictModel):
    top_n: int = Field(default=10, ge=1)
    slow_moving_days: int = Field(default=60, ge=1)
    file_prefix: str = "SmartMIS"
    include_raw_data_sheet: bool = True
    raw_data_row_limit: int = Field(default=100_000, ge=0, le=1_048_575)


class FeaturesConfig(_StrictModel):
    google_sheets: bool = True
    ai_analysis: bool = True
    email: bool = True


class SettingsFile(_StrictModel):
    application: ApplicationConfig = ApplicationConfig()
    paths: PathsConfig = PathsConfig()
    upload: UploadConfig = UploadConfig()
    logging: LoggingConfig = LoggingConfig()
    working_hours: WorkingHoursConfig = WorkingHoursConfig()
    working_days: WorkingDaysConfig = WorkingDaysConfig()
    holidays_file: Path | None = Path("data/sample/holidays.csv")
    sla: SLAConfig = SLAConfig()
    validation: ValidationSettings = ValidationSettings()
    anomaly: AnomalyConfig = AnomalyConfig()
    reporting: ReportingConfig = ReportingConfig()
    features: FeaturesConfig = FeaturesConfig()


# =============================================================================
# business_rules.yaml
# =============================================================================


class SalesRules(_StrictModel):
    negative_sales_allowed: bool = False
    max_discount_percent: float = Field(default=40, ge=0, le=100)
    zero_price_allowed: bool = False


class InventoryRules(_StrictModel):
    stockout_threshold: float = Field(default=0, ge=0)
    low_stock_days: float = Field(default=7, ge=0)
    overstock_days: float = Field(default=120, gt=0)
    slow_moving_days: int = Field(default=60, ge=1)

    @model_validator(mode="after")
    def _low_below_over(self) -> InventoryRules:
        if self.low_stock_days >= self.overstock_days:
            raise ValueError("inventory.low_stock_days must be below inventory.overstock_days")
        return self


class MarginRules(_StrictModel):
    minimum_margin_percent: float = Field(default=10, ge=-100, le=100)
    negative_margin_allowed: bool = False


class OrderRules(_StrictModel):
    high_value_threshold: float = Field(default=100_000, gt=0)


class OperationsRules(_StrictModel):
    pending_age_warning_hours: float = Field(default=24, gt=0)


class BusinessRules(_StrictModel):
    sales: SalesRules = SalesRules()
    inventory: InventoryRules = InventoryRules()
    margin: MarginRules = MarginRules()
    order: OrderRules = OrderRules()
    operations: OperationsRules = OperationsRules()


class BusinessRulesFile(_StrictModel):
    rules: BusinessRules = BusinessRules()


# =============================================================================
# validation_rules.yaml
# =============================================================================

ColumnType = Literal["string", "integer", "float", "date", "datetime", "category", "percent"]


class ColumnRule(_StrictModel):
    type: ColumnType
    required: bool = False
    min: float | None = None
    max: float | None = None
    allowed: tuple[str, ...] | None = None

    @model_validator(mode="after")
    def _min_le_max(self) -> ColumnRule:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"min ({self.min}) is greater than max ({self.max})")
        return self

    @property
    def is_numeric(self) -> bool:
        return self.type in {"integer", "float", "percent"}

    @property
    def is_temporal(self) -> bool:
        return self.type in {"date", "datetime"}


class DatasetSchema(_StrictModel):
    description: str = ""
    business_key: tuple[str, ...] = ()
    allow_unexpected_columns: bool = True
    columns: dict[str, ColumnRule]

    @model_validator(mode="after")
    def _key_columns_exist(self) -> DatasetSchema:
        missing = [col for col in self.business_key if col not in self.columns]
        if missing:
            raise ValueError(f"business_key columns not defined in columns: {missing}")
        return self

    @property
    def required_columns(self) -> list[str]:
        return [name for name, rule in self.columns.items() if rule.required]


class QualityScoreWeights(_StrictModel):
    completeness: float = Field(default=0.30, ge=0)
    validity: float = Field(default=0.25, ge=0)
    uniqueness: float = Field(default=0.20, ge=0)
    consistency: float = Field(default=0.15, ge=0)
    conformity: float = Field(default=0.10, ge=0)

    @model_validator(mode="after")
    def _sum_to_one(self) -> QualityScoreWeights:
        total = sum(self.model_dump().values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"quality_score_weights must sum to 1.0 (currently {total:.3f})")
        return self


class ValidationRulesFile(_StrictModel):
    quality_score_weights: QualityScoreWeights = QualityScoreWeights()
    datasets: dict[str, DatasetSchema] = Field(default_factory=dict)

    def schema_for(self, dataset_type: str) -> DatasetSchema:
        try:
            return self.datasets[dataset_type]
        except KeyError as exc:
            raise ConfigurationError(
                f"No schema defined for dataset type '{dataset_type}'",
                user_message=(
                    f"'{dataset_type}' is not a configured dataset type. "
                    f"Available types: {', '.join(sorted(self.datasets)) or 'none'}."
                ),
            ) from exc


# =============================================================================
# kpi_definitions.yaml
# =============================================================================

KPIUnit = Literal["currency", "pct", "count", "days", "hours", "ratio"]
KPIGroup = Literal["sales", "inventory", "margin", "operations"]


class KPIDefinition(_StrictModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    group: KPIGroup
    name: str
    description: str
    formula: str
    input_columns: tuple[str, ...]
    unit: KPIUnit
    business_meaning: str


class KPIDefinitionsFile(_StrictModel):
    kpis: tuple[KPIDefinition, ...] = ()

    @model_validator(mode="after")
    def _unique_codes(self) -> KPIDefinitionsFile:
        codes = [k.code for k in self.kpis]
        duplicates = sorted({c for c in codes if codes.count(c) > 1})
        if duplicates:
            raise ValueError(f"duplicate KPI codes: {duplicates}")
        return self

    def by_code(self) -> dict[str, KPIDefinition]:
        return {k.code: k for k in self.kpis}


# =============================================================================
# Environment (.env) — secrets and deployment-specific values only
# =============================================================================


class EnvSettings(BaseSettings):
    """Environment variables. Secrets are ``SecretStr`` so they never appear in logs/reprs."""

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    smartmis_env: Literal["development", "production", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    database_url: str | None = None

    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
    smtp_use_tls: bool = True
    report_recipients: str | None = None

    google_service_account_file: Path | None = None

    ai_api_key: SecretStr | None = None
    ai_model: str | None = None

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator(
        "database_url",
        "smtp_host",
        "smtp_username",
        "smtp_from",
        "report_recipients",
        "google_service_account_file",
        "ai_api_key",
        "smtp_password",
        "ai_model",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        """Treat empty strings from ``.env.example`` copies as 'not configured'."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def recipients(self) -> list[str]:
        if not self.report_recipients:
            return []
        return [r.strip() for r in self.report_recipients.split(",") if r.strip()]


# =============================================================================
# Aggregate
# =============================================================================


class AppConfig(BaseModel):
    """Complete, validated SmartMIS configuration."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    config_dir: Path
    settings: SettingsFile
    business_rules: BusinessRules
    validation_rules: ValidationRulesFile
    kpis: KPIDefinitionsFile
    env: EnvSettings

    # --- resolved paths ----------------------------------------------------
    def path(self, name: str) -> Path:
        """Absolute path for a key in ``settings.paths`` (e.g. ``"reports"``)."""
        try:
            relative = getattr(self.settings.paths, name)
        except AttributeError as exc:
            raise ConfigurationError(f"Unknown path setting '{name}'") from exc
        return resolve_path(relative)

    @property
    def holidays_path(self) -> Path | None:
        if self.settings.holidays_file is None:
            return None
        return resolve_path(self.settings.holidays_file)

    # --- derived values ----------------------------------------------------
    @property
    def database_url(self) -> str:
        """``DATABASE_URL`` if set, otherwise the local SQLite file."""
        if self.env.database_url:
            return self.env.database_url
        return f"sqlite:///{self.path('database_file').as_posix()}"

    @property
    def email_enabled(self) -> bool:
        return (
            self.settings.features.email
            and bool(self.env.smtp_host)
            and bool(self.env.smtp_from or self.env.smtp_username)
        )

    @property
    def ai_enabled(self) -> bool:
        return self.settings.features.ai_analysis and self.env.ai_api_key is not None

    @property
    def google_sheets_enabled(self) -> bool:
        credentials = self.env.google_service_account_file
        return (
            self.settings.features.google_sheets
            and credentials is not None
            and resolve_path(credentials).is_file()
        )

    def feature_status(self) -> dict[str, str]:
        """Human-readable status used by the Settings page and CLI."""

        def status(flag: bool) -> str:
            return "Enabled" if flag else "Disabled"

        return {
            "Email": status(self.email_enabled),
            "AI Analysis": status(self.ai_enabled),
            "Google Sheets": status(self.google_sheets_enabled),
            "Database": "External" if self.env.database_url else "Local SQLite",
        }


# =============================================================================
# Loading
# =============================================================================


def _format_validation_error(file_name: str, error: ValidationError) -> str:
    lines = [f"{file_name} has {error.error_count()} problem(s):"]
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "(root)"
        lines.append(f"  • {location}: {item['msg']}")
    return "\n".join(lines)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigurationError(
            f"Configuration file not found: {path}",
            user_message=f"Missing configuration file '{path.name}'.",
            details={"path": str(path)},
        )
    try:
        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigurationError(
            f"Invalid YAML in {path}: {exc}",
            user_message=f"'{path.name}' is not valid YAML. Check indentation and colons.",
            details={"path": str(path)},
        ) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigurationError(
            f"{path} must contain a mapping at the top level",
            user_message=f"'{path.name}' must contain key: value settings at the top level.",
            details={"path": str(path)},
        )
    return data


ModelT = TypeVar("ModelT", bound=BaseModel)


def _build(model: type[ModelT], path: Path) -> ModelT:
    try:
        return model.model_validate(_read_yaml(path))
    except ValidationError as exc:
        message = _format_validation_error(path.name, exc)
        raise ConfigurationError(
            message, user_message=message, details={"path": str(path)}
        ) from exc


def default_config_dir() -> Path:
    env_dir = os.getenv("SMARTMIS_CONFIG_DIR")
    return resolve_path(env_dir) if env_dir else project_root() / "config"


def load_config(
    config_dir: str | Path | None = None,
    *,
    env_file: str | Path | None = None,
) -> AppConfig:
    """Load and validate all configuration.

    Args:
        config_dir: Folder containing the YAML files. Defaults to
            ``$SMARTMIS_CONFIG_DIR`` or ``<project root>/config``.
        env_file: ``.env`` file to read. Defaults to ``<project root>/.env`` when it
            exists. Real environment variables always take precedence.

    Raises:
        ConfigurationError: if any file is missing or invalid.
    """
    directory = resolve_path(config_dir) if config_dir else default_config_dir()
    if not directory.is_dir():
        raise ConfigurationError(
            f"Configuration directory not found: {directory}",
            user_message="The configuration folder could not be found.",
            details={"path": str(directory)},
        )

    env_path = Path(env_file) if env_file else project_root() / ".env"
    try:
        env = EnvSettings(_env_file=env_path if env_path.is_file() else None)  # type: ignore[call-arg]
    except ValidationError as exc:
        message = _format_validation_error("Environment (.env)", exc)
        raise ConfigurationError(message, user_message=message) from exc

    return AppConfig(
        config_dir=directory,
        settings=_build(SettingsFile, directory / SETTINGS_FILE),
        business_rules=_build(BusinessRulesFile, directory / BUSINESS_RULES_FILE).rules,
        validation_rules=_build(ValidationRulesFile, directory / VALIDATION_RULES_FILE),
        kpis=_build(KPIDefinitionsFile, directory / KPI_DEFINITIONS_FILE),
        env=env,
    )


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Return the cached application configuration (call ``reload_config`` after edits)."""
    return load_config()


def reload_config() -> AppConfig:
    """Clear the cache and reload configuration from disk."""
    get_config.cache_clear()
    return get_config()
