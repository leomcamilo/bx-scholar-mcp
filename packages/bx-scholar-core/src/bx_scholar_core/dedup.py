"""Paper deduplication — shared DOI/PMID first, title similarity as fallback."""

from __future__ import annotations

from rapidfuzz import fuzz

from bx_scholar_core.models.paper import Paper


def deduplicate(papers: list[Paper]) -> list[Paper]:
    """Deduplicate papers by shared identifiers, then by title similarity + year.

    Records that share any identifier (DOI or PMID) are the same work, even when
    only one copy has the DOI: they are grouped and the one with more metadata is
    kept. Records with no identifier match if title similarity >90% AND same year.
    """
    groups: list[list[Paper]] = []
    group_of: dict[str, int] = {}
    no_id: list[Paper] = []

    for paper in papers:
        keys = _id_keys(paper)
        if not keys:
            no_id.append(paper)
            continue
        hits = sorted({group_of[k] for k in keys if k in group_of})
        if hits:
            target = hits[0]
            for other in hits[1:]:  # this paper bridges groups: merge them
                groups[target].extend(groups[other])
                groups[other] = []
                for k, g in group_of.items():
                    if g == other:
                        group_of[k] = target
        else:
            target = len(groups)
            groups.append([])
        groups[target].append(paper)
        for k in keys:
            group_of[k] = target

    # max() keeps the first of equals, so ties resolve to the earliest record
    result = [max(g, key=_metadata_score) for g in groups if g]

    # Deduplicate id-less papers against the others and each other
    for paper in no_id:
        if not _is_duplicate(paper, result):
            result.append(paper)

    return result


def _id_keys(paper: Paper) -> list[str]:
    keys = []
    if paper.doi.strip():
        keys.append(f"doi:{paper.doi.lower().strip()}")
    if paper.pmid.strip():
        keys.append(f"pmid:{paper.pmid.strip()}")
    return keys


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
