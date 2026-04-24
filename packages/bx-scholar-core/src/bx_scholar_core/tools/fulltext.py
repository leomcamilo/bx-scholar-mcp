"""Full-text pipeline tools — OA check, download, extract."""

from __future__ import annotations

import json
from pathlib import Path

from bx_scholar_core.clients.unpaywall import UnpaywallClient
from bx_scholar_core.config import Settings
from bx_scholar_core.logging import get_logger

logger = get_logger(__name__)


def register_fulltext_tools(mcp: object, settings: Settings) -> None:
    """Register full-text pipeline tools on the MCP server."""
    from mcp.server.fastmcp import FastMCP

    server: FastMCP = mcp  # type: ignore[assignment]

    @server.tool()
    async def check_open_access(doi: str) -> str:
        """Check if a paper has Open Access full-text available via Unpaywall.
        Returns OA status and PDF URL if available."""
        client = UnpaywallClient(settings.polite_email, settings.user_agent)
        try:
            result = await client.check_oa(doi)
            return json.dumps(result, ensure_ascii=False, indent=2)
        finally:
            await client.close()

    @server.tool()
    async def download_pdf(url: str, save_path: str) -> str:
        """Download a PDF from a URL (typically an OA source) and save locally.
        Creates parent directories if needed."""
        import httpx

        save = Path(save_path).expanduser()
        save.parent.mkdir(parents=True, exist_ok=True)

        async with httpx.AsyncClient(
            timeout=60, follow_redirects=True, headers={"User-Agent": settings.user_agent}
        ) as client:
            try:
                resp = await client.get(url, headers={"Accept": "application/pdf"})
                resp.raise_for_status()

                content_type = resp.headers.get("content-type", "")
                if "pdf" not in content_type and save.suffix != ".pdf":
                    return json.dumps(
                        {
                            "error": f"Response is not a PDF (content-type: {content_type})",
                            "url": url,
                        }
                    )

                save.write_bytes(resp.content)
                size_mb = len(resp.content) / (1024 * 1024)
                return json.dumps(
                    {
                        "saved_to": str(save),
                        "size_mb": round(size_mb, 2),
                        "url": url,
                    }
                )
            except Exception as exc:
                return json.dumps({"error": str(exc), "url": url})

    @server.tool()
    async def extract_pdf_text(pdf_path: str, output_format: str = "markdown") -> str:
        """Extract text from a PDF file as markdown or plain text.
        Uses marker-pdf (ML-powered) for quality extraction with pymupdf fallback.
        output_format: 'markdown' (structured with headers) or 'text' (plain)."""
        path = Path(pdf_path).expanduser()
        if not path.exists():
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
