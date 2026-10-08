"""Full-text pipeline tools — OA check, download, extract.

Security note: ``download_pdf`` and ``extract_pdf_text`` take URL/path inputs and
therefore are the highest-risk surface when this server is reachable over HTTP.
They are hardened here against SSRF (no fetching private/internal hosts, redirects
revalidated per hop) and path traversal (all I/O confined to the cache dir). Do
NOT relax these guards without an equivalent network/sandbox control in front.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import socket
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

from bx_scholar_core.clients.core import fulltext_of, parse_work
from bx_scholar_core.clients.europepmc import jats_to_sections
from bx_scholar_core.config import Settings
from bx_scholar_core.id_resolver import resolve_id
from bx_scholar_core.logging import get_logger

if TYPE_CHECKING:
    from bx_scholar_core.clients.pool import ClientPool

logger = get_logger(__name__)

MAX_PDF_BYTES = 50 * 1024 * 1024  # 50 MB hard cap on downloads
MAX_REDIRECTS = 5


def _host_resolves_to_public_ip(host: str) -> bool:
    """True only if *every* address ``host`` resolves to is a public IP.

    SSRF guard: blocks loopback, private, link-local (incl. 169.254.169.254
    cloud metadata), reserved, multicast and unspecified ranges. Fails closed
    on resolution errors.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def _validate_fetch_url(url: str) -> str | None:
    """Return an error message if ``url`` is unsafe to fetch (SSRF), else None."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return f"Only http/https URLs are allowed (got scheme {parsed.scheme!r})"
    host = parsed.hostname
    if not host:
        return "URL has no host"
    if not _host_resolves_to_public_ip(host):
        return f"Refusing to fetch private/internal/unresolvable host: {host}"
    return None


def _safe_pdf_dest(settings: Settings, requested: str) -> Path:
    """Confine downloads to ``<cache_dir>/pdfs/`` using only the basename.

    The directory part of ``requested`` is ignored on purpose so a caller can
    never write outside the cache (path-traversal guard).
    """
    pdfs_dir = (settings.cache_dir / "pdfs").resolve()
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(requested or "").name).lstrip(".")
    if not name:
        name = hashlib.sha256((requested or "").encode()).hexdigest()[:16]
    if not name.endswith(".pdf"):
        name += ".pdf"
    dest = (pdfs_dir / name).resolve()
    if not dest.is_relative_to(pdfs_dir):  # defense in depth
        dest = pdfs_dir / (hashlib.sha256(name.encode()).hexdigest()[:16] + ".pdf")
    return dest


def _sections_response(
    doc: dict[str, Any], wanted: list[str], max_chars: int, ids: dict[str, str]
) -> str:
    """The sections that fit in ``max_chars``, the headings of the rest."""
    chosen = [
        sec
        for sec in doc["sections"]
        if not wanted or any(w in sec["heading"].lower() for w in wanted)
    ]
    kept: list[dict[str, str]] = []
    omitted: list[str] = []
    truncated = ""
    used = 0
    for sec in chosen:
        remaining = max_chars - used
        if len(sec["text"]) <= remaining:
            kept.append(sec)
            used += len(sec["text"])
        elif not kept and remaining > 0:
            # Even the first section obeys the budget: cut it and say so
            kept.append({**sec, "text": sec["text"][:remaining]})
            truncated = sec["heading"]
            used = max_chars
        else:
            omitted.append(sec["heading"])
    return json.dumps(
        {
            "available": True,
            **ids,
            "title": doc["title"],
            "abstract": doc["abstract"],
            "headings": [sec["heading"] for sec in doc["sections"]],
            "sections": kept,
            "truncated_section": truncated,
            "omitted_sections": omitted,
        },
        ensure_ascii=False,
        indent=2,
    )


def register_fulltext_tools(mcp: object, pool: ClientPool) -> None:
    """Register full-text pipeline tools on the MCP server."""
    from mcp.server.fastmcp import FastMCP

    server: FastMCP = mcp  # type: ignore[assignment]
    settings = pool.settings

    async def _core_work(doi: str) -> tuple[dict[str, Any] | None, str]:
        """CORE's record for ``doi`` and an error message (one of them empty)."""
        try:
            return await pool.core.by_doi(doi), ""
        except Exception as exc:
            return None, f"CORE request failed: {type(exc).__name__}: {exc}"

    @server.tool(structured_output=False)
    async def check_open_access(doi: str) -> str:
        """Check if a paper has Open Access full text. Asks Unpaywall first; when
        Unpaywall has no PDF, CORE (repositories worldwide) is tried, and a copy
        found there is reported with pdf_source="core".
        Returns OA status and PDF URL if available."""
        result = await pool.unpaywall.check_oa(doi)
        if result.get("pdf_url"):
            result["pdf_source"] = "unpaywall"
            return json.dumps(result, ensure_ascii=False, indent=2)
        work, error = await _core_work(doi.strip().replace("https://doi.org/", ""))
        if work and work.get("downloadUrl"):
            result["pdf_url"] = work["downloadUrl"]
            result["pdf_source"] = "core"
            result["core_landing_url"] = parse_work(work).landing_url
        elif error:
            result["core_error"] = error
        return json.dumps(result, ensure_ascii=False, indent=2)

    @server.tool(structured_output=False)
    async def get_fulltext(identifier: str, sections: str = "", max_chars: int = 40000) -> str:
        """Read an open-access paper's full text, split by section, without downloading a PDF.
        identifier: DOI, PMCID ('PMC1234567') or PMID ('pmid:31398324').
        sections: optional comma-separated words; keeps only sections whose heading
        contains one of them (e.g. 'method,result,discussion').
        max_chars: cap on the returned section text (default 40000). A section that
        does not fit is listed in omitted_sections (or cut and named in
        truncated_section when it is the first one); the full list of headings is
        always returned so you can ask for the rest.
        Sources: Europe PMC open-access subset (mostly life and health sciences), split
        by section; then CORE (repositories of every field), as one "Full text" section
        and only with CORE_API_KEY set. When neither has the text, says so and gives a
        PDF URL when CORE has one: use download_pdf + extract_pdf_text on it."""
        resolved = resolve_id(identifier)
        if resolved.id_type not in ("pmcid", "doi", "pmid"):
            return json.dumps({"error": f"Use a DOI, PMCID or pmid:<n>, got: {identifier}"})
        pmcid, doi = "", resolved.value if resolved.id_type == "doi" else ""
        errors: dict[str, str] = {}
        xml = None
        try:
            if resolved.id_type == "pmcid":
                pmcid = resolved.value
            else:
                record = await pool.europepmc.lookup(resolved.id_type, resolved.value)
                if record:
                    pmcid, doi = record.pmcid, record.doi or doi
            xml = await pool.europepmc.fulltext_xml(pmcid) if pmcid else None
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        except Exception as exc:
            # A blocked or failing source is an error, not "no full text"
            errors["europepmc"] = f"Europe PMC request failed: {type(exc).__name__}: {exc}"

        wanted = [w.strip().lower() for w in sections.split(",") if w.strip()]
        if xml:
            doc = jats_to_sections(xml)
            return _sections_response(
                doc, wanted, max_chars, {"source": "europepmc", "pmcid": pmcid, "doi": doi}
            )

        work = None
        if doi:
            work, error = await _core_work(doi)
            if error:
                errors["core"] = error
        text = fulltext_of(work) if work else ""
        if work and text:
            paper = parse_work(work)
            doc = {
                "title": paper.title,
                "abstract": paper.abstract,
                "sections": [{"heading": "Full text", "text": text}],
            }
            return _sections_response(
                doc, [], max_chars,
                {"source": "core", "doi": doi, "core_id": paper.external_ids.get("core", "")},
            )  # fmt: skip

        if errors and not work:
            return json.dumps({"error": "; ".join(errors.values()), "identifier": identifier})
        out: dict[str, Any] = {
            "available": False,
            "identifier": identifier,
            "pmcid": pmcid,
            "reason": "No open-access full text in Europe PMC or CORE for this paper.",
            "next_step": "check_open_access, then download_pdf + extract_pdf_text",
        }
        if work and work.get("downloadUrl"):
            out["pdf_url"] = work["downloadUrl"]
            out["next_step"] = "download_pdf with pdf_url, then extract_pdf_text"
            if not pool.core.api_key:
                out["reason"] = (
                    "No full text in Europe PMC. CORE has this paper; its extracted text "
                    "needs CORE_API_KEY (free), and its PDF is in pdf_url."
                )
        if errors:
            out["source_errors"] = errors
        return json.dumps(out, ensure_ascii=False, indent=2)

    @server.tool(structured_output=False)
    async def download_pdf(url: str, save_path: str) -> str:
        """Download a PDF from a public Open-Access URL into the local cache.

        Security: only http/https; private/internal hosts are refused (SSRF
        guard, redirects revalidated per hop); the file is ALWAYS written under
        the cache dir — the directory part of save_path is ignored, only its
        filename is used. Returns the actual saved path (inside the cache)."""
        import httpx

        dest = _safe_pdf_dest(settings, save_path)
        current = url
        try:
            async with httpx.AsyncClient(
                timeout=60,
                follow_redirects=False,  # follow manually so each hop is revalidated
                headers={"User-Agent": settings.user_agent},
            ) as client:
                resp = None
                for _ in range(MAX_REDIRECTS + 1):
                    err = _validate_fetch_url(current)
                    if err:
                        return json.dumps({"error": err, "url": current})
                    r = await client.get(current, headers={"Accept": "application/pdf"})
                    if r.is_redirect and "location" in r.headers:
                        current = urljoin(current, r.headers["location"])
                        continue
                    resp = r
                    break
                if resp is None:
                    return json.dumps({"error": "Too many redirects", "url": url})
                resp.raise_for_status()

                clen = resp.headers.get("content-length")
                if clen and clen.isdigit() and int(clen) > MAX_PDF_BYTES:
                    return json.dumps({"error": f"PDF too large ({clen} bytes)", "url": current})

                content_type = resp.headers.get("content-type", "")
                if "pdf" not in content_type and dest.suffix != ".pdf":
                    return json.dumps(
                        {
                            "error": f"Response is not a PDF (content-type: {content_type})",
                            "url": current,
                        }
                    )

                content = resp.content
                if len(content) > MAX_PDF_BYTES:
                    return json.dumps(
                        {"error": f"PDF too large ({len(content)} bytes)", "url": current}
                    )

                dest.write_bytes(content)
                return json.dumps(
                    {
                        "saved_to": str(dest),
                        "size_mb": round(len(content) / (1024 * 1024), 2),
                        "url": current,
                    }
                )
        except Exception as exc:
            return json.dumps({"error": str(exc), "url": url})

    @server.tool(structured_output=False)
    async def extract_pdf_text(pdf_path: str, output_format: str = "markdown") -> str:
        """Extract text from a PDF file as markdown or plain text.
        Uses marker-pdf (ML-powered) for quality extraction with pymupdf fallback.
        output_format: 'markdown' (structured with headers) or 'text' (plain).

        Security: only files inside the cache directory can be read (path-traversal
        guard) — use download_pdf first, which saves there."""
        cache_root = settings.cache_dir.resolve()
        try:
            path = Path(pdf_path).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            return json.dumps({"error": f"Invalid path: {exc}"})
        if not path.is_relative_to(cache_root):
            return json.dumps(
                {"error": "File must be inside the cache directory", "file": pdf_path}
            )
        if not path.exists() or not path.is_file():
            return json.dumps({"error": f"File not found: {pdf_path}"})

        full_text = ""
        method_used = "unknown"
        num_pages = 0

        # Try marker-pdf first for markdown
        if output_format == "markdown":
            try:
                from marker.config.parser import ConfigParser
                from marker.converters.pdf import PdfConverter

                config = ConfigParser({"output_format": "markdown"})
                converter = PdfConverter(config=config)
                result = converter(str(path))
                full_text = result.markdown
                num_pages = (
                    result.metadata.get("pages", 0)
                    if hasattr(result, "metadata") and isinstance(result.metadata, dict)
                    else 0
                )
                method_used = "marker-pdf"
            except Exception:
                method_used = "pymupdf_fallback"

        # Fallback: pymupdf
        if not full_text:
            try:
                import fitz

                doc = fitz.open(str(path))
                num_pages = len(doc)
                pages = []
                for page in doc:
                    if output_format == "markdown" or method_used == "pymupdf_fallback":
                        blocks = page.get_text("dict")["blocks"]
                        page_text = []
                        for block in blocks:
                            if block["type"] == 0:
                                for line in block.get("lines", []):
                                    spans = line.get("spans", [])
                                    if not spans:
                                        continue
                                    text = "".join(s["text"] for s in spans).strip()
                                    if not text:
                                        continue
                                    max_size = max(s["size"] for s in spans)
                                    is_bold = any(
                                        "bold" in s.get("font", "").lower()
                                        or s.get("flags", 0) & 16
                                        for s in spans
                                    )
                                    if max_size > 14 and is_bold:
                                        page_text.append(f"\n## {text}\n")
                                    elif max_size > 12 and is_bold:
                                        page_text.append(f"\n### {text}\n")
                                    elif is_bold and len(text) < 100:
                                        page_text.append(f"\n**{text}**\n")
                                    else:
                                        page_text.append(text)
                        pages.append("\n".join(page_text))
                    else:
                        pages.append(page.get_text())
                doc.close()
                full_text = "\n\n---\n\n".join(pages)
                if method_used == "unknown":
                    method_used = "pymupdf"
            except Exception as exc:
                return json.dumps({"error": str(exc), "file": str(path)})

        # Truncate if too long
        if len(full_text) > 100000:
            full_text = full_text[:100000] + "\n\n[... TRUNCATED — full text too long.]"

        return json.dumps(
            {
                "file": str(path),
                "pages": num_pages,
                "chars": len(full_text),
                "format": output_format,
                "method": method_used,
                "text": full_text,
            },
            ensure_ascii=False,
        )
