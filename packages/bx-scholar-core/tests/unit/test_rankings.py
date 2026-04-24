"""Tests for bx_scholar_core.rankings."""

from __future__ import annotations

from pathlib import Path

from bx_scholar_core.logging import setup_logging
from bx_scholar_core.rankings.jql import load_jql
from bx_scholar_core.rankings.service import RankingService
from bx_scholar_core.rankings.sjr import load_sjr

setup_logging(level="WARNING")

SJR_HEADER = "Title;Issn;SJR;SJR Best Quartile;H index;Country;Areas;Type;Publisher"
SJR_ROW1 = "Academy of Management Journal;00014273, 19487169;15.123;Q1;200;United States;Business;journal;Academy of Management"
SJR_ROW2 = "Journal of Finance;00221082;12.456;Q1;180;United States;Finance;journal;Wiley"

JQL_HEADER = "issn,journal,subject,ft2016,cnrs2020,hceres2021,ajg_abs2024,ejl2024,scopus2024,vhb2024,abdc2025,fnege2025"
JQL_ROW1 = "0001-4273,Academy of Management Journal,Gen Mgmt,Y,1,,4*,,,A+,A*,1"


class TestSJRLoader:
    def test_load(self, tmp_path: Path) -> None:
        sjr_file = tmp_path / "sjr_rankings.csv"
        sjr_file.write_text(f"{SJR_HEADER}\n{SJR_ROW1}\n{SJR_ROW2}\n", encoding="utf-8")

        index, names = load_sjr(sjr_file)
        # 00014273 and 19487169 both mapped
        assert "0001-4273" in index
        assert "1948-7169" in index
        assert "0022-1082" in index
        assert index["0001-4273"].title == "Academy of Management Journal"
        assert index["0001-4273"].quartile == "Q1"
        assert "academy of management journal" in names

    def test_missing_file(self, tmp_path: Path) -> None:
        index, names = load_sjr(tmp_path / "nonexistent.csv")
        assert index == {}
        assert names == {}

    def test_issn_normalization(self, tmp_path: Path) -> None:
        sjr_file = tmp_path / "sjr.csv"
        sjr_file.write_text(f"{SJR_HEADER}\n{SJR_ROW1}\n", encoding="utf-8")
        index, _ = load_sjr(sjr_file)
        # Both ISSNs should have hyphens
        for issn in index:
            assert "-" in issn


class TestJQLLoader:
    def test_load(self, tmp_path: Path) -> None:
        jql_file = tmp_path / "jql_rankings.csv"
        jql_file.write_text(f"{JQL_HEADER}\n{JQL_ROW1}\n", encoding="utf-8")

        index, names = load_jql(jql_file)
        assert "0001-4273" in index
        assert index["0001-4273"].title == "Academy of Management Journal"
        assert index["0001-4273"].abs == "4*"
        assert index["0001-4273"].abdc == "A*"
        assert "academy of management journal" in names

    def test_missing_file(self, tmp_path: Path) -> None:
        index, _names = load_jql(tmp_path / "nonexistent.csv")
        assert index == {}


class TestRankingService:
    def _create_service(self, tmp_path: Path) -> RankingService:
        """Create a service with test data."""
        # SJR
        sjr_file = tmp_path / "sjr_rankings.csv"
        sjr_file.write_text(f"{SJR_HEADER}\n{SJR_ROW1}\n{SJR_ROW2}\n", encoding="utf-8")

        # JQL
        jql_file = tmp_path / "jql_rankings.csv"
        jql_file.write_text(f"{JQL_HEADER}\n{JQL_ROW1}\n", encoding="utf-8")

        svc = RankingService(data_dir=tmp_path)
        svc.load()
        return svc

    def test_lookup_by_issn(self, tmp_path: Path) -> None:
        svc = self._create_service(tmp_path)
        result = svc.lookup("0001-4273")
        assert result.sjr is not None
        assert result.sjr.quartile == "Q1"
        assert result.jql is not None
        assert result.jql.abs == "4*"

    def test_lookup_by_issn_no_hyphen(self, tmp_path: Path) -> None:
        svc = self._create_service(tmp_path)
        result = svc.lookup("00014273")
        assert result.sjr is not None

    def test_lookup_by_name_exact(self, tmp_path: Path) -> None:
        svc = self._create_service(tmp_path)
        result = svc.lookup("Academy of Management Journal")
        assert result.sjr is not None
        assert result.jql is not None

    def test_lookup_by_name_fuzzy(self, tmp_path: Path) -> None:
        svc = self._create_service(tmp_path)
        result = svc.lookup("Acad. of Management Journal")
        # fuzzy match should find it (>85% similarity)
        assert result.sjr is not None or result.jql is not None

    def test_lookup_not_found(self, tmp_path: Path) -> None:
        svc = self._create_service(tmp_path)
        result = svc.lookup("Completely Unknown Journal XYZ")
        assert result.sjr is None
        assert result.jql is None
        assert result.qualis is None

    def test_top_journals(self, tmp_path: Path) -> None:
        svc = self._create_service(tmp_path)
        top = svc.top_journals_for_field("business", limit=10)
        # At least AMJ should be there (area contains "Business")
        assert len(top) >= 1
        assert top[0].sjr is not None

    def test_top_journals_empty_field(self, tmp_path: Path) -> None:
        svc = self._create_service(tmp_path)
        top = svc.top_journals_for_field("quantum physics", limit=10)
        assert top == []
