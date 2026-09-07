"""Validated application settings and explicit environment loading."""

from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Read application settings from environment variables."""

    model_config = SettingsConfigDict(
        case_sensitive=False,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
    )

    log_level: LogLevel = "INFO"
    max_question_length: int = Field(default=2000, gt=0)
    per_source_timeout_seconds: float = Field(
        default=10.0,
        gt=0,
        allow_inf_nan=False,
    )
    max_sources_per_query: int = Field(default=3, gt=0)

    # Includes the initial attempt.
    retry_max_attempts: int = Field(default=3, ge=1)
    retry_initial_delay_seconds: float = Field(
        default=0.5,
        gt=0,
        allow_inf_nan=False,
    )
    retry_max_delay_seconds: float = Field(
        default=4.0,
        gt=0,
        allow_inf_nan=False,
    )

    cache_ttl_seconds: int = Field(default=86400, gt=0)
    database_url: SecretStr | None = None

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def validate_retry_delays(self) -> "Settings":
        if self.retry_max_delay_seconds < self.retry_initial_delay_seconds:
            raise ValueError(
                "retry_max_delay_seconds must be at least " "retry_initial_delay_seconds."
            )
        return self


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    """Load configuration once at application startup.

    Existing environment variables take precedence over the file.
    Pass None to skip loading a file.
    """
    if env_file is not None:
        load_dotenv(
            dotenv_path=env_file,
            override=False,
            encoding="utf-8",
        )

    return Settings()
