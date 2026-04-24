"""Tests for workflow skill/resource registration."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from bx_scholar_workflow.skills.loader import SKILL_REGISTRY, register_all_skills


class TestSkillRegistration:
    def test_all_skills_register(self) -> None:
        server = FastMCP("test-workflow")
        register_all_skills(server)

        resources = server._resource_manager.list_resources()
        resource_uris = {str(r.uri) for r in resources}
        expected = {f"skills://{entry['name']}" for entry in SKILL_REGISTRY}
        assert expected == resource_uris, f"Missing: {expected - resource_uris}"

    def test_skill_count(self) -> None:
        server = FastMCP("test-workflow")
        register_all_skills(server)

        resources = server._resource_manager.list_resources()
        assert len(resources) == 21

    def test_all_skills_have_descriptions(self) -> None:
        server = FastMCP("test-workflow")
        register_all_skills(server)

        for resource in server._resource_manager.list_resources():
            assert resource.description, f"Skill {resource.uri} has no description"
