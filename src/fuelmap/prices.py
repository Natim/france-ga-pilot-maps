"""Crowd-reported fuel prices for the price map."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .model import PRICEABLE_FUELS, Aerodrome

PRICE_COLUMNS = (
    "icao",
    "fuel",
    "price_eur",
    "observed_on",
    "payment",
    "note",
)

VALID_PAYMENTS = frozenset({"cash", "total", "bp", "other"})

MIN_PRICE_EUR = 0.5
MAX_PRICE_EUR = 10.0

#: Prices older than this are flagged as stale on the map.
STALE_DAYS = 90


@dataclass(frozen=True)
class PriceRecord:
    icao: str
    fuel: str
    price_eur: float
    observed_on: date
    payment: str
    note: str = ""


@dataclass(frozen=True)
class ValidationMessage:
    level: str  # "error" or "warning"
    message: str


def read_prices(path: Path) -> list[PriceRecord]:
    """Load price rows from ``docs/prices.csv``."""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(PRICE_COLUMNS):
            expected = ", ".join(PRICE_COLUMNS)
            actual = ", ".join(reader.fieldnames or ())
            raise ValueError(
                f"en-tête CSV attendue ({expected}), reçue ({actual})"
            )
        records = []
        for index, row in enumerate(reader):
            if not row["icao"].strip():
                continue
            records.append(_from_row(row, line_number=index + 2))
        return records


def _from_row(row: dict[str, str], line_number: int) -> PriceRecord:
    icao = row["icao"].strip().upper()
    fuel = row["fuel"].strip()
    payment = row["payment"].strip().lower()
    note = row.get("note", "").strip()
    try:
        price_eur = float(row["price_eur"].strip().replace(",", "."))
    except ValueError as exc:
        raise ValueError(f"ligne {line_number} : price_eur invalide") from exc
    try:
        observed_on = date.fromisoformat(row["observed_on"].strip())
    except ValueError as exc:
        raise ValueError(f"ligne {line_number} : observed_on invalide") from exc
    return PriceRecord(
        icao=icao,
        fuel=fuel,
        price_eur=price_eur,
        observed_on=observed_on,
        payment=payment,
        note=note,
    )


def validate_prices(
    prices: list[PriceRecord],
    aerodromes: list[Aerodrome],
    extra_icaos: frozenset[str] | None = None,
    today: date | None = None,
) -> list[ValidationMessage]:
    """Return validation errors and warnings for a price file."""
    today = today or date.today()
    by_icao = {aerodrome.icao: aerodrome for aerodrome in aerodromes}
    known_icaos = set(by_icao) | (extra_icaos or frozenset())
    messages: list[ValidationMessage] = []
    seen: set[tuple[str, str, str]] = set()

    for index, record in enumerate(prices, start=2):
        prefix = f"ligne {index} ({record.icao}/{record.fuel}/{record.payment})"
        key = (record.icao, record.fuel, record.payment)

        if record.icao not in known_icaos:
            messages.append(
                ValidationMessage("error", f"{prefix} : code OACI inconnu")
            )
        if record.fuel not in PRICEABLE_FUELS:
            messages.append(
                ValidationMessage("error", f"{prefix} : carburant non pris en charge")
            )
        if record.payment not in VALID_PAYMENTS:
            messages.append(
                ValidationMessage(
                    "error",
                    f"{prefix} : payment doit être "
                    f"{', '.join(sorted(VALID_PAYMENTS))}",
                )
            )
        if not (MIN_PRICE_EUR <= record.price_eur <= MAX_PRICE_EUR):
            messages.append(
                ValidationMessage(
                    "error",
                    f"{prefix} : price_eur hors plage "
                    f"[{MIN_PRICE_EUR}, {MAX_PRICE_EUR}]",
                )
            )
        if record.observed_on > today:
            messages.append(
                ValidationMessage("error", f"{prefix} : observed_on dans le futur")
            )
        if key in seen:
            messages.append(
                ValidationMessage(
                    "error",
                    f"{prefix} : doublon (icao, fuel, payment)",
                )
            )
        seen.add(key)

        aerodrome = by_icao.get(record.icao)
        if aerodrome is not None and record.fuel not in aerodrome.fuels:
            messages.append(
                ValidationMessage(
                    "warning",
                    f"{prefix} : carburant absent de la VAC pour ce terrain",
                )
            )

    return messages
