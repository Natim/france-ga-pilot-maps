"""Command line entry point.

Two subcommands:

``extract``
    Parse the SIA eAIP VAC charts and regenerate every output. Needs the
    unzipped eAIP package and ``pdftotext``.
``rebuild``
    Regenerate the Markdown and map data from the committed CSV. Useful when
    editing presentation without a copy of the source PDFs at hand.

Both write the CSVs straight from the charts, then apply the curated access
conditions of :mod:`fuelmap.overrides` to the Markdown and map only.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import overrides, pipeline, prices, vac
from .model import Aerodrome
from .render import csv_export, locations, markdown, web

DEFAULT_ALL_CSV = Path("data/aerodromes-all.csv")
DEFAULT_UNLEADED_CSV = Path("data/aerodromes-unleaded.csv")
DEFAULT_MARKDOWN = Path("AERODROMES.md")
DEFAULT_MAP_DATA = Path("docs/aerodromes.json")
DEFAULT_LOCATIONS_DATA = Path("docs/locations.json")
DEFAULT_PRICES_CSV = Path("docs/prices.csv")

PREVIEW_ROWS = 10


def _add_output_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--all-csv",
        type=Path,
        default=DEFAULT_ALL_CSV,
        help="CSV de tous les aérodromes (défaut : %(default)s).",
    )
    parser.add_argument(
        "--unleaded-csv",
        type=Path,
        default=DEFAULT_UNLEADED_CSV,
        help="CSV des terrains sans plomb (défaut : %(default)s).",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=DEFAULT_MARKDOWN,
        help="Liste Markdown lisible (défaut : %(default)s).",
    )
    parser.add_argument(
        "--map-data",
        type=Path,
        default=DEFAULT_MAP_DATA,
        help="Données JSON de la carte sans plomb (défaut : %(default)s).",
    )
    parser.add_argument(
        "--locations-data",
        type=Path,
        default=DEFAULT_LOCATIONS_DATA,
        help="Données JSON des terrains pour la carte prix (défaut : %(default)s).",
    )
    parser.add_argument(
        "--airac",
        default=None,
        help="Cycle AIRAC (YYYY-MM-DD). Auto-détecté si omis.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fuelmap",
        description="Aérodromes français avec essence sans plomb (UL91 / mogas).",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    extract = subcommands.add_parser(
        "extract", help="Analyser les cartes VAC de l'eAIP et tout régénérer."
    )
    extract.add_argument(
        "--vac-dir",
        type=Path,
        required=True,
        help="Dossier Atlas-VAC/PDF_AIPparSSection/VAC/AD de l'eAIP dézippé.",
    )
    extract.add_argument(
        "--workers",
        type=int,
        default=pipeline.DEFAULT_WORKERS,
        help="Processus d'analyse en parallèle (défaut : %(default)s).",
    )
    _add_output_arguments(extract)

    rebuild = subcommands.add_parser(
        "rebuild", help="Régénérer Markdown et carte depuis le CSV commité."
    )
    _add_output_arguments(rebuild)

    validate_prices = subcommands.add_parser(
        "validate-prices",
        help="Vérifier docs/prices.csv contre les terrains connus.",
    )
    validate_prices.add_argument(
        "--prices-csv",
        type=Path,
        default=DEFAULT_PRICES_CSV,
        help="CSV des prix (défaut : %(default)s).",
    )
    validate_prices.add_argument(
        "--all-csv",
        type=Path,
        default=DEFAULT_ALL_CSV,
        help="CSV de tous les aérodromes (défaut : %(default)s).",
    )

    return parser


def _previous_metadata(path: Path) -> tuple[str, date | None]:
    """Recover the AIRAC cycle and extraction date from a previous run.

    ``rebuild`` reuses both rather than stamping today, so that regenerating
    the derived files is a no-op as long as the underlying data is unchanged.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload["airac"], date.fromisoformat(payload["generated"])
    except (OSError, ValueError, KeyError):
        return vac.UNKNOWN_AIRAC, None


