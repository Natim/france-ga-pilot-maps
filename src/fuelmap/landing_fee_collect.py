"""Collect draft landing fees from public operator PDF guides."""

from __future__ import annotations

import csv
import re
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from enum import Enum
from pathlib import Path

from . import landing_fees
from .vac import PdftotextMissing, pdf_to_text

SOURCE_COLUMNS = (
    "icao",
    "url",
    "parser",
    "parser_arg",
    "effective_on",
    "payment",
    "operator",
)

DEFAULT_SOURCES = Path("data/landing_fee_sources.csv")
DEFAULT_PENDING_SOURCES = Path("data/landing_fee_sources.pending.csv")
DEFAULT_BAND = "1-2"

PENDING_COLUMNS = (
    "icao",
    "name",
    "operator",
    "pilot_page",
    "pdf_url",
    "parser",
    "parser_arg",
    "effective_on",
    "status",
    "notes",
)
DEFAULT_VAT_RATE = 0.20
USER_AGENT = "fuelmap/1.0 (+https://github.com/Natim/france-ul91-mogas-map)"

SECTION_START = re.compile(r"moins de 6 tonn", re.I)
SECTION_END = re.compile(r"plus de 6 tonn|more than 6 tonn", re.I)
APPLICABLE_ON = re.compile(
    r"Applicable au\s+(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})",
    re.I,
)
APPLICABLE_ON_FR = re.compile(
    r"Applicable au\s+(\d{1,2})(?:er|e|ème)?\s+"
    r"(janvier|février|fevrier|mars|avril|mai|juin|juillet|août|aout|"
    r"septembre|octobre|novembre|décembre|decembre)\s+(\d{4})",
    re.I,
)
FRENCH_MONTHS = {
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
}
PRICE_NUMBER = re.compile(r"(\d+[.,]\d+)")
BAND_LINE = re.compile(
    r"^(?:de\s*/?\s*from\s+)?de\s+(?:>\s*)?"
    r"(\d+(?:[.,]\d+)?)\s*(?:t\b|à|-).*?"
    r"(\d+(?:[.,]\d+)?)\s*t\b",
    re.I | re.M,
)
ROW_BAND_PRICE = re.compile(
    r"De\s+(?:>\s*)?\d+.*?t.*?(\d+[.,]\d+)\s*€?\s*(\d+[.,]\d+)",
    re.I,
)


def _normalize_band_label(line: str) -> str:
    match = re.match(
        r"((?:de\s*/?\s*from\s+)?de\s+(?:>\s*)?"
        r"\d+(?:[.,]\d+)?\s*(?:t\b|à|-).+?\d+(?:[.,]\d+)?\s*t\b)",
        line.strip(),
        re.I,
    )
    if match:
        return " ".join(match.group(1).split())
    cleaned = re.sub(r"\s+\d+[.,]\d+(?:\s*€)?(?:\s+\d+[.,]\d+(?:\s*€)?)?\s*$", "", line)
    return " ".join(cleaned.split())


def _is_weight_band_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    lowered = stripped.lower()
    if not (lowered.startswith("de ") or lowered.startswith("de / from")):
        return False
    if "par tonne" in lowered:
        return False
    return bool(re.search(r"\d+(?:[.,]\d+)?\s*t", lowered)) and (
        " à " in lowered or " to " in lowered
    )

ADP_AAG_CATEGORIES: dict[str, int] = {
    "LFPN": 1,
    "LFPT": 1,
    "LFPL": 2,
    "LFPV": 3,
    "LFPE": 3,
    "LFPZ": 3,
    "LFPA": 4,
    "LFPC": 4,
    "LFPK": 4,
    "LFDE": 4,
}

ADP_AAG_FEES: dict[int, float] = {
    1: 24.0,
    2: 18.0,
    3: 12.0,
    4: 12.0,
}


class FeeBand(str, Enum):
    FIRST = "0-1"
    SECOND = "1-2"
    MIN = "min"


@dataclass(frozen=True)
class PendingLandingFeeSource:
    icao: str
    name: str
    operator: str
    pilot_page: str
    pdf_url: str
    parser: str
    parser_arg: str = ""
    effective_on: date | None = None
    status: str = ""
    notes: str = ""


@dataclass(frozen=True)
class LandingFeeSource:
    icao: str
    url: str
    parser: str
    parser_arg: str = ""
    effective_on: date | None = None
    payment: str = "other"
    operator: str = ""


@dataclass(frozen=True)
class CollectedLandingFee:
    icao: str
    fee_eur: float
    observed_on: date
    payment: str
    note: str
    source_url: str
    band_label: str
    confidence: str


@dataclass(frozen=True)
class SourceCheckResult:
    icao: str
    pdf_url: str
    http_status: str
    parse_status: str = ""
    message: str = ""


