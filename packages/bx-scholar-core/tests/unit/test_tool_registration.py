"""Tests for tool registration — verifies all tools register without errors."""

from __future__ import annotations

from pathlib import Path

from mcp.server.fastmcp import FastMCP

from bx_scholar_core.config import Settings
from bx_scholar_core.rankings.service import RankingService
from bx_scholar_core.tools.registry import register_all_tools


def _make_settings() -> Settings:
    return Settings(polite_email="ci@bxscholar.dev")


def _make_ranking_service(tmp_path: Path) -> RankingService:
    svc = RankingService(data_dir=tmp_path)
    # Don't load — no files needed for registration test
    return svc


class TestToolRegistration:
    def test_all_tools_register(self, tmp_path: Path) -> None:
        server = FastMCP("test-core")
        settings = _make_settings()
        ranking_service = _make_ranking_service(tmp_path)

        register_all_tools(server, settings, ranking_service)

        # Verify expected tools are registered
        tool_names = {t.name for t in server._tool_manager.list_tools()}
        expected = {
            "search_papers",
            "search_journal_papers",
            "get_paper",
            "get_author",
            "get_journal_info",
            "get_citations",
            "get_keyword_trends",
            "rank_journal",
            "top_journals_for_field",
            "verify_citation",
            "check_retraction",
            "batch_verify_references",
            "get_influential_citations",
            "get_citation_context",
            "build_citation_network",
            "find_co_citation_clusters",
            "check_open_access",
            "download_pdf",
            "extract_pdf_text",
        }
        assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"

    def test_tool_count(self, tmp_path: Path) -> None:
        server = FastMCP("test-core")
        settings = _make_settings()
        ranking_service = _make_ranking_service(tmp_path)

        register_all_tools(server, settings, ranking_service)
        tools = server._tool_manager.list_tools()
        assert len(tools) == 19  # current expected count
