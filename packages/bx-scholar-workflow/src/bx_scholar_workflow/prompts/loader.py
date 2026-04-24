"""Load prompt .md files and register them on a FastMCP server."""

from __future__ import annotations

import importlib.resources
from typing import Any

from mcp.server.fastmcp import FastMCP

PROMPT_REGISTRY: list[dict[str, Any]] = [
    {
        "name": "research_pipeline",
        "file": "research_pipeline.md",
        "description": (
            "Complete academic research pipeline - 13 phases from topic to submission."
        ),
    },
    {
        "name": "journal_calibrator",
        "file": "journal_calibrator.md",
        "description": (
            "Build a Journal DNA profile for calibrating your paper to a target journal."
        ),
        "params": [("journal_name", str)],
    },
    {
        "name": "citation_verification",
        "file": "citation_verification.md",
        "description": (
            "Anti-hallucination protocol for verifying all citations before submission."
        ),
    },
    {
        "name": "literature_search",
        "file": "literature_search.md",
        "description": (
            "Systematic literature search protocol with parallel multi-source queries."
        ),
        "params": [("topic", str)],
    },
    {
        "name": "revise_and_resubmit",
        "file": "revise_and_resubmit.md",
        "description": (
            "Full R&R (Revise and Resubmit) protocol: parse comments, define strategy, "
            "generate response letter."
        ),
    },
    {
        "name": "qualitative_analysis",
        "file": "qualitative_analysis.md",
        "description": (
            "Qualitative analysis protocol selection guide: Gioia, Braun & Clarke, "
            "content analysis, process tracing."
        ),
    },
    {
        "name": "theory_development",
        "file": "theory_development.md",
        "description": (
            "Theory building, extension, and integration guide with Whetten's criteria."
        ),
    },
    {
        "name": "meta_analysis_protocol",
        "file": "meta_analysis_protocol.md",
        "description": (
            "Meta-analysis workflow: effect size extraction, forest plots, "
            "heterogeneity, publication bias."
        ),
    },
]


def _load_md(filename: str) -> str:
    ref = importlib.resources.files("bx_scholar_workflow.prompts").joinpath(filename)
    return ref.read_text(encoding="utf-8")


def register_all_prompts(server: FastMCP) -> None:
    """Register all 8 workflow prompts on the server."""
    for entry in PROMPT_REGISTRY:
        template = _load_md(entry["file"])
        name = entry["name"]
        description = entry["description"]
        params = entry.get("params")

        if not params:
            _register_static(server, name, description, template)
        else:
            _register_parameterized(server, name, description, template, params)


def _register_static(server: FastMCP, name: str, description: str, template: str) -> None:
    def make_fn(content: str):
        def prompt_fn() -> str:
            return content

        prompt_fn.__name__ = name
        prompt_fn.__doc__ = description
        return prompt_fn

    fn = make_fn(template)
    server.prompt(name=name, description=description)(fn)


def _register_parameterized(
    server: FastMCP,
    name: str,
    description: str,
    template: str,
    params: list[tuple[str, type]],
) -> None:
    param_name = params[0][0]

    # FastMCP inspects function signatures via inspect.signature(), so we need
    # a real function with the correct parameter name. We use exec to create one.
    namespace: dict[str, Any] = {"template": template}
    exec(
        f"def prompt_fn({param_name}: str) -> str:\n"
        f"    return template.format({param_name}={param_name})\n",
        namespace,
    )
    fn = namespace["prompt_fn"]
    fn.__name__ = name
    fn.__doc__ = description
    server.prompt(name=name, description=description)(fn)
