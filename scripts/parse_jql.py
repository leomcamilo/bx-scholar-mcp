#!/usr/bin/env python3
"""
Parse Harzing's Journal Quality List (JQL) v72 ISSN PDF into CSV.

The JQL PDF (23 pages) contains 842 journals with 14 columns of ranking data.
Pages 9-23 (indices 8-22) contain journal data rows.

Usage:
    python scripts/parse_jql.py [pdf_path] [output_csv]
    python scripts/parse_jql.py /tmp/jql_issn.pdf data/jql_rankings.csv

Column x-coordinate boundaries (determined from PDF span analysis):
    ISSN:        x < 55
    Journal:     55 <= x < 234
    Subject:    234 <= x < 287
    FT2016:     287 <= x < 310
    CNRS2020:   310 <= x < 335
    Den2021:    335 <= x < 358
    HCERES2021: 358 <= x < 385
    META2023:   385 <= x < 410
    AJG_ABS2024:410 <= x < 436
    EJL2024:    436 <= x < 458
    Scopus2024: 458 <= x < 488
    VHB2024:    488 <= x < 516
    ABDC2025:   516 <= x < 542
    FNEGE2025:  542 <= x
"""

import csv
import re
import sys
from pathlib import Path

import fitz  # pymupdf

# Column boundary thresholds (x-coordinate ranges)
COL_BOUNDS = {
    "issn": (0, 55),
    "journal": (55, 234),
    "subject": (234, 287),
    "ft2016": (287, 310),
    "cnrs2020": (310, 335),
    "den2021": (335, 358),
    "hceres2021": (358, 385),
    "meta2023": (385, 410),
    "ajg_abs2024": (410, 436),
    "ejl2024": (436, 458),
    "scopus2024": (458, 488),
    "vhb2024": (488, 516),
    "abdc2025": (516, 542),
    "fnege2025": (542, 999),
}

FIELDNAMES = list(COL_BOUNDS.keys())

ISSN_RE = re.compile(r"^\d{4}-\d{3}[\dX]$")

# Rows starting with these are headers/footers, not data
SKIP_PREFIXES = (
    "ISSN",
    "Journal",
    "Subject",
    "Harzing",
    "Professor",
    "Journal Quality",
    "\u00a9",
    "Page",
)


def _classify_span(x: float) -> str:
    """Map an x-coordinate to a column name."""
    for col, (lo, hi) in COL_BOUNDS.items():
        if lo <= x < hi:
            return col
    return "fnege2025"  # fallback for far-right


def _empty_entry() -> dict:
    return {col: "" for col in FIELDNAMES}


def parse_jql_pdf(pdf_path: str, output_csv: str) -> list[dict]:
    """Parse a JQL ISSN PDF and write results to CSV.

    Returns the list of parsed journal entries.
    """
    doc = fitz.open(pdf_path)
    entries: list[dict] = []
    current_entry: dict | None = None

    for page_idx in range(8, min(23, len(doc))):
        page = doc[page_idx]
        blocks = page.get_text("dict")["blocks"]

        # Collect all text spans with position
        spans_on_page: list[tuple[float, float, str]] = []
        for block in blocks:
            if block["type"] != 0:
                continue
            for line in block.get("lines", []):
                for span in line["spans"]:
                    text = span["text"].strip()
                    if not text:
                        continue
                    x = span["bbox"][0]
                    y = span["bbox"][1]
                    spans_on_page.append((x, y, text))

        # Sort by y (row) then x (column)
        spans_on_page.sort(key=lambda s: (round(s[1], 0), s[0]))

        # Group spans into rows (same row if y within 3px)
        rows: list[list[tuple[float, str]]] = []
        current_row: list[tuple[float, str]] = []
        last_y = -100.0

        for x, y, text in spans_on_page:
            if abs(y - last_y) > 3:
                if current_row:
                    rows.append(current_row)
                current_row = [(x, text)]
                last_y = y
            else:
                current_row.append((x, text))
        if current_row:
            rows.append(current_row)

        # Process rows
        for row in rows:
            first_text = row[0][1] if row else ""

            # Skip header/footer rows
            if any(first_text.startswith(p) for p in SKIP_PREFIXES):
                continue
            # Skip sub-header rows (range descriptors like "1=Y", "1*-4", etc.)
            if first_text in ("1=Y", "1*-4", "highest to", "lowest]", "[Range"):
                continue
            # Skip page number lines
            if re.match(r"^\d+ of \d+$", first_text):
                continue

            # Detect if this row starts a new entry (contains ISSN in first column)
            has_issn = False
            for x, text in row:
                if x < 55 and (ISSN_RE.match(text) or re.match(r"\d{4}-\d{3}[\dX]\s+", text)):
                    has_issn = True
                    break

            if has_issn:
                # Save previous entry
                if current_entry:
                    entries.append(current_entry)
                current_entry = _empty_entry()

                # Map each span to its column
                for x, text in row:
                    col = _classify_span(x)

                    if col == "issn" and ISSN_RE.match(text):
                        current_entry["issn"] = text
                    elif col == "issn":
                        # ISSN merged with journal text
                        m = re.match(r"(\d{4}-\d{3}[\dX])\s+(.*)", text)
                        if m:
                            current_entry["issn"] = m.group(1)
                            current_entry["journal"] = m.group(2)
                        else:
                            current_entry["journal"] += text
                    elif col == "journal":
                        # Journal text might overflow into subject column
                        # Check if text contains subject at the end (after a known pattern)
                        current_entry["journal"] += " " + text if current_entry["journal"] else text
                    elif col == "subject":
                        current_entry["subject"] += " " + text if current_entry["subject"] else text
                    else:
                        # Rating columns - just assign
                        if current_entry[col]:
                            current_entry[col] += " " + text
                        else:
                            current_entry[col] = text

            elif current_entry:
                # Continuation row (multi-line journal name or subject)
                for x, text in row:
                    col = _classify_span(x)
                    if col in ("journal", "issn"):
                        current_entry["journal"] += " " + text
                    elif col == "subject":
                        current_entry["subject"] += " " + text
                    # Rating columns don't span multiple lines

    # Don't forget the last entry
    if current_entry:
        entries.append(current_entry)

    doc.close()

    # Clean up whitespace
    for e in entries:
        for k in e:
            e[k] = e[k].strip()

    # Write CSV
    Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
    with Path(output_csv).open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(entries)

    print(f"Parsed {len(entries)} journals -> {output_csv}", file=sys.stderr)
    return entries


if __name__ == "__main__":
    pdf = sys.argv[1] if len(sys.argv) > 1 else "data/jql_rankings.pdf"
    out = sys.argv[2] if len(sys.argv) > 2 else "data/jql_rankings.csv"
    parse_jql_pdf(pdf, out)
