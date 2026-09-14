from datetime import date

import pytest

from fuelmap import model, prices


def aerodrome(icao="LFCL", fuels=None):
    return model.Aerodrome(
        icao=icao,
        name="TOULOUSE LASBORDES",
        fuels=frozenset(fuels or {model.UL91, model.AVGAS_100LL}),
        latitude=43.58,
        longitude=1.49,
        fuel_section="10 - AVT : 100 LL, UL91.",
        availability=(
            (model.UL91, model.AVAILABILITY_SELF_SERVICE),
            (model.AVGAS_100LL, model.AVAILABILITY_SELF_SERVICE),
        ),
    )


class TestReadPrices:
    def test_reads_a_valid_csv(self, tmp_path):
        path = tmp_path / "prices.csv"
        path.write_text(
            "icao,fuel,price_eur,observed_on,payment,note\n"
            "LFCL,UL91,2.15,2026-08-01,total,Automate\n",
            encoding="utf-8",
        )
        records = prices.read_prices(path)
        assert records == [
            prices.PriceRecord(
                icao="LFCL",
                fuel="UL91",
                price_eur=2.15,
                observed_on=date(2026, 8, 1),
                payment="total",
                note="Automate",
            )
        ]

    def test_rejects_an_invalid_header(self, tmp_path):
        path = tmp_path / "prices.csv"
        path.write_text("icao,fuel\n", encoding="utf-8")
        with pytest.raises(ValueError, match="en-tête CSV"):
            prices.read_prices(path)


class TestValidatePrices:
    def test_accepts_a_valid_record(self):
        record = prices.PriceRecord(
            icao="LFCL",
            fuel=model.UL91,
            price_eur=2.15,
            observed_on=date(2026, 8, 1),
            payment="total",
        )
        messages = prices.validate_prices(
            [record], [aerodrome()], today=date(2026, 8, 12)
        )
        assert messages == []

    def test_rejects_unknown_icao(self):
        record = prices.PriceRecord(
            icao="ZZZZ",
            fuel=model.UL91,
            price_eur=2.15,
            observed_on=date(2026, 8, 1),
            payment="total",
        )
        messages = prices.validate_prices([record], [aerodrome()])
        assert any(message.level == "error" for message in messages)

    def test_warns_when_fuel_is_absent_from_the_vac(self):
        record = prices.PriceRecord(
            icao="LFCL",
            fuel=model.AKI93,
            price_eur=2.15,
            observed_on=date(2026, 8, 1),
            payment="total",
        )
        messages = prices.validate_prices([record], [aerodrome()])
        assert messages == [
            prices.ValidationMessage(
                "warning",
                "ligne 2 (LFCL/AKI93/total) : carburant absent de la VAC "
                "pour ce terrain",
            )
        ]

    def test_accepts_off_aip_icao(self):
        record = prices.PriceRecord(
            icao="LF4724",
            fuel=model.UL91,
            price_eur=2.15,
            observed_on=date(2026, 8, 1),
            payment="cash",
        )
        messages = prices.validate_prices(
            [record], [aerodrome()], extra_icaos=frozenset({"LF4724"})
        )
        assert not any(message.level == "error" for message in messages)

    def test_rejects_duplicate_keys(self):
        record = prices.PriceRecord(
            icao="LFCL",
            fuel=model.UL91,
            price_eur=2.15,
            observed_on=date(2026, 8, 1),
            payment="total",
        )
        messages = prices.validate_prices([record, record], [aerodrome()])
        assert any("doublon" in message.message for message in messages)


class TestValidatePricesCli:
    def test_empty_file_is_valid(self, tmp_path):
        prices_path = tmp_path / "prices.csv"
        prices_path.write_text(
            "icao,fuel,price_eur,observed_on,payment,note\n", encoding="utf-8"
        )
        records = prices.read_prices(prices_path)
        assert prices.validate_prices(records, [aerodrome()]) == []
