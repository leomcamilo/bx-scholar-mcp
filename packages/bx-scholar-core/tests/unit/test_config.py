"""Tests for bx_scholar_core.config."""

from __future__ import annotations

from pathlib import Path

import pytest

from bx_scholar_core.config import Settings, find_project_root, load_settings


class TestSettings:
    def test_valid_email(self) -> None:
        s = Settings(polite_email="leo@baxijen.ai")
        assert s.polite_email == "leo@baxijen.ai"

    def test_email_stripped(self) -> None:
        s = Settings(polite_email="  leo@baxijen.ai  ")
        assert s.polite_email == "leo@baxijen.ai"

    def test_rejects_empty_email(self) -> None:
        with pytest.raises(Exception, match="POLITE_EMAIL is required"):
            Settings(polite_email="")

    def test_rejects_no_at_sign(self) -> None:
        with pytest.raises(Exception, match="valid email"):
            Settings(polite_email="notanemail")

    @pytest.mark.parametrize(
        "email",
        [
            "researcher@example.com",
            "test@example.org",
            "noreply@something.com",
            "no-reply@university.edu",
            "user@test.com",
        ],
    )
    def test_rejects_placeholder_emails(self, email: str) -> None:
        with pytest.raises(Exception, match=r"real email|placeholder"):
            Settings(polite_email=email)

    def test_accepts_real_university_email(self) -> None:
        s = Settings(polite_email="jane.doe@mit.edu")
        assert s.polite_email == "jane.doe@mit.edu"

    def test_default_cache_dir(self) -> None:
        s = Settings(polite_email="leo@baxijen.ai")
        assert s.cache_dir is not None
        assert str(s.cache_dir).endswith(".cache/bx-scholar")

    def test_custom_cache_dir(self, tmp_path) -> None:
        s = Settings(polite_email="leo@baxijen.ai", cache_dir=tmp_path)
        assert s.cache_dir == tmp_path

    def test_cache_enabled_by_default(self) -> None:
        s = Settings(polite_email="leo@baxijen.ai")
        assert s.cache_enabled is True

    def test_user_agent(self) -> None:
        s = Settings(polite_email="leo@baxijen.ai")
        assert "leo@baxijen.ai" in s.user_agent
        assert "BX-Scholar" in s.user_agent

    def test_log_level_validation(self) -> None:
        s = Settings(polite_email="leo@baxijen.ai", log_level="debug")
        assert s.log_level == "DEBUG"

    def test_log_level_invalid(self) -> None:
        with pytest.raises(Exception, match="log_level"):
            Settings(polite_email="leo@baxijen.ai", log_level="VERBOSE")

    def test_log_format_validation(self) -> None:
        s = Settings(polite_email="leo@baxijen.ai", log_format="JSON")
        assert s.log_format == "json"

    def test_log_format_invalid(self) -> None:
        with pytest.raises(Exception, match="log_format"):
            Settings(polite_email="leo@baxijen.ai", log_format="yaml")


class TestLoadSettings:
    def test_exits_on_invalid_config(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            load_settings(polite_email="")
        assert exc_info.value.code == 1

    def test_loads_with_overrides(self) -> None:
        s = load_settings(polite_email="leo@baxijen.ai", log_level="DEBUG")
        assert s.polite_email == "leo@baxijen.ai"
        assert s.log_level == "DEBUG"


class TestPathResolution:
    def test_root_found_from_package_subdir_via_dotenv(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("BX_SCHOLAR_HOME", raising=False)
        (tmp_path / ".env").write_text("POLITE_EMAIL=jane.doe@mit.edu\n")
        pkg = tmp_path / "packages" / "bx-scholar-core"
        pkg.mkdir(parents=True)
        assert find_project_root(pkg) == tmp_path.resolve()

    def test_root_found_via_uv_workspace(self, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("BX_SCHOLAR_HOME", raising=False)
        (tmp_path / "pyproject.toml").write_text("[tool.uv.workspace]\nmembers = []\n")
        pkg = tmp_path / "packages" / "x"
        pkg.mkdir(parents=True)
        assert find_project_root(pkg) == tmp_path.resolve()

    def test_bx_scholar_home_wins(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("BX_SCHOLAR_HOME", str(tmp_path))
        assert find_project_root(Path("/")) == tmp_path.resolve()

    def test_load_settings_reads_root_env_and_data_from_subdir(self, tmp_path, monkeypatch) -> None:
        """The README flow: `uv run --directory packages/bx-scholar-core`."""
        for var in ("BX_SCHOLAR_HOME", "POLITE_EMAIL", "BX_SCHOLAR_DATA_DIR", "DATA_DIR"):
            monkeypatch.delenv(var, raising=False)
        (tmp_path / ".env").write_text("POLITE_EMAIL=jane.doe@mit.edu\n")
        pkg = tmp_path / "packages" / "bx-scholar-core"
        pkg.mkdir(parents=True)
        monkeypatch.chdir(pkg)

        s = load_settings()
        assert s.polite_email == "jane.doe@mit.edu"
        assert s.data_dir == (tmp_path / "data").resolve()

    def test_relative_data_dir_resolves_against_root(self, tmp_path) -> None:
        s = Settings(polite_email="leo@baxijen.ai", project_root=tmp_path, data_dir="rankings")
        assert s.data_dir == (tmp_path / "rankings").resolve()

    def test_absolute_data_dir_kept(self, tmp_path) -> None:
        s = Settings(polite_email="leo@baxijen.ai", project_root=Path("/nope"), data_dir=tmp_path)
        assert s.data_dir == tmp_path

    @pytest.mark.parametrize("var", ["BX_SCHOLAR_DATA_DIR", "DATA_DIR"])
    def test_data_dir_env_names(self, var, tmp_path, monkeypatch) -> None:
        monkeypatch.delenv("BX_SCHOLAR_DATA_DIR", raising=False)
        monkeypatch.delenv("DATA_DIR", raising=False)
        monkeypatch.setenv(var, str(tmp_path))
        s = Settings(polite_email="leo@baxijen.ai")
        assert s.data_dir == tmp_path

    def test_prefixed_cache_env_vars(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("BX_SCHOLAR_CACHE_DIR", str(tmp_path))
        monkeypatch.setenv("BX_SCHOLAR_CACHE_ENABLED", "false")
        s = Settings(polite_email="leo@baxijen.ai")
        assert s.cache_dir == tmp_path
        assert s.cache_enabled is False


class TestLoadSettingsOverrideCodexRegression:
    def test_project_root_override_also_picks_its_env(self, tmp_path, monkeypatch) -> None:
        for var in ("BX_SCHOLAR_HOME", "POLITE_EMAIL"):
            monkeypatch.delenv(var, raising=False)
        other = tmp_path / "other-home"
        other.mkdir()
        (other / ".env").write_text("POLITE_EMAIL=jane.doe@mit.edu\n")
        monkeypatch.chdir(tmp_path)  # cwd has no .env

        s = load_settings(project_root=other)

        assert s.polite_email == "jane.doe@mit.edu"
        assert s.data_dir == (other / "data").resolve()
