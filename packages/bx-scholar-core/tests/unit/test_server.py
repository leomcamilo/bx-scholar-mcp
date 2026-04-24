"""Tests for bx_scholar_core.server — server creation smoke test."""

from __future__ import annotations

from bx_scholar_core.logging import setup_logging
from bx_scholar_core.server import create_server

setup_logging(level="WARNING")


class TestCreateServer:
    def test_server_creates_with_env(self, tmp_path, monkeypatch) -> None:
        """Server should create successfully with valid POLITE_EMAIL."""
        monkeypatch.setenv("POLITE_EMAIL", "ci@bxscholar.dev")
        monkeypatch.setenv("BX_SCHOLAR_DATA_DIR", str(tmp_path))

        server = create_server()
        assert server is not None
        # Verify it has registered tools
        tools = server._tool_manager.list_tools()
        assert len(tools) >= 19

    def test_server_fails_without_email(self, monkeypatch) -> None:
        """Server should exit if POLITE_EMAIL is missing."""
        monkeypatch.delenv("POLITE_EMAIL", raising=False)
        # Also clear any .env that might provide it
        monkeypatch.setenv("POLITE_EMAIL", "")

        import pytest

        with pytest.raises(SystemExit):
            create_server()
