"""Location data for the landing-fee map on GitHub Pages.

Every aerodrome with coordinates from the eAIP (plus hand-entered additions)
gets one marker. Crowd-reported fees in ``docs/landing_fees.csv`` are joined
at load time by ``docs/landing.html``.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from ..model import Aerodrome

LANDING_LOCATIONS_SCHEMA_VERSION = 1


def _marker(aerodrome: Aerodrome) -> dict:
    return {
        "icao": aerodrome.icao,
        "name": aerodrome.name,
        "lat": aerodrome.latitude,
        "lon": aerodrome.longitude,
        "note": aerodrome.availability_note,
        "source": aerodrome.curated_source,
    }


def build_payload(
    aerodromes: list[Aerodrome],
    airac: str,
    today: date | None = None,
) -> dict:
    """Build the JSON document consumed by ``docs/landing.html``."""
    markers = [
        _marker(aerodrome)
        for aerodrome in sorted(aerodromes, key=lambda a: a.icao)
        if aerodrome.has_position
    ]
    return {
        "schema": LANDING_LOCATIONS_SCHEMA_VERSION,
        "airac": airac,
        "generated": (today or date.today()).isoformat(),
        "aerodromeCount": len(markers),
        "markers": markers,
    }


def write_landing_locations_data(
    path: Path,
    aerodromes: list[Aerodrome],
    airac: str,
    today: date | None = None,
) -> int:
    """Write the landing locations JSON and return the aerodrome count."""
    payload = build_payload(aerodromes, airac, today)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return payload["aerodromeCount"]
