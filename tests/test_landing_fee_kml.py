from datetime import date
from pathlib import Path

from fuelmap import landing_fee_kml, landing_fees

FIXTURE = Path(__file__).parent / "fixtures" / "landing_fees" / "community-map.kml"


class TestParseKmlDescription:
    def test_parses_free(self):
        fee, kind = landing_fee_kml.parse_kml_description("Gratuit - 24/03/2019")
        assert fee == 0.0
        assert kind == "free"

    def test_parses_exact_amount(self):
        fee, kind = landing_fee_kml.parse_kml_description("12,84 € - 18/03/2023")
        assert fee == 12.84
        assert kind == "exact"

    def test_parses_range_midpoint(self):
        fee, kind = landing_fee_kml.parse_kml_description("10 à 15 € - décembre 2011")
        assert fee == 12.5
        assert kind == "range 10.0-15.0"

    def test_skips_unknown_tariff(self):
        fee, kind = landing_fee_kml.parse_kml_description("tarif inconnu")
        assert fee is None
        assert kind == "skip"


class TestParseKmlLandingFees:
    def test_parses_lf_placemarks(self):
        rows = landing_fee_kml.parse_kml_landing_fees(FIXTURE)
        icaos = {row.icao for row in rows}
        assert "LFJR" in icaos
        assert "LFAC" in icaos
        assert len(rows) >= 400

    def test_uses_map_snapshot_date(self):
        rows = landing_fee_kml.parse_kml_landing_fees(FIXTURE)
        assert all(row.observed_on == date(2024, 6, 2) for row in rows)


class TestMergeKmlIntoLandingFees:
    def test_keeps_existing_rows(self):
        existing = [
            landing_fees.LandingFeeRecord(
                icao="LFJR",
                fee_eur=14.37,
                observed_on=date(2026, 1, 1),
                payment="other",
                note="PDF vérifié",
            )
        ]
        imported = landing_fee_kml.parse_kml_landing_fees(FIXTURE)
        merged, added, skipped = landing_fee_kml.merge_kml_into_landing_fees(
            existing,
            imported,
            known_icaos=frozenset({"LFJR", "LFAB"}),
        )
        assert added == 1
        assert skipped == 406
        by_icao = {row.icao: row for row in merged}
        assert by_icao["LFJR"].fee_eur == 14.37
        assert by_icao["LFJR"].note == "PDF vérifié"
        assert by_icao["LFAB"].observed_on == date(2024, 6, 2)
