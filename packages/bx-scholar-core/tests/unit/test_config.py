"""Tests for bx_scholar_core.config."""

from __future__ import annotations

import pytest

from bx_scholar_core.config import Settings, load_settings


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
