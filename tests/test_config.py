"""Tests for settings validation and environment loading."""

import os

import pytest
from pydantic import ValidationError

from researcher.config import Settings, load_settings


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    """Restore the original environment after every test.

    Replacing the mapping also isolates changes made by load_dotenv.
    """
    monkeypatch.setattr(os, "environ", {})


def test_default_settings():
    settings = load_settings(env_file=None)

    assert settings.log_level == "INFO"
    assert settings.per_source_timeout_seconds == 10.0
    assert settings.retry_max_attempts == 3
    assert settings.cache_ttl_seconds == 86400
    assert settings.database_url is None


def test_settings_parse_environment_values(monkeypatch):
    monkeypatch.setenv("MAX_QUESTION_LENGTH", "1500")
    monkeypatch.setenv("PER_SOURCE_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("RETRY_MAX_ATTEMPTS", "4")

    settings = Settings()

    assert settings.max_question_length == 1500
    assert settings.per_source_timeout_seconds == 2.5
    assert settings.retry_max_attempts == 4


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_question_length", 0),
        ("max_sources_per_query", -1),
        ("per_source_timeout_seconds", 0),
        ("per_source_timeout_seconds", float("inf")),
        ("per_source_timeout_seconds", float("nan")),
        ("retry_max_attempts", 0),
        ("retry_initial_delay_seconds", 0),
        ("retry_max_delay_seconds", -1),
        ("cache_ttl_seconds", 0),
    ],
)
def test_settings_reject_invalid_limits(field, value):
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_settings_reject_invalid_environment_value(monkeypatch):
    monkeypatch.setenv("PER_SOURCE_TIMEOUT_SECONDS", "not-a-number")

    with pytest.raises(ValidationError):
        Settings()


def test_retry_max_delay_cannot_be_less_than_initial_delay():
    with pytest.raises(ValidationError, match="must be at least"):
        Settings(
            retry_initial_delay_seconds=5,
            retry_max_delay_seconds=2,
        )


def test_retry_delays_can_be_equal():
    settings = Settings(
        retry_initial_delay_seconds=2,
        retry_max_delay_seconds=2,
    )

    assert settings.retry_initial_delay_seconds == settings.retry_max_delay_seconds


@pytest.mark.parametrize("value", ["debug", " DEBUG ", "Debug"])
def test_log_level_is_normalized(value):
    assert Settings(log_level=value).log_level == "DEBUG"


def test_unknown_log_level_is_rejected():
    with pytest.raises(ValidationError):
        Settings(log_level="VERBOSE")


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_database_url_becomes_none(value):
    assert Settings(database_url=value).database_url is None


def test_database_url_is_trimmed_and_masked():
    url = "postgresql://user:example-password@localhost/research"
    settings = Settings(database_url=f"  {url}  ")

    assert settings.database_url.get_secret_value() == url
    assert "example-password" not in repr(settings)
    assert "example-password" not in settings.model_dump_json()


def test_unknown_constructor_setting_is_rejected():
    with pytest.raises(ValidationError):
        Settings(retry_max_attemps=3)


def test_settings_are_frozen():
    settings = Settings()

    with pytest.raises(ValidationError):
        settings.log_level = "DEBUG"


def test_dotenv_loads_settings_and_provider_variables(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "MAX_QUESTION_LENGTH=900\n" "LOG_LEVEL=debug\n" "ANTHROPIC_API_KEY=fake-test-key\n",
        encoding="utf-8",
    )

    settings = load_settings(env_file)

    assert settings.max_question_length == 900
    assert settings.log_level == "DEBUG"
    assert os.environ["ANTHROPIC_API_KEY"] == "fake-test-key"


def test_existing_environment_takes_priority_over_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "existing-fake-key")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "LOG_LEVEL=DEBUG\n" "ANTHROPIC_API_KEY=file-fake-key\n",
        encoding="utf-8",
    )

    settings = load_settings(env_file)

    assert settings.log_level == "ERROR"
    assert os.environ["ANTHROPIC_API_KEY"] == "existing-fake-key"


def test_none_skips_dotenv_loading(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LOG_LEVEL=DEBUG\n", encoding="utf-8")

    settings = load_settings(env_file=None)

    assert settings.log_level == "INFO"
    assert "LOG_LEVEL" not in os.environ


def test_default_dotenv_path_uses_current_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LOG_LEVEL=WARNING\n", encoding="utf-8")

    assert load_settings().log_level == "WARNING"


def test_missing_dotenv_file_uses_defaults(tmp_path):
    settings = load_settings(tmp_path / "missing.env")

    assert settings.log_level == "INFO"
