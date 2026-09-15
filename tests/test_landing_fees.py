from datetime import date

import pytest

from fuelmap import landing_fees, model


def aerodrome(icao="LFCL"):
    return model.Aerodrome(
        icao=icao,
        name="TOULOUSE LASBORDES",
        fuels=frozenset({model.AVGAS_100LL}),
        latitude=43.58,
        longitude=1.49,
        fuel_section="10 - AVT : 100 LL.",
        availability=((model.AVGAS_100LL, model.AVAILABILITY_SELF_SERVICE),),
    )


class TestReadLandingFees:
    def test_reads_a_valid_csv(self, tmp_path):
        path = tmp_path / "landing_fees.csv"
        path.write_text(
            "icao,fee_eur,observed_on,payment,note\n"
            "LFCL,18,2026-08-01,cash,Forfait avion léger\n",
            encoding="utf-8",
        )
        records = landing_fees.read_landing_fees(path)
        assert records == [
            landing_fees.LandingFeeRecord(
                icao="LFCL",
                fee_eur=18.0,
                observed_on=date(2026, 8, 1),
                payment="cash",
                note="Forfait avion léger",
            )
        ]


class TestFeeCategory:
    @pytest.mark.parametrize(
        ("fee_eur", "expected"),
        [
            (0, landing_fees.FEE_CATEGORY_FREE),
            (0.01, landing_fees.FEE_CATEGORY_LT8),
            (7.99, landing_fees.FEE_CATEGORY_LT8),
            (8, landing_fees.FEE_CATEGORY_LT10),
            (9.99, landing_fees.FEE_CATEGORY_LT10),
            (10, landing_fees.FEE_CATEGORY_LT15),
            (14.99, landing_fees.FEE_CATEGORY_LT15),
            (15, landing_fees.FEE_CATEGORY_LT20),
            (19.99, landing_fees.FEE_CATEGORY_LT20),
            (20, landing_fees.FEE_CATEGORY_MORE),
            (120, landing_fees.FEE_CATEGORY_MORE),
        ],
    )
    def test_fee_category_boundaries(self, fee_eur, expected):
        assert landing_fees.fee_category(fee_eur) == expected


class TestValidateLandingFees:
    def test_accepts_a_valid_record(self):
        record = landing_fees.LandingFeeRecord(
            icao="LFCL",
            fee_eur=18.0,
            observed_on=date(2026, 8, 1),
            payment="cash",
        )
        messages = landing_fees.validate_landing_fees(
            [record], [aerodrome()], today=date(2026, 8, 12)
        )
        assert messages == []

    def test_rejects_duplicate_icao(self):
        record = landing_fees.LandingFeeRecord(
            icao="LFCL",
            fee_eur=18.0,
            observed_on=date(2026, 8, 1),
            payment="cash",
        )
        messages = landing_fees.validate_landing_fees([record, record], [aerodrome()])
        assert any("doublon" in message.message for message in messages)
