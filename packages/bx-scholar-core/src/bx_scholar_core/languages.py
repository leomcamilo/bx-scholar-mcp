"""Language code normalization: sources report ISO 639-2 (3 letters), Paper uses 639-1."""

from __future__ import annotations

_ISO639_2_TO_1 = {
    "eng": "en", "por": "pt", "spa": "es", "fre": "fr", "fra": "fr", "ger": "de", "deu": "de",
    "ita": "it", "chi": "zh", "zho": "zh", "jpn": "ja", "kor": "ko", "rus": "ru", "dut": "nl",
    "nld": "nl", "pol": "pl", "cat": "ca", "glg": "gl", "eus": "eu", "baq": "eu",
}  # fmt: skip


def to_iso639_1(code: str) -> str:
    """'por' -> 'pt'. Two-letter codes pass through; unknown codes are kept as given."""
    code = (code or "").strip().lower()
    return _ISO639_2_TO_1.get(code, code)
