"""Location data for the price map on GitHub Pages.

Like :mod:`fuelmap.render.web`, only the JSON is generated; ``docs/prix.html``
is hand-maintained and fetches this file plus ``docs/prices.csv`` at load time.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ..model import (
    AVAILABILITY_LABELS,
    AVAILABILITY_ORDER,
    PRICE_MAP_FAMILIES,
    PRICE_MAP_FAMILY_LABELS,
    Aerodrome,
    format_fuels,
)

#: Bumped when the JSON shape changes, so the page can refuse stale data.
LOCATIONS_SCHEMA_VERSION = 1


def _legend(keys, labels: dict[str, str]) -> list[dict]:
    return [{"key": key, "label": labels[key]} for key in keys]


def _marker(aerodrome: Aerodrome, family: str) -> dict:
    family_fuels = aerodrome.price_map_family_fuels(family)
    availability = aerodrome.price_map_family_availability(family)
    return {
        "icao": aerodrome.icao,
        "name": aerodrome.name,
        "lat": aerodrome.latitude,
        "lon": aerodrome.longitude,
        "family": family,
        "familyLabel": PRICE_MAP_FAMILY_LABELS[family],
        "fuels": sorted(family_fuels),
        "fuelsLabel": format_fuels(family_fuels),
        "availability": availability,
        "availabilityLabel": AVAILABILITY_LABELS[availability],
        "allFuelsLabel": format_fuels(aerodrome.fuels),
        "section": aerodrome.fuel_section,
        "note": aerodrome.availability_note,
        "source": aerodrome.curated_source,
    }


def build_payload(
    aerodromes: list[Aerodrome],
    airac: str,
    today: date | None = None,
) -> dict:
    """Build the JSON document consumed by ``docs/prix.html``."""
    mappable = sorted(
        (a for a in aerodromes if a.has_position and a.is_plottable_for_prices),
        key=lambda a: a.icao,
    )
    unmappable = [a.icao for a in mappable if not a.price_map_families()]
    if unmappable:
        raise ValueError(f"aerodromes without plottable fuel: {unmappable}")

    markers = [
        _marker(aerodrome, family)
        for aerodrome in mappable
        for family in aerodrome.price_map_families()
    ]
    return {
        "schema": LOCATIONS_SCHEMA_VERSION,
        "airac": airac,
        "generated": (today or date.today()).isoformat(),
        "aerodromeCount": len(mappable),
        "families": _legend(PRICE_MAP_FAMILIES, PRICE_MAP_FAMILY_LABELS),
        "availability": _legend(AVAILABILITY_ORDER, AVAILABILITY_LABELS),
        "markers": markers,
    }


def write_locations_data(
    path: Path,
    aerodromes: list[Aerodrome],
    airac: str,
    today: date | None = None,
) -> int:
    """Write the locations JSON and return the number of plotted aerodromes."""
    payload = build_payload(aerodromes, airac, today)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload["aerodromeCount"]
