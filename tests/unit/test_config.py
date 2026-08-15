"""Configuration precedence and the single-source-of-environment rule."""

from __future__ import annotations

from pathlib import Path

import pytest

from openpdn.infrastructure.config import (
    AUTO_DETECT_IMPORTER,
    CONFIG_FILE_ENV_VAR,
    LogLevel,
    Settings,
    configure_settings,
    get_settings,
    load_settings,
    reset_settings,
)


@pytest.fixture(autouse=True)
def _clean_settings():
    reset_settings()
    yield
    reset_settings()


class TestPrecedence:
    def test_defaults_apply_with_no_configuration(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("OPENPDN_LOG_LEVEL", raising=False)
        monkeypatch.setenv(CONFIG_FILE_ENV_VAR, "/nonexistent/openpdn.toml")
        assert load_settings().log_level is LogLevel.INFO

    def test_environment_beats_defaults(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("OPENPDN_LOG_LEVEL", "DEBUG")
        assert load_settings().log_level is LogLevel.DEBUG

    def test_explicit_arguments_beat_the_environment(self, monkeypatch: pytest.MonkeyPatch):
        # This is the CLI flag tier of the hierarchy.
        monkeypatch.setenv("OPENPDN_LOG_LEVEL", "DEBUG")
        assert load_settings(log_level="ERROR").log_level is LogLevel.ERROR

    def test_a_config_file_is_read_below_the_environment(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        config_file = tmp_path / "openpdn.toml"
        config_file.write_text('log_level = "WARNING"\nsolver = "from-file"\n')
        monkeypatch.setenv(CONFIG_FILE_ENV_VAR, str(config_file))
        monkeypatch.setenv("OPENPDN_LOG_LEVEL", "DEBUG")

        settings = load_settings()
        assert settings.log_level is LogLevel.DEBUG, "environment must win over the file"
        assert settings.solver == "from-file", "file must win over the default"


class TestSettingsBehaviour:
    def test_settings_are_immutable(self):
        settings = load_settings()
        with pytest.raises(Exception):  # noqa: B017 - pydantic raises ValidationError
            settings.solver = "other"

    def test_home_relative_paths_are_expanded(self):
        settings = load_settings(data_dir="~/openpdn-data")
        assert "~" not in str(settings.data_dir)

    def test_an_out_of_range_port_is_rejected(self):
        with pytest.raises(Exception):  # noqa: B017 - pydantic raises ValidationError
            load_settings(api_port=70000)

    def test_the_importer_defaults_to_detection(self):
        # Naming an importer is an override, not a requirement: openPDN
        # identifies the format from the document.
        assert load_settings().importer == AUTO_DETECT_IMPORTER

    def test_directories_are_created_on_demand(self, tmp_path: Path):
        settings = load_settings(data_dir=tmp_path / "d", cache_dir=tmp_path / "c")
        settings.ensure_directories()
        assert (tmp_path / "d").is_dir()
        assert (tmp_path / "c").is_dir()

    def test_the_process_wide_settings_can_be_pinned(self, tmp_path: Path):
        pinned = Settings(data_dir=tmp_path / "pinned")
        configure_settings(pinned)
        assert get_settings() is pinned


class TestNoScatteredEnvironmentReads:
    def test_only_the_config_module_touches_the_environment(self):
        """`os.environ`/`os.getenv` appear in exactly one place by design.

        Scattered environment reads make a deployment impossible to reason
        about; the exception below is the config module itself.
        """
        repo_root = Path(__file__).resolve().parents[2]
        allowed = {
            Path("packages/infrastructure/src/openpdn/infrastructure/config.py"),
            # Reads a fixed allow-list of variables to build a child process
            # environment; documented in the module.
            Path("packages/infrastructure/src/openpdn/infrastructure/process.py"),
        }
        offenders = []
        for root in ("packages", "apps"):
            for path in (repo_root / root).rglob("*.py"):
                relative = path.relative_to(repo_root)
                if relative in allowed:
                    continue
                text = path.read_text(encoding="utf-8")
                if "os.getenv" in text or "os.environ" in text:
                    offenders.append(str(relative))
        assert not offenders, (
            "Read configuration through openpdn.infrastructure.config, not the "
            f"environment directly: {offenders}"
        )