@dataclass(frozen=True)
class CollectFailure:
    icao: str
    source_url: str
    message: str


def read_pending_sources(path: Path) -> list[PendingLandingFeeSource]:
    """Load backlog rows from ``data/landing_fee_sources.pending.csv``."""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(PENDING_COLUMNS):
            expected = ", ".join(PENDING_COLUMNS)
            actual = ", ".join(reader.fieldnames or ())
            raise ValueError(
                f"en-tête CSV attendue ({expected}), reçue ({actual})"
            )
        rows = []
        for row in reader:
            if not row["icao"].strip() or row["icao"].strip().startswith("#"):
                continue
            effective_on = None
            if row["effective_on"].strip():
                effective_on = date.fromisoformat(row["effective_on"].strip())
            rows.append(
                PendingLandingFeeSource(
                    icao=row["icao"].strip().upper(),
                    name=row["name"].strip(),
                    operator=row["operator"].strip(),
                    pilot_page=row["pilot_page"].strip(),
                    pdf_url=row.get("pdf_url", "").strip(),
                    parser=row["parser"].strip().lower(),
                    parser_arg=row.get("parser_arg", "").strip(),
                    effective_on=effective_on,
                    status=row.get("status", "").strip(),
                    notes=row.get("notes", "").strip(),
                )
            )
        return rows


def read_sources(path: Path) -> list[LandingFeeSource]:
    """Load PDF source rows from ``data/landing_fee_sources.csv``."""
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != list(SOURCE_COLUMNS):
            expected = ", ".join(SOURCE_COLUMNS)
            actual = ", ".join(reader.fieldnames or ())
            raise ValueError(
                f"en-tête CSV attendue ({expected}), reçue ({actual})"
            )
        sources = []
        for row in reader:
            if not row["icao"].strip() or row["icao"].strip().startswith("#"):
                continue
            effective_on = None
            if row["effective_on"].strip():
                effective_on = date.fromisoformat(row["effective_on"].strip())
            sources.append(
                LandingFeeSource(
                    icao=row["icao"].strip().upper(),
                    url=row["url"].strip(),
                    parser=row["parser"].strip().lower(),
                    parser_arg=row.get("parser_arg", "").strip(),
                    effective_on=effective_on,
                    payment=row["payment"].strip().lower() or "other",
                    operator=row.get("operator", "").strip(),
                )
            )
        return sources


