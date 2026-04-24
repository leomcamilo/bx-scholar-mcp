"""Tests for workflow prompt registration."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from bx_scholar_workflow.prompts.loader import PROMPT_REGISTRY, register_all_prompts


class TestPromptRegistration:
    def test_all_prompts_register(self) -> None:
        server = FastMCP("test-workflow")
        register_all_prompts(server)

        prompts = server._prompt_manager.list_prompts()
        prompt_names = {p.name for p in prompts}
        expected = {entry["name"] for entry in PROMPT_REGISTRY}
        assert expected == prompt_names, f"Missing: {expected - prompt_names}"

    def test_prompt_count(self) -> None:
        server = FastMCP("test-workflow")
        register_all_prompts(server)

        prompts = server._prompt_manager.list_prompts()
        assert len(prompts) == 8

    def test_static_prompts_return_content(self) -> None:
        server = FastMCP("test-workflow")
        register_all_prompts(server)

        for prompt in server._prompt_manager.list_prompts():
            if prompt.name not in ("journal_calibrator", "literature_search"):
                # Static prompts have no required arguments
                assert len(prompt.arguments or []) == 0 or all(
                    not arg.required for arg in (prompt.arguments or [])
                )

    def test_journal_calibrator_has_param(self) -> None:
        server = FastMCP("test-workflow")
        register_all_prompts(server)

        prompts = {p.name: p for p in server._prompt_manager.list_prompts()}
        jc = prompts["journal_calibrator"]
        arg_names = [a.name for a in (jc.arguments or [])]
        assert "journal_name" in arg_names

    def test_literature_search_has_param(self) -> None:
        server = FastMCP("test-workflow")
        register_all_prompts(server)

        prompts = {p.name: p for p in server._prompt_manager.list_prompts()}
        ls = prompts["literature_search"]
        arg_names = [a.name for a in (ls.arguments or [])]
        assert "topic" in arg_names

    def test_all_prompts_have_descriptions(self) -> None:
        server = FastMCP("test-workflow")
        register_all_prompts(server)

        for prompt in server._prompt_manager.list_prompts():
            assert prompt.description, f"Prompt {prompt.name} has no description"
