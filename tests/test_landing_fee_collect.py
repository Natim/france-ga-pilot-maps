from datetime import date
from pathlib import Path

import pytest

from fuelmap import landing_fee_collect

FIXTURES = Path(__file__).parent / "fixtures" / "landing_fees"


class TestParseEdeisLandingFee:
    def test_parses_ttc_table_for_second_band(self):
        text = (FIXTURES / "dijon-excerpt.txt").read_text(encoding="utf-8")
        parsed = landing_fee_collect.parse_edeis_landing_fee(
            text,
            band=landing_fee_collect.FeeBand.SECOND,
        )
        fee, label, observed_on, confidence = parsed
        assert fee == 14.40
        assert "1t à 2t" in label
        assert observed_on == date(2025, 4, 10)
        assert confidence == "high"

    def test_parses_ht_only_table_with_vat(self):
        text = (FIXTURES / "nimes-excerpt.txt").read_text(encoding="utf-8")
        parsed = landing_fee_collect.parse_edeis_landing_fee(
            text,
            band=landing_fee_collect.FeeBand.SECOND,
        )
        fee, label, observed_on, confidence = parsed
        assert fee == pytest.approx(22.91, abs=0.01)
        assert "> 1t" in label
        assert observed_on == date(2025, 1, 1)
        assert confidence == "high"

    def test_min_band_picks_lowest_ttc(self):
        text = (FIXTURES / "dijon-excerpt.txt").read_text(encoding="utf-8")
        fee, label, _, _ = landing_fee_collect.parse_edeis_landing_fee(
            text,
            band=landing_fee_collect.FeeBand.MIN,
        )
        assert fee == 14.40
        assert label.startswith("min (")

    @pytest.mark.parametrize(
        ("fixture", "fee", "label_part", "observed_on"),
        [
            ("cherbourg-excerpt.txt", 13.20, "0 tonne à 2 t", date(2025, 1, 1)),
            ("calais-excerpt.txt", 15.47, ">1 t à 2 t", date(2026, 1, 1)),
            (
                "reims-excerpt.txt",
                14.25,
                "De > 1t à 2t",
                date(2026, 1, 1),
            ),
            ("lorient-excerpt.txt", 114.00, "forfait < 3 t", date(2025, 8, 1)),
            ("perigueux-excerpt.txt", 12.00, "2t", date(2025, 7, 1)),
        ],
    )
    def test_parses_extended_edeis_layouts(self, fixture, fee, label_part, observed_on):
        text = (FIXTURES / fixture).read_text(encoding="utf-8")
        parsed = landing_fee_collect.parse_edeis_landing_fee(
            text,
            band=landing_fee_collect.FeeBand.SECOND,
            effective_on=observed_on if fixture == "reims-excerpt.txt" else None,
        )
        result_fee, label, result_on, confidence = parsed
        assert result_fee == pytest.approx(fee, abs=0.01)
        assert label_part in label
        assert result_on == observed_on
        assert confidence == "high"


class TestReadPendingSources:
    def test_reads_pending_rows(self, tmp_path):
        path = tmp_path / "pending.csv"
        path.write_text(
            "icao,name,operator,pilot_page,pdf_url,parser,parser_arg,"
            "effective_on,status,notes\n"
            "LFRC,Cherbourg,EDEIS,https://example.test/pilot,,edeis,,,"
            "needs_url,Phase 1\n",
            encoding="utf-8",
        )
        rows = landing_fee_collect.read_pending_sources(path)
        assert rows[0].icao == "LFRC"
        assert rows[0].status == "needs_url"


class TestProbePdfUrl:
    def test_detects_missing_url(self):
        status, message = landing_fee_collect.probe_pdf_url("")
        assert status == "missing"
        assert message


class TestReadSources:
    def test_reads_curated_sources(self, tmp_path):
        path = tmp_path / "sources.csv"
        path.write_text(
            "icao,url,parser,parser_arg,effective_on,payment,operator\n"
            "LFSD,https://example.test/dijon.pdf,edeis,,2025-04-10,other,EDEIS\n",
            encoding="utf-8",
        )
        sources = landing_fee_collect.read_sources(path)
        assert sources == [
            landing_fee_collect.LandingFeeSource(
                icao="LFSD",
                url="https://example.test/dijon.pdf",
                parser="edeis",
                parser_arg="",
                effective_on=date(2025, 4, 10),
                payment="other",
                operator="EDEIS",
            )
        ]


class TestCollectFromSource:
    def test_collects_from_local_pdf(self, tmp_path):
        pdf = tmp_path / "LFSD.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        text = (FIXTURES / "dijon-excerpt.txt").read_text(encoding="utf-8")

        source = landing_fee_collect.LandingFeeSource(
            icao="LFSD",
            url=str(pdf),
            parser="edeis",
            effective_on=date(2025, 4, 10),
            payment="other",
            operator="EDEIS",
        )

        def fake_pdf_to_text(path: Path) -> str:
            assert path == pdf
            return text

        original = landing_fee_collect.pdf_to_text
        landing_fee_collect.pdf_to_text = fake_pdf_to_text
        try:
            result = landing_fee_collect.collect_from_source(
                source,
                cache_dir=tmp_path / "cache",
            )
        finally:
            landing_fee_collect.pdf_to_text = original

        assert isinstance(result, landing_fee_collect.CollectedLandingFee)
        assert result.fee_eur == 14.40
        assert "Import auto" in result.note
        assert "https://" not in result.note or str(pdf) in result.note
