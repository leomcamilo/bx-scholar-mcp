"""Paper deduplication — DOI exact match + title similarity fallback."""

from __future__ import annotations

from rapidfuzz import fuzz

from bx_scholar_core.models.paper import Paper


def deduplicate(papers: list[Paper]) -> list[Paper]:
    """Deduplicate papers by DOI (exact) then by title similarity + year.

    For papers with the same DOI: keeps the one with more metadata.
    For papers without DOI: matches if title similarity >90% AND same year.
    """
    seen_dois: dict[str, Paper] = {}
    no_doi: list[Paper] = []
    result: list[Paper] = []

    for paper in papers:
        doi = paper.doi.lower().strip()
        if doi:
            if doi in seen_dois:
                existing = seen_dois[doi]
                if _metadata_score(paper) > _metadata_score(existing):
                    seen_dois[doi] = paper
            else:
                seen_dois[doi] = paper
        else:
            no_doi.append(paper)

    result.extend(seen_dois.values())

    # Deduplicate no-DOI papers against DOI papers and each other
    for paper in no_doi:
        if not _is_duplicate(paper, result):
            result.append(paper)

    return result


def _metadata_score(paper: Paper) -> int:
    """Score how "complete" a paper's metadata is."""
    score = 0
    if paper.doi:
        score += 3
    if paper.abstract:
        score += 2
    if paper.authors:
        score += 1
    if paper.cited_by_count > 0:
        score += 1
    if paper.journal:
        score += 1
    if paper.year:
        score += 1
    return score


def _is_duplicate(paper: Paper, existing: list[Paper]) -> bool:
    """Check if paper is a duplicate of any existing paper."""
    title = paper.title.strip().lower()
    if not title:
        return False

    for other in existing:
        other_title = other.title.strip().lower()
        if not other_title:
            continue

        # Same year required for title-based matching
        if paper.year and other.year and paper.year != other.year:
            continue

        similarity = fuzz.ratio(title, other_title)
        if similarity > 90:
            return True

    return False
