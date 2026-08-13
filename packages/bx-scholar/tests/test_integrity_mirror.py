"""Espelho do Retraction Watch — parsing e regra cronológica, sem rede."""

from __future__ import annotations

from bx_scholar.harvest import retraction_watch as rw
from bx_scholar.workflows.integrity import IntegrityStatus, lookup, mirror_is_populated

HEADER = (
    "Record ID,Title,Journal,RetractionDate,RetractionDOI,OriginalPaperDOI,"
    "RetractionNature,Reason\n"
)


def csv_rows(*rows: str) -> str:
    return HEADER + "".join(r if r.endswith("\n") else r + "\n" for r in rows)


class TestParsing:
    def test_maps_the_four_natures(self) -> None:
        text = csv_rows(
            "1,T,J,01/02/2020,10.9/r1,10.1/a,Retraction,+Falsification",
            "2,T,J,01/02/2020,10.9/r2,10.1/b,Expression of concern,+Investigation",
            "3,T,J,01/02/2020,10.9/r3,10.1/c,Correction,+Error in Data",
            "4,T,J,01/02/2020,10.9/r4,10.1/d,Reinstatement,",
        )
        entries, report = rw.parse_csv(text)
        assert entries["10.1/a"]["status"] == "retracted"
        assert entries["10.1/b"]["status"] == "concern"
        assert entries["10.1/c"]["status"] == "corrected"
        assert entries["10.1/d"]["status"] == "reinstated"
        assert report.fetched_rows == 4

    def test_normalizes_doi(self) -> None:
        entries, _ = rw.parse_csv(
            csv_rows("1,T,J,01/02/2020,10.9/r,https://doi.org/10.1/ABC,Retraction,")
        )
        assert "10.1/abc" in entries

    def test_reasons_are_split_and_cleaned(self) -> None:
        entries, _ = rw.parse_csv(
            csv_rows(
                "1,T,J,01/02/2020,10.9/r,10.1/a,Retraction,"
                "+Falsification of Data;+Investigation by Journal"
            )
        )
        assert entries["10.1/a"]["reasons"] == [
            "Falsification of Data",
            "Investigation by Journal",
        ]

    def test_row_without_original_doi_is_skipped_not_crashed(self) -> None:
        entries, report = rw.parse_csv(csv_rows("1,T,J,01/02/2020,10.9/r,,Retraction,"))
        assert entries == {}
        assert report.skipped_no_doi == 1

    def test_unknown_nature_is_skipped(self) -> None:
        _, report = rw.parse_csv(csv_rows("1,T,J,01/02/2020,10.9/r,10.1/a,Something Else,"))
        assert report.skipped_unknown_nature == 1


class TestCurrentState:
    def test_latest_notice_wins(self) -> None:
        # Preocupação em 2019, retratação em 2021 -> o estado atual é retratado.
        entries, _ = rw.parse_csv(
            csv_rows(
                "1,T,J,03/04/2019,10.9/r1,10.1/a,Expression of concern,",
                "2,T,J,05/06/2021,10.9/r2,10.1/a,Retraction,",
            )
        )
        assert entries["10.1/a"]["status"] == "retracted"

    def test_reinstatement_after_retraction_restores(self) -> None:
        entries, _ = rw.parse_csv(
            csv_rows(
                "1,T,J,03/04/2019,10.9/r1,10.1/a,Retraction,",
                "2,T,J,05/06/2022,10.9/r2,10.1/a,Reinstatement,",
            )
        )
        assert entries["10.1/a"]["status"] == "reinstated"

    def test_same_date_favors_the_stricter_state(self) -> None:
        # Errar para o lado cauteloso custa uma sinalização; errar para o outro
        # lado é citar artigo retratado como válido.
        entries, _ = rw.parse_csv(
            csv_rows(
                "1,T,J,01/02/2020,10.9/r1,10.1/a,Correction,",
                "2,T,J,01/02/2020,10.9/r2,10.1/a,Retraction,",
            )
        )
        assert entries["10.1/a"]["status"] == "retracted"


class TestStorage:
    async def test_store_and_lookup_roundtrip(self, store) -> None:
        entries, _ = rw.parse_csv(
            csv_rows("1,T,J,01/02/2020,10.9/r,10.1/a,Retraction,+Falsification")
        )
        assert await rw.store_entries(entries) == 1

        found = await lookup(["https://doi.org/10.1/A"])
        assert found["10.1/a"].status is IntegrityStatus.RETRACTED
        assert found["10.1/a"].blocks_selection
        assert found["10.1/a"].reasons == ["Falsification"]

    async def test_refresh_replaces_instead_of_merging(self, store) -> None:
        # Um aviso retirado do dataset precisa sumir do espelho; merge deixaria
        # o estado antigo grudado para sempre.
        first, _ = rw.parse_csv(
            csv_rows(
                "1,T,J,01/02/2020,10.9/r,10.1/a,Retraction,",
                "2,T,J,01/02/2020,10.9/r,10.1/b,Retraction,",
            )
        )
        await rw.store_entries(first)
        second, _ = rw.parse_csv(csv_rows("1,T,J,01/02/2020,10.9/r,10.1/a,Retraction,"))
        await rw.store_entries(second)

        assert await lookup(["10.1/b"]) == {}
        assert "10.1/a" in await lookup(["10.1/a"])

    async def test_empty_download_does_not_wipe_a_valid_mirror(self, store) -> None:
        # Um HTML de erro parseado como CSV vazio não pode deixar o gate cego.
        entries, _ = rw.parse_csv(csv_rows("1,T,J,01/02/2020,10.9/r,10.1/a,Retraction,"))
        await rw.store_entries(entries)
        assert await rw.store_entries({}) == 0
        assert await mirror_is_populated()

    async def test_mirror_empty_is_distinguishable_from_clean(self, store) -> None:
        assert not await mirror_is_populated()
        assert await lookup(["10.1/a"]) == {}
