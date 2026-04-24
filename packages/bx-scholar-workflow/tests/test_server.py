"""Tests for bx_scholar_workflow.server — server creation smoke test."""

from __future__ import annotations

import pytest

from bx_scholar_workflow.server import create_server


class TestCreateServer:
    def test_server_creates_with_env(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("POLITE_EMAIL", "ci@bxscholar.dev")
        monkeypatch.setenv("BX_SCHOLAR_DATA_DIR", str(tmp_path))

        server = create_server()
        assert server is not None

        # Verify tools from core
        tools = server._tool_manager.list_tools()
        assert len(tools) >= 19

        # Verify prompts from workflow
        prompts = server._prompt_manager.list_prompts()
        assert len(prompts) == 8

        # Verify skills from workflow
        resources = server._resource_manager.list_resources()
        assert len(resources) == 21

    def test_server_fails_without_email(self, monkeypatch) -> None:
        monkeypatch.delenv("POLITE_EMAIL", raising=False)
        monkeypatch.setenv("POLITE_EMAIL", "")

        with pytest.raises(SystemExit):
            create_server()
