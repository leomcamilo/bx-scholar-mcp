"""Paper deduplication — DOI exact match + title similarity fallback."""

from __future__ import annotations

from rapidfuzz import fuzz

from bx_scholar_core.models.paper import Paper


def deduplicate(papers: list[Paper]) -> list[Paper]:
    """Deduplicate papers by DOI or PMID (exact) then by title similarity + year.

    For papers with the same DOI (or PMID, when there is no DOI): keeps the one
    with more metadata. For papers with neither: matches if title similarity
    >90% AND same year.
    """
    seen_ids: dict[str, Paper] = {}
    no_id: list[Paper] = []
    result: list[Paper] = []

    for paper in papers:
        key = _id_key(paper)
        if key:
            if key in seen_ids:
                existing = seen_ids[key]
                if _metadata_score(paper) > _metadata_score(existing):
                    seen_ids[key] = paper
            else:
                seen_ids[key] = paper
        else:
            no_id.append(paper)

    result.extend(seen_ids.values())

    # Deduplicate id-less papers against the others and each other
    for paper in no_id:
        if not _is_duplicate(paper, result):
            result.append(paper)

    return result


def _id_key(paper: Paper) -> str:
    doi = paper.doi.lower().strip()
    if doi:
        return f"doi:{doi}"
    return f"pmid:{paper.pmid}" if paper.pmid else ""


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
    if paper.mesh or paper.pmid:
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
