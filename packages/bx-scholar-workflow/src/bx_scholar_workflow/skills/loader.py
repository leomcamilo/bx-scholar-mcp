"""Load skill .md files and register them as MCP resources."""

from __future__ import annotations

import importlib.resources
from typing import Any

from mcp.server.fastmcp import FastMCP

SKILL_REGISTRY: list[dict[str, Any]] = [
    {
        "name": "research-pipeline",
        "file": "research_pipeline.md",
        "description": "Complete academic research pipeline -- orchestrator with 13 phases.",
    },
    {
        "name": "journal-calibrator",
        "file": "journal_calibrator.md",
        "description": "Journal DNA profiling and strategic positioning.",
    },
    {
        "name": "discovery",
        "file": "discovery.md",
        "description": "Research topic discovery and validation.",
    },
    {
        "name": "systematic-search",
        "file": "systematic_search.md",
        "description": "Autonomous multi-source academic search execution.",
    },
    {
        "name": "curation",
        "file": "curation.md",
        "description": "Paper quality curation using real rankings.",
    },
    {
        "name": "paper-reader",
        "file": "paper_reader.md",
        "description": "Paper reading and structured notes.",
    },
    {
        "name": "literature-review",
        "file": "literature_review.md",
        "description": "Argumentative literature review writing.",
    },
    {
        "name": "methodology",
        "file": "methodology.md",
        "description": "Research methodology design.",
    },
    {
        "name": "results-discussion",
        "file": "results_discussion.md",
        "description": "Results and discussion section writing.",
    },
    {
        "name": "academic-writing",
        "file": "academic_writing.md",
        "description": "Academic writing for Introduction, Conclusion, and Abstract.",
    },
    {
        "name": "internal-review",
        "file": "internal_review.md",
        "description": "Adversarial paper review.",
    },
    {
        "name": "revise-resubmit",
        "file": "revise_resubmit.md",
        "description": "R&R response management.",
    },
    {
        "name": "reference-manager",
        "file": "reference_manager.md",
        "description": "Reference verification, BibTeX generation, multi-style formatting.",
    },
    {
        "name": "formatter",
        "file": "formatter.md",
        "description": "Journal submission formatting.",
    },
    {
        "name": "submission",
        "file": "submission.md",
        "description": "Venue selection and submission package preparation.",
    },
    {
        "name": "conclusion",
        "file": "conclusion.md",
        "description": "Research conclusion writing.",
    },
    {
        "name": "prisma",
        "file": "prisma.md",
        "description": "PRISMA 2020 systematic review protocol.",
    },
    {
        "name": "compliance",
        "file": "compliance.md",
        "description": "Research ethics and regulatory compliance.",
    },
    {
        "name": "theory-development",
        "file": "theory_development.md",
        "description": "Theory building, extension, integration.",
    },
    {
        "name": "qualitative-analysis",
        "file": "qualitative_analysis.md",
        "description": "Qualitative analysis methods.",
    },
    {
        "name": "meta-analysis",
        "file": "meta_analysis.md",
        "description": "Quantitative meta-analysis.",
    },
]


def _load_md(filename: str) -> str:
    ref = importlib.resources.files("bx_scholar_workflow.skills").joinpath(filename)
    return ref.read_text(encoding="utf-8")


def register_all_skills(server: FastMCP) -> None:
    """Register all 21 workflow skill resources on the server."""
    for entry in SKILL_REGISTRY:
        uri = f"skills://{entry['name']}"
        content = _load_md(entry["file"])
        func_name = f"skill_{entry['name'].replace('-', '_')}"
        description = entry["description"]

        def make_fn(text: str, fname: str, desc: str):
            def resource_fn() -> str:
                return text

            resource_fn.__name__ = fname
            resource_fn.__doc__ = desc
            return resource_fn

        fn = make_fn(content, func_name, description)
        server.resource(uri, name=func_name, description=description)(fn)
