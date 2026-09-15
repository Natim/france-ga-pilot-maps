"""Crowd-reported landing fees for light aircraft on the landing-fee map."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .model import Aerodrome
from .prices import VALID_PAYMENTS, ValidationMessage

FEE_COLUMNS = (
    "icao",
    "fee_eur",
    "observed_on",
    "payment",
    "note",
)

MIN_FEE_EUR = 0.0
MAX_FEE_EUR = 500.0

# Disjoint fee tiers shown on docs/landing.html (keep in sync with landing.html).
FEE_CATEGORY_FREE = "free"
FEE_CATEGORY_LT8 = "lt8"
FEE_CATEGORY_LT10 = "lt10"
FEE_CATEGORY_LT15 = "lt15"
FEE_CATEGORY_LT20 = "lt20"
FEE_CATEGORY_MORE = "more"


def fee_category(fee_eur: float) -> str:
    """Return the landing-fee tier key for ``fee_eur`` (TTC, avion léger)."""
    if fee_eur == 0:
        return FEE_CATEGORY_FREE
    if fee_eur < 8:
        return FEE_CATEGORY_LT8
    if fee_eur < 10:
        return FEE_CATEGORY_LT10
    if fee_eur < 15:
        return FEE_CATEGORY_LT15
    if fee_eur < 20:
        return FEE_CATEGORY_LT20
    return FEE_CATEGORY_MORE


@dataclass(frozen=True)
class LandingFeeRecord:
    icao: str
    fee_eur: float
    observed_on: date
    payment: str
    note: str = ""


def read_landing_fees(path: Path) -> list[LandingFeeRecord]:
    """Load fee rows from ``docs/landing_fees.csv``."""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(FEE_COLUMNS):
            expected = ", ".join(FEE_COLUMNS)
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


def _from_row(row: dict[str, str], line_number: int) -> LandingFeeRecord:
    icao = row["icao"].strip().upper()
    payment = row["payment"].strip().lower()
    note = row.get("note", "").strip()
    try:
        fee_eur = float(row["fee_eur"].strip().replace(",", "."))
    except ValueError as exc:
        raise ValueError(f"ligne {line_number} : fee_eur invalide") from exc
    try:
        observed_on = date.fromisoformat(row["observed_on"].strip())
    except ValueError as exc:
        raise ValueError(f"ligne {line_number} : observed_on invalide") from exc
    return LandingFeeRecord(
        icao=icao,
        fee_eur=fee_eur,
        observed_on=observed_on,
        payment=payment,
        note=note,
    )


def validate_landing_fees(
    fees: list[LandingFeeRecord],
    aerodromes: list[Aerodrome],
    extra_icaos: frozenset[str] | None = None,
    today: date | None = None,
) -> list[ValidationMessage]:
    """Return validation errors for a landing-fee file."""
    today = today or date.today()
    known_icaos = {aerodrome.icao for aerodrome in aerodromes} | (
        extra_icaos or frozenset()
    )
    messages: list[ValidationMessage] = []
    seen: set[str] = set()

    for index, record in enumerate(fees, start=2):
        prefix = f"ligne {index} ({record.icao}/{record.payment})"

        if record.icao not in known_icaos:
            messages.append(
                ValidationMessage("error", f"{prefix} : code OACI inconnu")
            )
        if record.payment not in VALID_PAYMENTS:
            messages.append(
                ValidationMessage(
                    "error",
                    f"{prefix} : payment doit être "
                    f"{', '.join(sorted(VALID_PAYMENTS))}",
                )
            )
        if not (MIN_FEE_EUR <= record.fee_eur <= MAX_FEE_EUR):
            messages.append(
                ValidationMessage(
                    "error",
                    f"{prefix} : fee_eur hors plage "
                    f"[{MIN_FEE_EUR}, {MAX_FEE_EUR}]",
                )
            )
        if record.observed_on > today:
            messages.append(
                ValidationMessage("error", f"{prefix} : observed_on dans le futur")
            )
        if record.icao in seen:
            messages.append(
                ValidationMessage(
                    "error",
                    f"{prefix} : doublon (icao)",
                )
            )
        seen.add(record.icao)

    return messages
