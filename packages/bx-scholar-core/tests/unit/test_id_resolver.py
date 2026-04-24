"""Tests for bx_scholar_core.id_resolver."""

from __future__ import annotations

import pytest

from bx_scholar_core.id_resolver import resolve_id


class TestResolveID:
    @pytest.mark.parametrize(
        ("raw", "expected_type", "expected_value"),
        [
            ("10.1234/test", "doi", "10.1234/test"),
            ("https://doi.org/10.1234/test", "doi", "10.1234/test"),
            ("http://doi.org/10.5678/abc", "doi", "10.5678/abc"),
            ("doi:10.9999/xyz", "doi", "10.9999/xyz"),
            ("10.1016/j.cities.2023.104567", "doi", "10.1016/j.cities.2023.104567"),
        ],
    )
    def test_doi(self, raw: str, expected_type: str, expected_value: str) -> None:
        r = resolve_id(raw)
        assert r.id_type == expected_type
        assert r.value == expected_value

    @pytest.mark.parametrize(
        ("raw", "expected_value"),
        [
            ("2401.12345", "2401.12345"),
            ("2401.12345v2", "2401.12345"),
            ("arXiv:2401.12345", "2401.12345"),
            ("arXiv:2401.12345v1", "2401.12345"),
        ],
    )
    def test_arxiv(self, raw: str, expected_value: str) -> None:
        r = resolve_id(raw)
        assert r.id_type == "arxiv"
        assert r.value == expected_value

    @pytest.mark.parametrize(
        ("raw", "expected_value"),
        [
            ("W12345", "W12345"),
            ("w99999", "W99999"),
            ("https://openalex.org/W12345", "W12345"),
        ],
    )
    def test_openalex(self, raw: str, expected_value: str) -> None:
        r = resolve_id(raw)
        assert r.id_type == "openalex"
        assert r.value == expected_value

    def test_semantic_scholar(self) -> None:
        s2_id = "a" * 40
        r = resolve_id(s2_id)
        assert r.id_type == "s2"

    def test_unknown(self) -> None:
        r = resolve_id("some random string")
        assert r.id_type == "unknown"

    def test_preserves_raw(self) -> None:
        r = resolve_id("  https://doi.org/10.1234/test  ")
        assert r.raw == "  https://doi.org/10.1234/test  "
        assert r.value == "10.1234/test"
