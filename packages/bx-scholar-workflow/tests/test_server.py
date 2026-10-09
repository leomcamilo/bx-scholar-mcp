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


def test_prompts_and_skills_only_call_tools_that_exist(tmp_path, monkeypatch) -> None:
    """The markdown told agents to call search_openalex, get_paper_citations,
    lookup_journal_ranking... long after those tools were renamed. Every
    name written as a call, ``name(``, must be a registered tool."""
    import re
    from pathlib import Path

    monkeypatch.setenv("POLITE_EMAIL", "ci@bxscholar.dev")
    monkeypatch.setenv("BX_SCHOLAR_DATA_DIR", str(tmp_path))
    tools = {t.name for t in create_server()._tool_manager.list_tools()}

    root = Path(__file__).parents[1] / "src" / "bx_scholar_workflow"
    unknown = {
        f"{md.parent.name}/{md.name}: {name}"
        for md in [*root.glob("skills/*.md"), *root.glob("prompts/*.md")]
        for name in re.findall(r"\b([a-z]+(?:_[a-z]+)+)\(", md.read_text())
        if name not in tools
    }
    assert not unknown, sorted(unknown)