def parse_fee_band(value: str) -> FeeBand:
    normalized = value.strip().lower()
    aliases = {
        "first": FeeBand.FIRST,
        "0-1": FeeBand.FIRST,
        "1-2": FeeBand.SECOND,
        "second": FeeBand.SECOND,
        "min": FeeBand.MIN,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        options = ", ".join(sorted(aliases))
        raise ValueError(f"bande inconnue ({value}), attendu : {options}") from exc


def _band_index(band: FeeBand) -> int | None:
    if band is FeeBand.FIRST:
        return 0
    if band is FeeBand.SECOND:
        return 1
    return None


def _to_float(value: str) -> float:
    return float(value.replace(",", "."))


def _extract_section(text: str) -> str | None:
    start = SECTION_START.search(text)
    if not start:
        return None
    end = SECTION_END.search(text, start.end())
    if end:
        return text[start.start() : end.start()]
    return text[start.start() : start.start() + 5000]


def _extract_bands(section: str) -> list[str]:
    bands: list[str] = []
    seen: set[str] = set()
    for line in section.splitlines():
        if not _is_weight_band_line(line):
            continue
        label = _normalize_band_label(line)
        if label in seen:
            continue
        seen.add(label)
        bands.append(label)
        if len(bands) >= 6:
            break
    if bands:
        return bands
    for match in BAND_LINE.finditer(section):
        label = " ".join(match.group(0).split())
        if label in seen:
            continue
        seen.add(label)
        bands.append(label)
        if len(bands) >= 6:
            break
    return bands


def _extract_prices(section: str, count: int, *, vat_included: bool) -> list[float]:
    ttc_marker = re.search(r"Tarif.*TTC|VAT incl", section, re.I)
    if ttc_marker:
        chunk = section[ttc_marker.end() :]
        prices = _scan_price_numbers(chunk, count)
        if prices:
            return prices

    row_prices = []
    for match in ROW_BAND_PRICE.finditer(section):
        row_prices.append(_to_float(match.group(2)))
        if len(row_prices) >= count:
            return row_prices

    ht_marker = re.search(r"Tarif.*HT|EX VAT|€ H\.T", section, re.I)
    chunk = section[ht_marker.end() :] if ht_marker else section
    prices = _scan_price_numbers(chunk, count)
    if not prices:
        return []
    if vat_included:
        return prices
    return [round(price * (1 + DEFAULT_VAT_RATE), 2) for price in prices]


def _scan_price_numbers(chunk: str, count: int) -> list[float]:
    prices: list[float] = []
    for match in PRICE_NUMBER.finditer(chunk):
        value = _to_float(match.group(1))
        if value > 500:
            continue
        prices.append(value)
        if len(prices) >= count:
            break
    return prices


def _extract_applicable_on(text: str) -> date | None:
    match = APPLICABLE_ON.search(text)
    if match:
        day, month, year = match.groups()
        return date(int(year), int(month), int(day))
    match = APPLICABLE_ON_FR.search(text)
    if not match:
        return None
    day, month_name, year = match.groups()
    month = FRENCH_MONTHS[month_name.lower()]
    return date(int(year), month, int(day))


def parse_edeis_landing_fee(
    text: str,
    *,
    band: FeeBand = FeeBand.SECOND,
    effective_on: date | None = None,
) -> tuple[float, str, date, str]:
    """Parse an EDEIS-style landing-fee guide and return TTC fee details."""
    section = _extract_section(text)
    if not section:
        raise ValueError("section « moins de 6 tonnes » introuvable")

    bands = _extract_bands(section)
    if not bands:
        raise ValueError("tranches de masse introuvables")

    prices = _extract_prices(section, len(bands), vat_included=False)
    if len(prices) < len(bands):
        raise ValueError("tarifs TTC/HT introuvables")

    if band is FeeBand.MIN:
        fee = min(prices)
        label = f"min ({', '.join(f'{p:.2f} €' for p in prices)})"
        confidence = "medium"
    else:
        index = _band_index(band)
        assert index is not None
        if index >= len(prices):
            raise ValueError(f"tranche {band.value} absente")
        fee = prices[index]
        label = bands[index]
        confidence = "high"

    observed_on = effective_on or _extract_applicable_on(text)
    if observed_on is None:
        raise ValueError("date d'application introuvable")

    return fee, label, observed_on, confidence


def parse_adp_aag_landing_fee(
    text: str,
    *,
    category: int,
    effective_on: date | None = None,
) -> tuple[float, str, date, str]:
    """Parse the ADP general-aviation fee guide (MMD ≤ 2 t column)."""
    if category not in ADP_AAG_FEES:
        raise ValueError(f"catégorie ADP AAG inconnue ({category})")
    fee = ADP_AAG_FEES[category]
    observed_on = effective_on or date(2019, 7, 1)
    label = f"ADP AAG cat. {category}, MMD ≤ 2 t"
    return fee, label, observed_on, "medium"


def _resolve_pdf_source(url: str) -> Path | None:
    if url.startswith("file://"):
        return Path(url[7:])
    candidate = Path(url)
    if candidate.exists():
        return candidate
    return None


def probe_pdf_url(url: str) -> tuple[str, str]:
    """Return ``(http_status, message)`` for a PDF URL or local path."""
    if not url:
        return "missing", "pdf_url vide"
    local = _resolve_pdf_source(url)
    if local is not None:
        if local.exists() and local.read_bytes()[:4] == b"%PDF":
            return "local", str(local)
        return "missing", f"fichier local introuvable ({local})"
    request = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = str(response.status)
            content_type = response.headers.get("Content-Type", "")
            if "pdf" not in content_type.lower() and not url.lower().endswith(".pdf"):
                return status, f"type inattendu ({content_type})"
            return status, ""
    except urllib.error.HTTPError as exc:
        return str(exc.code), str(exc.reason)
    except urllib.error.URLError as exc:
        return "error", str(exc.reason)


def check_landing_sources(
    sources: list[LandingFeeSource],
    pending: list[PendingLandingFeeSource] | None = None,
    *,
    verify_parse: bool = False,
    band: FeeBand = FeeBand.SECOND,
) -> list[SourceCheckResult]:
    """Check PDF URLs and optionally verify EDEIS parsing."""
    results: list[SourceCheckResult] = []
    seen: set[str] = set()

    def add_result(
        icao: str,
        pdf_url: str,
        *,
        parser: str = "edeis",
        parser_arg: str = "",
        effective_on: date | None = None,
        payment: str = "other",
        operator: str = "",
    ) -> None:
        if not pdf_url or pdf_url in seen:
            return
        seen.add(pdf_url)
        http_status, message = probe_pdf_url(pdf_url)
        parse_status = ""
        if verify_parse and http_status in {"200", "local"}:
            source = LandingFeeSource(
                icao=icao,
                url=pdf_url,
                parser=parser,
                parser_arg=parser_arg,
                effective_on=effective_on,
                payment=payment,
                operator=operator,
            )
            outcome = collect_from_source(source, band=band)
            if isinstance(outcome, CollectFailure):
                parse_status = "fail"
                message = outcome.message
            else:
                parse_status = "ok"
                message = f"{outcome.fee_eur:.2f} € ({outcome.band_label})"
        results.append(
            SourceCheckResult(
                icao=icao,
                pdf_url=pdf_url,
                http_status=http_status,
                parse_status=parse_status,
                message=message,
            )
        )

    for source in sources:
        add_result(
            source.icao,
            source.url,
            parser=source.parser,
            parser_arg=source.parser_arg,
            effective_on=source.effective_on,
            payment=source.payment,
            operator=source.operator,
        )
    for row in pending or []:
        add_result(
            row.icao,
            row.pdf_url,
            parser=row.parser,
            parser_arg=row.parser_arg,
            effective_on=row.effective_on,
            operator=row.operator,
        )
    results.sort(key=lambda row: row.icao)
    return results


def download_pdf(url: str, destination: Path) -> None:
    local = _resolve_pdf_source(url)
    if local is not None:
        destination.write_bytes(local.read_bytes())
        return
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = response.read()
    except urllib.error.URLError as exc:
        raise ValueError(f"téléchargement impossible : {exc}") from exc
    if not payload.startswith(b"%PDF"):
        raise ValueError("le fichier téléchargé n'est pas un PDF")
    destination.write_bytes(payload)


def _build_note(
    *,
    operator: str,
    band_label: str,
    source_url: str,
    confidence: str,
) -> str:
    parts = [
        "Import auto",
        f"bande {band_label}",
        "TTC",
        "avion léger < 6 t",
    ]
    if operator:
        parts.insert(1, operator)
    if confidence != "high":
        parts.append(f"confiance {confidence}")
    parts.append(source_url)
    return "; ".join(parts)


def collect_from_source(
    source: LandingFeeSource,
    *,
    band: FeeBand = FeeBand.SECOND,
    cache_dir: Path | None = None,
) -> CollectedLandingFee | CollectFailure:
    cache_dir = cache_dir or Path(tempfile.gettempdir()) / "fuelmap-landing-fees"
    cache_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = cache_dir / f"{source.icao}.pdf"

    try:
        local = _resolve_pdf_source(source.url)
        if local is not None:
            pdf_path = local
        elif not pdf_path.exists():
            download_pdf(source.url, pdf_path)
        text = pdf_to_text(pdf_path)
        if not text.strip():
            raise ValueError("PDF illisible (pdftotext vide)")

        if source.parser == "edeis":
            fee, band_label, observed_on, confidence = parse_edeis_landing_fee(
                text,
                band=band,
                effective_on=source.effective_on,
            )
        elif source.parser == "adp_aag":
            category = int(source.parser_arg or ADP_AAG_CATEGORIES[source.icao])
            fee, band_label, observed_on, confidence = parse_adp_aag_landing_fee(
                text,
                category=category,
                effective_on=source.effective_on,
            )
        else:
            raise ValueError(f"parser inconnu ({source.parser})")

        return CollectedLandingFee(
            icao=source.icao,
            fee_eur=fee,
            observed_on=observed_on,
            payment=source.payment,
            note=_build_note(
                operator=source.operator,
                band_label=band_label,
                source_url=source.url,
                confidence=confidence,
            ),
            source_url=source.url,
            band_label=band_label,
            confidence=confidence,
        )
    except (ValueError, PdftotextMissing) as exc:
        return CollectFailure(
            icao=source.icao,
            source_url=source.url,
            message=str(exc),
        )


def collect_landing_fees(
    sources: list[LandingFeeSource],
    *,
    band: FeeBand = FeeBand.SECOND,
    cache_dir: Path | None = None,
) -> tuple[list[CollectedLandingFee], list[CollectFailure]]:
    collected: list[CollectedLandingFee] = []
    failures: list[CollectFailure] = []
    for source in sources:
        result = collect_from_source(source, band=band, cache_dir=cache_dir)
        if isinstance(result, CollectFailure):
            failures.append(result)
        else:
            collected.append(result)
    collected.sort(key=lambda row: row.icao)
    failures.sort(key=lambda row: row.icao)
    return collected, failures


def write_draft_csv(
    path: Path,
    rows: list[CollectedLandingFee],
    *,
    append: bool = False,
) -> None:
    """Write collected rows to a draft ``landing_fees`` CSV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        if not append:
            writer.writerow(landing_fees.FEE_COLUMNS)
        for row in rows:
            writer.writerow(
                [
                    row.icao,
                    f"{row.fee_eur:.2f}".replace(".", ","),
                    row.observed_on.isoformat(),
                    row.payment,
                    row.note,
                ]
            )
