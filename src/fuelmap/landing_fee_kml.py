"""Import landing fees from the community Google My Maps KML export."""

from __future__ import annotations

import csv
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import landing_fees

KML_NS = {"k": "http://www.opengis.net/kml/2.2"}
DEFAULT_KML_URL = (
    "https://www.google.com/maps/d/kml?mid=12cCo5-MXHQGUaqV84Ws8IHOXRCY&forcekml=1"
)
DEFAULT_MAP_OBSERVED_ON = date(2024, 6, 2)
MAP_SOURCE_NOTE = "carte taxes d'atterrissage (C. Rousseau), snapshot 2024-06-02"
USER_AGENT = "fuelmap/1.0 (+https://github.com/Natim/france-ga-pilot-maps)"

ICAO_PATTERN = re.compile(r"^LF[A-Z0-9]{2,3}$")
SKIP_DESCRIPTION = re.compile(
    r"tarif inconnu|fermé|ferme|closed",
    re.I,
)

# Midpoints of the map legend tiers (< 2 t).
COLOR_TIER_FEE = {
    "FFFFFF": 0.0,
    "62AF44": 3.0,
    "009D57": 3.0,
    "F4EB37": 7.5,
    "F4B400": 7.5,
    "F8971B": 12.5,
    "DB4436": 22.5,
    "000000": 35.0,
}


@dataclass(frozen=True)
class KmlLandingFee:
    icao: str
    fee_eur: float
    observed_on: date
    payment: str
    note: str
    source_description: str
    fee_kind: str


def download_kml(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = response.read()
    destination.write_bytes(payload)


def _style_color(style_url: str | None) -> str | None:
    if not style_url:
        return None
    match = re.search(r"icon-95[0-9]-([0-9A-Fa-f]{6})", style_url)
    return match.group(1).upper() if match else None


def _clean_description(description: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", description, flags=re.I)
    return " ".join(text.split())


def parse_kml_description(description: str) -> tuple[float | None, str]:
    """Return ``(fee_eur, kind)`` parsed from a placemark description."""
    text = _clean_description(description)
    lowered = text.lower()
    if SKIP_DESCRIPTION.search(lowered):
        return None, "skip"
    if "gratuit" in lowered or re.search(r"\bfree\b", lowered):
        return 0.0, "free"

    match = re.search(r"(\d+[.,]\d+)\s*€", text)
    if match:
        return float(match.group(1).replace(",", ".")), "exact"

    match = re.search(r"(\d+[.,]?\d*)\s*à\s*(\d+[.,]?\d*)\s*€", text, re.I)
    if match:
        low = float(match.group(1).replace(",", "."))
        high = float(match.group(2).replace(",", "."))
        return round((low + high) / 2, 2), f"range {low}-{high}"

    match = re.search(r"(\d+)\s*€", text)
    if match:
        return float(match.group(1)), "exact"

    return None, "unknown"


def parse_kml_landing_fees(
    kml_path: Path,
    *,
    observed_on: date = DEFAULT_MAP_OBSERVED_ON,
    payment: str = "other",
) -> list[KmlLandingFee]:
    """Parse French ``LF*`` placemarks from a Google My Maps KML export."""
    root = ET.parse(kml_path).getroot()
    records: list[KmlLandingFee] = []

    for placemark in root.findall(".//k:Placemark", KML_NS):
        name = placemark.find("k:name", KML_NS)
        if name is None or not name.text:
            continue
        icao = name.text.strip().upper()
        if not ICAO_PATTERN.match(icao):
            continue

        description = placemark.find("k:description", KML_NS)
        raw_description = description.text if description is not None else ""
        style_url = placemark.find("k:styleUrl", KML_NS)
        color = _style_color(style_url.text if style_url is not None else None)

        fee, kind = parse_kml_description(raw_description)
        if fee is None and color in COLOR_TIER_FEE:
            fee = COLOR_TIER_FEE[color]
            kind = f"tier {color}"
        if fee is None:
            continue

        cleaned = _clean_description(raw_description)
        note = MAP_SOURCE_NOTE
        if cleaned:
            note = f"{MAP_SOURCE_NOTE}; {cleaned}"

        records.append(
            KmlLandingFee(
                icao=icao,
                fee_eur=fee,
                observed_on=observed_on,
                payment=payment,
                note=note,
                source_description=raw_description,
                fee_kind=kind,
            )
        )

    records.sort(key=lambda row: row.icao)
    return records


def merge_kml_into_landing_fees(
    existing: list[landing_fees.LandingFeeRecord],
    imported: list[KmlLandingFee],
    *,
    known_icaos: frozenset[str],
) -> tuple[list[landing_fees.LandingFeeRecord], int, int]:
    """Merge KML rows into existing fees; existing ICAOs win."""
    by_icao = {record.icao: record for record in existing}
    added = 0
    skipped_unknown = 0

    for row in imported:
        if row.icao in by_icao:
            continue
        if row.icao not in known_icaos:
            skipped_unknown += 1
            continue
        by_icao[row.icao] = landing_fees.LandingFeeRecord(
            icao=row.icao,
            fee_eur=row.fee_eur,
            observed_on=row.observed_on,
            payment=row.payment,
            note=row.note,
        )
        added += 1

    merged = sorted(by_icao.values(), key=lambda record: record.icao)
    return merged, added, skipped_unknown


def write_landing_fees_csv(
    path: Path,
    records: list[landing_fees.LandingFeeRecord],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(landing_fees.FEE_COLUMNS)
        for record in records:
            writer.writerow(
                [
                    record.icao,
                    f"{record.fee_eur:.2f}",
                    record.observed_on.isoformat(),
                    record.payment,
                    record.note,
                ]
            )
