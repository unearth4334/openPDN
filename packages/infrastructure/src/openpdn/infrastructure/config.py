"""Typed configuration.

This module is the *only* place in openPDN allowed to read the environment.
Anything else that needs a setting receives it as an argument -- scattered
`os.getenv()` calls are what turn a configurable application into one that can
only be understood by grepping.

Precedence, lowest to highest:

    field defaults  ->  TOML config file  ->  .env file  ->  environment
                    ->  explicit arguments (CLI flags, test overrides)

All environment variables use the `OPENPDN_` prefix. The config file location
is itself an environment variable, `OPENPDN_CONFIG_FILE`.
"""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

ENV_PREFIX: Final = "OPENPDN_"
CONFIG_FILE_ENV_VAR: Final = f"{ENV_PREFIX}CONFIG_FILE"
DEFAULT_CONFIG_FILE: Final = Path("openpdn.toml")

#: `OPENPDN_IMPORTER` value meaning "work it out from the document". Users
#: should not have to name an importer openPDN can identify for itself.
AUTO_DETECT_IMPORTER: Final = "auto"


class LogLevel(StrEnum):
    """Supported logging levels."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class LogFormat(StrEnum):
    """Log rendering styles."""

    #: One JSON object per line, for ingestion.
    JSON = "json"
    #: Human-readable console output, for development.
    TEXT = "text"


class Environment(StrEnum):
    """Deployment environment label, reported by `/api/info`."""

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    """Runtime configuration for every openPDN surface."""

    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    environment: Environment = Environment.DEVELOPMENT

    # Logging -----------------------------------------------------------------
    log_level: LogLevel = LogLevel.INFO
    log_format: LogFormat = LogFormat.TEXT

    # Filesystem --------------------------------------------------------------
    data_dir: Path = Field(
        default=Path(".cache/data"),
        description="Persistent application data: imported boards, studies.",
    )
    cache_dir: Path = Field(
        default=Path(".cache/cache"),
        description="Regenerable artefacts: meshes, assembled matrices, results.",
    )

    # Adapter selection -------------------------------------------------------
    importer: str = Field(
        default=AUTO_DETECT_IMPORTER,
        description=(
            "PCB importer registry key, or 'auto' to detect the format from the document itself."
        ),
    )
    solver: str = Field(
        default="mock",
        description="Default electrical solver registry key.",
    )

    # HTTP API ----------------------------------------------------------------
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, ge=1, le=65535)
    static_dir: Path | None = Field(
        default=None,
        description="Built frontend to serve; None disables static serving.",
    )
    max_upload_bytes: int = Field(
        default=256 * 1024 * 1024,
        gt=0,
        description="Hard limit on uploaded PCB archives (untrusted input).",
    )

    # Development conveniences --------------------------------------------------
    dev_fixture: Path | None = Field(
        default=None,
        description=(
            "Local PCB source the development UI can import with one click. "
            "Only honoured when environment=development; never ships in an image."
        ),
    )

    @field_validator("data_dir", "cache_dir", "static_dir", "dev_fixture")
    @classmethod
    def _expand(cls, value: Path | None) -> Path | None:
        """Expand `~` so a configured home-relative path behaves as written."""
        if value is None:
            return None
        return value.expanduser()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Order the configuration sources; earlier sources win."""
        sources: list[PydanticBaseSettingsSource] = [
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
        ]
        config_file = Path(os.environ.get(CONFIG_FILE_ENV_VAR, DEFAULT_CONFIG_FILE))
        if config_file.is_file():
            sources.append(TomlConfigSettingsSource(settings_cls, toml_file=config_file))
        return tuple(sources)

    def ensure_directories(self) -> None:
        """Create the data and cache directories if they do not exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)


_settings: Settings | None = None


def load_settings(**overrides: Any) -> Settings:
    """Build a fresh `Settings`; `overrides` outrank the environment.

    Used by the CLI to apply explicit flags, and by tests to pin values.
    """
    return Settings(**overrides)


def get_settings() -> Settings:
    """Return the process-wide settings, loading them on first use."""
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def configure_settings(settings: Settings) -> Settings:
    """Install `settings` as the process-wide configuration."""
    global _settings
    _settings = settings
    return _settings


def reset_settings() -> None:
    """Forget the cached settings. Primarily a test hook."""
    global _settings
    _settings = None