def _write_outputs(
    args: argparse.Namespace,
    aerodromes: list[Aerodrome],
    airac: str,
    extracted_on: date | None,
):
    unleaded = pipeline.unleaded_only(aerodromes)

    # The CSVs record the charts as they are; the reader-facing outputs get the
    # curated data on top.
    csv_export.write_csv(args.all_csv, aerodromes)
    csv_export.write_csv(
        args.unleaded_csv, unleaded, columns=csv_export.SUBSET_COLUMNS
    )

    # Overrides run before the unleaded filter, not after: an entry may add a
    # fuel, which is the whole reason a field selling only 100LL can qualify.
    curated_all = overrides.apply_all(aerodromes)
    curated = pipeline.unleaded_only(curated_all)
    args.markdown.write_text(
        markdown.render_markdown(curated, airac, today=extracted_on), encoding="utf-8"
    )
    plotted = web.write_map_data(args.map_data, curated, airac, today=extracted_on)
    price_plottable = pipeline.plottable_for_prices(curated_all)
    price_plotted = locations.write_locations_data(
        args.locations_data, price_plottable, airac, today=extracted_on
    )

    print(f"\nTous les terrains  : {args.all_csv} ({len(aerodromes)})")
    print(f"Sans plomb (VAC)   : {args.unleaded_csv} ({len(unleaded)})")
    print(f"Markdown           : {args.markdown} ({len(curated)} terrains)")
    print(f"Données carte      : {args.map_data} ({plotted} points)")
    print(f"Terrains prix      : {args.locations_data} ({price_plotted} terrains)")

    unplottable = [a.icao for a in curated if not a.has_position]
    if unplottable:
        print(f"  ⚠ sans coordonnées, absents de la carte : {unplottable}")
    return curated


def _run_validate_prices(args: argparse.Namespace) -> int:
    if not args.prices_csv.exists():
        print(f"Erreur : {args.prices_csv} introuvable.", file=sys.stderr)
        return 1
    if not args.all_csv.exists():
        print(f"Erreur : {args.all_csv} introuvable.", file=sys.stderr)
        return 1
    try:
        records = prices.read_prices(args.prices_csv)
    except ValueError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    aerodromes = csv_export.read_csv(args.all_csv)
    extra_icaos = frozenset(overrides.addition_icaos())
    messages = prices.validate_prices(records, aerodromes, extra_icaos)
    errors = [m for m in messages if m.level == "error"]
    warnings = [m for m in messages if m.level == "warning"]
    for message in warnings:
        print(f"Avertissement : {message.message}", file=sys.stderr)
    for message in errors:
        print(f"Erreur : {message.message}", file=sys.stderr)
    if errors:
        return 1
    print(f"{len(records)} prix valides ({len(warnings)} avertissement(s)).")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "validate-prices":
        return _run_validate_prices(args)

    if args.command == "extract":
        try:
            aerodromes = pipeline.extract_all(args.vac_dir, workers=args.workers)
        except (FileNotFoundError, vac.PdftotextMissing) as exc:
            print(f"Erreur : {exc}", file=sys.stderr)
            return 1
        airac = args.airac or vac.detect_airac(args.vac_dir)
        extracted_on = None
    else:
        if not args.all_csv.exists():
            print(f"Erreur : {args.all_csv} introuvable.", file=sys.stderr)
            return 1
        aerodromes = csv_export.read_csv(args.all_csv)
        previous_airac, extracted_on = _previous_metadata(args.map_data)
        airac = args.airac or previous_airac

    unleaded = _write_outputs(args, aerodromes, airac, extracted_on)

    print(f"\nAperçu ({PREVIEW_ROWS} premiers) :")
    for aerodrome in unleaded[:PREVIEW_ROWS]:
        fuels = "|".join(sorted(aerodrome.fuels))
        print(f"  {aerodrome.icao:6} {fuels:35} {aerodrome.name}")
    return 0
