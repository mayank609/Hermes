"""
Centralized application configuration.

All runtime configuration is sourced from environment variables (loaded from
a local .env file via python-dotenv). Nothing here should be hardcoded for a
specific deployment - everything that varies between environments (API keys,
group lists, database location, etc.) lives in the environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env once, as early as possible, before any Settings is constructed.
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
LOG_DIR = DATA_DIR / "logs"
EXPORT_DIR = DATA_DIR / "exports"
SUMMARY_DIR = DATA_DIR / "summaries"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be an integer, got {value!r}") from exc


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"Environment variable {name} must be a number, got {value!r}") from exc


def _get_list(name: str, default: list[str] | None = None) -> list[str]:
    value = os.getenv(name)
    if not value or not value.strip():
        return default or []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of application configuration."""

    # --- Telegram ---
    api_id: int
    api_hash: str
    phone_number: str
    session_name: str
    telegram_groups: list[str]

    # --- Gemini ---
    gemini_api_key: str
    gemini_model: str
    gemini_max_retries: int
    gemini_timeout_seconds: float
    gemini_max_concurrent_requests: int
    gemini_temperature: float

    # --- Filtering ---
    keywords: list[str]
    min_message_length: int

    # --- Database ---
    database_url: str

    # --- API server ---
    run_api: bool
    api_host: str
    api_port: int

    # --- Daily summary ---
    daily_summary_enabled: bool
    daily_summary_hour: int

    # --- Logging ---
    log_level: str

    def __post_init__(self) -> None:
        if not self.telegram_groups:
            raise ConfigError(
                "TELEGRAM_GROUPS must contain at least one group username or ID."
            )


def get_settings() -> Settings:
    """Build a Settings instance from the current environment.

    Raises ConfigError with a clear message if required variables are missing.
    """
    required = {
        "API_ID": os.getenv("API_ID"),
        "API_HASH": os.getenv("API_HASH"),
        "PHONE_NUMBER": os.getenv("PHONE_NUMBER"),
        "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY"),
        "TELEGRAM_GROUPS": os.getenv("TELEGRAM_GROUPS"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ConfigError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + ". Copy .env.example to .env and fill in the values."
        )

    try:
        api_id = int(required["API_ID"])
    except ValueError as exc:
        raise ConfigError("API_ID must be an integer.") from exc

    return Settings(
        api_id=api_id,
        api_hash=required["API_HASH"],
        phone_number=required["PHONE_NUMBER"],
        session_name=os.getenv("SESSION_NAME", "telegram_agent_session"),
        telegram_groups=_get_list("TELEGRAM_GROUPS"),
        gemini_api_key=required["GEMINI_API_KEY"],
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        gemini_max_retries=_get_int("GEMINI_MAX_RETRIES", 5),
        gemini_timeout_seconds=_get_float("GEMINI_TIMEOUT_SECONDS", 60.0),
        gemini_max_concurrent_requests=_get_int("GEMINI_MAX_CONCURRENT_REQUESTS", 3),
        gemini_temperature=_get_float("GEMINI_TEMPERATURE", 0.2),
        keywords=_get_list("KEYWORDS"),
        min_message_length=_get_int("MIN_MESSAGE_LENGTH", 8),
        database_url=os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{DATA_DIR / 'telegram_agent.db'}"),
        run_api=_get_bool("RUN_API", True),
        api_host=os.getenv("API_HOST", "0.0.0.0"),
        api_port=_get_int("API_PORT", 8000),
        daily_summary_enabled=_get_bool("DAILY_SUMMARY_ENABLED", True),
        daily_summary_hour=_get_int("DAILY_SUMMARY_HOUR", 23),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
    )


def ensure_data_dirs() -> None:
    """Create the data directories the app writes to, if missing."""
    for directory in (DATA_DIR, LOG_DIR, EXPORT_DIR, SUMMARY_DIR):
        directory.mkdir(parents=True, exist_ok=True)
