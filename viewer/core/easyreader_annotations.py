"""Przenośne notatki ``.easyreader`` nakładane na tymczasowy podgląd EPUB-a.

Plik notatek nie zawiera pełnej treści książki. Przechowuje cyfrowy odcisk
oryginału, pozycje opracowanych bloków oraz treść komentarzy. Oryginalny EPUB
jest zawsze otwierany tylko do odczytu, a adnotacje trafiają wyłącznie do jego
rozpakowanej kopii w katalogu tymczasowym viewera.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import copy
import difflib
import hashlib
import html
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any

logger = logging.getLogger("epub_viewer.easyreader")


FORMAT_NAME = "czytelnicza-brzytwa-ockhama"
FORMAT_VERSION = 1

BLOCK_RE = re.compile(
    r"<(?P<tag>h[1-6]|p|blockquote|li)\b[^>]*>.*?</(?P=tag)\s*>",
    re.IGNORECASE | re.DOTALL,
)

READING_STYLE = """
<style id="easyreader-reading-style" type="text/css">
body {
  max-width: 44em !important;
  margin-left: auto !important;
  margin-right: auto !important;
  padding-left: 1.2em !important;
  padding-right: 1.2em !important;
  font-family: Verdana, Arial, sans-serif !important;
  font-size: 1em !important;
  line-height: 1.65 !important;
  letter-spacing: 0.015em;
  word-spacing: 0.06em;
}
p { margin-top: 0.35em; margin-bottom: 0.9em; }
.easyreader-opracowanie {
  margin: 1.2em 0 1.8em;
  padding: 0.8em 1em;
  border-left: 0.35em solid #4f7396;
  background: #f2f6fa;
  color: #17202a;
}
.easyreader-opracowanie h4 {
  margin: 0.8em 0 0.3em;
  color: #315675;
  font-size: 1em;
}
.easyreader-opracowanie h4:first-child { margin-top: 0; }
.easyreader-opracowanie p { margin: 0.2em 0 0.8em; }
.easyreader-opracowanie ul { margin-top: 0.3em; }
.easyreader-ai { border-left-color: #875c9e; }
.easyreader-notatka { border-left-color: #9a6b20; }
</style>
""".strip()


class EasyReaderAnnotationError(Exception):
    """Niepoprawny plik notatek albo notatki przypisane do innej książki."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def block_sha256(raw_html: str) -> str:
    return hashlib.sha256(raw_html.encode("utf-8")).hexdigest()


def normalize_block_text(raw_html: str) -> str:
    """Widoczny tekst bloku, bez znaczników HTML i ze znormalizowanymi białymi znakami."""
    without_tags = re.sub(r"<[^>]+>", " ", raw_html)
    return " ".join(html.unescape(without_tags).split())


def block_text_sha256(raw_html: str) -> str:
    """Odcisk widocznej treści, odporny na techniczne zmiany znaczników HTML."""
    return hashlib.sha256(normalize_block_text(raw_html).encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_document(source_epub: str | Path, title: str | None = None) -> dict[str, Any]:
    source = Path(source_epub).resolve()
    return {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "created": _utc_now(),
        "modified": _utc_now(),
        "book": {
            "title": title or source.stem,
            "filename": source.name,
            "size": source.stat().st_size,
            "sha256": sha256_file(source),
        },
        "annotations": [],
    }


def save_document(path: str | Path, document: dict[str, Any]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    document["modified"] = _utc_now()
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)


def create_document(path: str | Path, source_epub: str | Path, title: str | None = None) -> dict[str, Any]:
    document = new_document(source_epub, title=title)
    save_document(path, document)
    return document


def load_document(path: str | Path) -> dict[str, Any]:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EasyReaderAnnotationError(f"Nie można odczytać pliku .easyreader: {exc}") from exc
    if document.get("format") != FORMAT_NAME or document.get("version") != FORMAT_VERSION:
        raise EasyReaderAnnotationError("Nieobsługiwany format pliku .easyreader.")
    if not isinstance(document.get("book"), dict) or not isinstance(document.get("annotations"), list):
        raise EasyReaderAnnotationError("Plik .easyreader ma niepełną strukturę.")
    return document


def verify_source(document: dict[str, Any], source_epub: str | Path) -> None:
    expected = str(document.get("book", {}).get("sha256", ""))
    if not expected or sha256_file(source_epub) != expected:
        raise EasyReaderAnnotationError(
            "Plik notatek należy do innego wydania książki albo oryginalny EPUB został zmieniony."
        )


def matches_source(document: dict[str, Any], source_epub: str | Path) -> bool:
    """Wersja `verify_source`, która nie zgłasza wyjątku - do wstępnych sprawdzeń w UI
    (np. zanim zaproponujemy użycie pliku notatek, sprawdzamy, czy w ogóle pasuje)."""
    expected = str(document.get("book", {}).get("sha256", ""))
    try:
        return bool(expected) and sha256_file(source_epub) == expected
    except OSError:
        return False


def append_annotation(path: str | Path, source_epub: str | Path, annotation: dict[str, Any]) -> None:
    document = load_document(path)
    verify_source(document, source_epub)
    annotation_id = str(annotation.get("id", ""))
    if not annotation_id:
        raise EasyReaderAnnotationError("Adnotacja nie ma identyfikatora.")
    if any(str(item.get("id")) == annotation_id for item in document["annotations"]):
        raise EasyReaderAnnotationError(f"Adnotacja {annotation_id} już istnieje.")
    annotation.setdefault("created", _utc_now())
    document["annotations"].append(annotation)
    save_document(path, document)


def _html_paragraphs(value: str) -> str:
    parts = [part.strip() for part in re.split(r"\n\s*\n", value) if part.strip()]
    return "".join("<p>" + html.escape(part).replace("\n", "<br/>") + "</p>" for part in parts)


def render_annotation(annotation: dict[str, Any]) -> str:
    content = annotation.get("content") or {}
    sections: list[str] = []
    for key, title, css in [
        ("modernizacja", "Przekład lub uwspółcześnienie", "easyreader-modernizacja"),
        ("prosty_jezyk", "Prostym językiem", "easyreader-prosto"),
        ("komentarz_ai", "Komentarz AI", "easyreader-ai"),
        ("notatka_czytelnika", "Notatka czytelnika", "easyreader-notatka"),
    ]:
        value = str(content.get(key, "")).strip()
        if value:
            sections.append(f'<section class="{css}"><h4>{title}</h4>{_html_paragraphs(value)}</section>')
    explanations = content.get("objasnienia") or []
    if explanations:
        items: list[str] = []
        for item in explanations:
            if isinstance(item, dict):
                term = str(item.get("haslo", "")).strip()
                text = str(item.get("tresc", "")).strip()
                label = f"<strong>{html.escape(term)}:</strong> " if term else ""
                items.append(f"<li>{label}{html.escape(text)}</li>")
            else:
                items.append(f"<li>{html.escape(str(item))}</li>")
        sections.append(
            '<section class="easyreader-objasnienia"><h4>Objaśnienia</h4><ul>'
            + "".join(items)
            + "</ul></section>"
        )
    if not sections:
        raise EasyReaderAnnotationError(f"Adnotacja {annotation.get('id', '?')} jest pusta.")
    annotation_id = html.escape(str(annotation.get("id", "")), quote=True)
    return (
        f'\n<aside class="easyreader-opracowanie" data-easyreader-id="{annotation_id}">'
        + "".join(sections)
        + "</aside>\n"
    )


MINHASH_SIZE = 16
SHINGLE_SIZE = 3

# Punkt 8 zgłoszenia: ocena ryzyka i uzasadnienie progu.
#
# MinHash to odcisk zbioru n-gramów (shingli) tekstu. Dla DŁUGIEGO,
# naturalnego tekstu odtworzenie oryginału z samego odcisku jest praktycznie
# niewykonalne - liczba możliwych zdań o podobnej długości jest astronomicznie
# duża, więc atak "zgadnij i porównaj odcisk" nie ma szans się powieść w
# rozsądnym czasie.
#
# Dla BARDZO KRÓTKIEGO bloku (pojedyncze słowo, krótki śródtytuł typu
# "Rozdział 5" albo "KONIEC") sytuacja jest inna: liczba sensownych
# kandydatów jest mała, więc atakujący dysponujący słownikiem typowych
# krótkich fraz (i posiadający już plik `.easyreader`) mógłby w rozsądnym
# czasie wypróbować wszystkie i porównać odciski MinHash, aby POTWIERDZIĆ
# trafną domyślną treść. To jest realne, choć wąskie, ryzyko - dokładnie
# ten sam rodzaj krótkiego bloku, który i tak daje NIEWIARYGODNE dopasowanie
# przez podobieństwo (patrz punkt 2: zbyt mało shingli => szumny wynik).
#
# Bezpieczniejsza metoda (zastosowana poniżej): dla bloków krótszych niż
# `MIN_WORDS_FOR_MINHASH` słów NIE liczymy i NIE zapisujemy odcisku MinHash
# w ogóle - `block_minhash` zostaje pominięte. Takie bloki są dopasowywane
# WYŁĄCZNIE przez dokładny skrót (poziom 1/2), nigdy przez podobieństwo, co
# jednocześnie usuwa ryzyko zgadywania i usuwa niewiarygodne dopasowania
# krótkich fragmentów.
MIN_WORDS_FOR_MINHASH = 8

# Próg dla podobieństwa szacowanego przez MinHash. Podniesiony względem
# pierwszej wersji (0.55 -> 0.78): przy 16 funkcjach haszujących różnica
# między "to na pewno ten sam akapit po drobnej korekcie OCR" a "to
# przypadkowo podobny, ale INNY akapit" bywa subtelna - wolimy pominąć
# niepewną adnotację (patrz punkt 1) niż wstawić ją w złe miejsce.
SIMILARITY_THRESHOLD = 0.78
SIMILARITY_SEARCH_WINDOW = 15  # ile bloków w każdą stronę od zapisanego indeksu przeszukujemy

# Punkt 2 (powtarzające się akapity): jeśli w oknie wyszukiwania jest więcej
# niż jeden kandydat o wysokim podobieństwie (typowe dla powtarzającego się
# tekstu - refren, powtórzony nagłówek), różnica między najlepszym a drugim
# najlepszym wynikiem musi być wystarczająco duża, inaczej dopasowanie jest
# UZNAWANE ZA NIEPEWNE (nie zgadujemy, który z bliźniaczych akapitów to ten
# właściwy).
AMBIGUITY_MARGIN = 0.08

# Poziomy dopasowania uznawane za "pewne" - patrz punkt 1: TYLKO te wolno
# faktycznie zastosować (wstawić adnotację do tekstu). Wszystko inne (w tym
# dawny "poziom 4" - samo raw_index bez potwierdzenia treścią) jest
# traktowane jako NIEDOPASOWANE i pomijane, żeby notatka nigdy nie trafiła
# do niewłaściwego akapitu.
CONFIDENT_TIERS = ("dokładny HTML", "znormalizowany tekst")


def is_confident_tier(tier: str) -> bool:
    """Czy `tier` (zwrócony przez `find_matching_block`) uznajemy za
    wystarczająco pewny, by faktycznie zastosować adnotację (punkt 1)."""
    return tier in CONFIDENT_TIERS or tier.startswith("podobieństwo tekstu")


def _word_count(text: str) -> int:
    return len(text.split())


def _shingles(text: str, size: int = SHINGLE_SIZE) -> set[str]:
    words = text.split()
    if not words:
        return set()
    if len(words) < size:
        return {" ".join(words)}
    return {" ".join(words[i : i + size]) for i in range(len(words) - size + 1)}


def minhash_signature(text: str, *, num_hashes: int = MINHASH_SIZE, shingle_size: int = SHINGLE_SIZE) -> list[int]:
    """Nieodwracalny odcisk tekstu do porównań podobieństwa (przybliżone
    podobieństwo Jaccarda przez MinHash). W przeciwieństwie do
    przechowywania fragmentu tekstu wprost, z tego odcisku NIE da się
    odtworzyć oryginalnej treści - a moduł ten celowo nigdy nie zapisuje
    treści książki do pliku ``.easyreader`` (patrz docstring modułu oraz
    ocena ryzyka przy `MIN_WORDS_FOR_MINHASH` powyżej)."""
    shingles = _shingles(text, size=shingle_size)
    if not shingles:
        return []
    signature = []
    for seed in range(num_hashes):
        best = min(
            int(hashlib.sha256(f"{seed}:{shingle}".encode("utf-8")).hexdigest()[:12], 16)
            for shingle in shingles
        )
        signature.append(best)
    return signature


def minhash_similarity(signature_a: list[int], signature_b: list[int]) -> float:
    """Szacowane podobieństwo Jaccarda dwóch odcisków MinHash (0.0-1.0)."""
    if not signature_a or not signature_b or len(signature_a) != len(signature_b):
        return 0.0
    equal = sum(1 for a, b in zip(signature_a, signature_b) if a == b)
    return equal / len(signature_a)


def find_matching_block(
    matches: list[re.Match[str]],
    target: dict[str, Any],
    *,
    similarity_threshold: float = SIMILARITY_THRESHOLD,
    search_window: int = SIMILARITY_SEARCH_WINDOW,
    ambiguity_margin: float = AMBIGUITY_MARGIN,
) -> tuple[int | None, str]:
    """Odnajduje indeks bloku najlepiej odpowiadającego zapisanej kotwicy `target`.

    Szuka kolejno (pierwsze trafienie wygrywa):
      1. dokładny skrót surowego HTML (`block_sha256`) - przetrwa tylko
         całkowicie niezmieniony blok,
      2. dokładny skrót znormalizowanego tekstu (`block_text_sha256`) -
         przetrwa zmiany samych znaczników HTML,
      3. podobieństwo tekstu (odcisk MinHash, `block_minhash`, jeśli obecny
         w notatce - patrz `MIN_WORDS_FOR_MINHASH`) w oknie wokół zapisanego
         `raw_index` - przetrwa drobne korekty OCR (np. usunięcie podziału
         wyrazu albo numeru strony) BEZ przechowywania w pliku notatek
         fragmentu oryginalnego tekstu. Wymaga WYRAŹNEJ przewagi nad drugim
         najlepszym kandydatem (patrz `AMBIGUITY_MARGIN`) - w przeciwnym
         razie dopasowanie jest niepewne (powtarzające się akapity).
      4. (WYŁĄCZNIE INFORMACYJNIE) najbliższe dopasowanie po samym
         `raw_index`, bez żadnego potwierdzenia treścią. Zwracane, żeby
         diagnostyka/raport mogły pokazać "co by było, gdyby", ale
         `is_confident_tier()` dla tego poziomu zwraca False - punkt 1
         zgłoszenia wymaga, by taka adnotacja NIGDY nie została faktycznie
         zastosowana (pominięcie + widoczność w raporcie zamiast ryzyka
         wstawienia w złe miejsce).

    Zwraca (indeks_bloku | None, nazwa_zastosowanego_poziomu).
    """
    if not matches:
        return None, "brak"

    raw_index = target.get("raw_index")
    raw_index = int(raw_index) if isinstance(raw_index, (int, float)) else -1
    expected_hash = str(target.get("block_sha256", ""))
    expected_text_hash = str(target.get("block_text_sha256", ""))
    expected_minhash = target.get("block_minhash")
    expected_minhash = expected_minhash if isinstance(expected_minhash, list) and expected_minhash else None

    def _distance_sorted(indices: list[int]) -> list[int]:
        if raw_index < 0:
            return indices
        return sorted(indices, key=lambda i: abs(i - raw_index))

    # 1) Dokładny skrót surowego HTML - dowolne miejsce w dokumencie.
    if expected_hash:
        candidates = [i for i, m in enumerate(matches) if block_sha256(m.group(0)) == expected_hash]
        if candidates:
            return _distance_sorted(candidates)[0], "dokładny HTML"

    # 2) Dokładny skrót znormalizowanego tekstu - dowolne miejsce w dokumencie.
    if expected_text_hash:
        candidates = [
            i for i, m in enumerate(matches) if block_text_sha256(m.group(0)) == expected_text_hash
        ]
        if candidates:
            return _distance_sorted(candidates)[0], "znormalizowany tekst"

    # 3) Podobieństwo tekstu (MinHash) w oknie wokół zapisanego indeksu -
    # z ochroną przed niejednoznacznością przy powtarzających się akapitach.
    if expected_minhash and raw_index >= 0:
        lo = max(0, raw_index - search_window)
        hi = min(len(matches), raw_index + search_window + 1)
        scored: list[tuple[float, int]] = []
        for i in range(lo, hi):
            candidate_signature = minhash_signature(normalize_block_text(matches[i].group(0)))
            ratio = minhash_similarity(expected_minhash, candidate_signature)
            scored.append((ratio, i))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        if scored:
            best_ratio, best_index = scored[0]
            second_ratio = scored[1][0] if len(scored) > 1 else 0.0
            unambiguous = (best_ratio - second_ratio) >= ambiguity_margin
            if best_ratio >= similarity_threshold and unambiguous:
                return best_index, f"podobieństwo tekstu ({best_ratio:.0%})"
            if best_ratio >= similarity_threshold and not unambiguous:
                logger.info(
                    "Pominięto niejednoznaczne dopasowanie przez podobieństwo "
                    "(najlepszy=%.2f, drugi=%.2f) - prawdopodobnie powtarzający się akapit.",
                    best_ratio, second_ratio,
                )

    # 4) Sąsiednie/najbliższe dopasowanie - WYŁĄCZNIE informacyjne (patrz
    # `is_confident_tier` - ten poziom NIGDY nie jest traktowany jako pewny;
    # punkt 1 zgłoszenia zabrania wstawiania adnotacji na tej podstawie).
    if 0 <= raw_index < len(matches):
        return raw_index, "niepewne: tylko indeks (bez potwierdzenia treści)"
    if matches:
        clamped = max(0, min(raw_index if raw_index >= 0 else 0, len(matches) - 1))
        return clamped, "niepewne: indeks poza zakresem"

    return None, "brak"


def _target_metadata(raw_html: str, raw_index: int) -> dict[str, Any]:
    """Buduje metadane kotwicy zapisywane przy tworzeniu adnotacji - obejmuje
    skróty (dla trybu ścisłego) oraz - TYLKO dla wystarczająco długich bloków
    (patrz `MIN_WORDS_FOR_MINHASH` i ocena ryzyka powyżej) - nieodwracalny
    odcisk MinHash (dla dopasowania przez podobieństwo, patrz
    `find_matching_block`). Odcisk MinHash NIE pozwala odtworzyć
    oryginalnego tekstu - w przeciwieństwie do przechowywania fragmentu
    wprost, co złamałoby zasadę modułu, że plik ``.easyreader`` nigdy nie
    zawiera treści książki."""
    normalized = normalize_block_text(raw_html)
    metadata: dict[str, Any] = {
        "raw_index": raw_index,
        "block_sha256": block_sha256(raw_html),
        "block_text_sha256": block_text_sha256(raw_html),
    }
    if _word_count(normalized) >= MIN_WORDS_FOR_MINHASH:
        metadata["block_minhash"] = minhash_signature(normalized)
    return metadata



def _safe_target(root: Path, section: str) -> Path:
    pure = PurePosixPath(section)
    if pure.is_absolute() or ".." in pure.parts:
        raise EasyReaderAnnotationError(f"Niedozwolona ścieżka rozdziału: {section}")
    target = (root / Path(*pure.parts)).resolve()
    root_resolved = root.resolve()
    if root_resolved != target and root_resolved not in target.parents:
        raise EasyReaderAnnotationError(f"Ścieżka rozdziału wychodzi poza książkę: {section}")
    return target


@dataclass
class ApplyReport:
    """Wynik nałożenia notatek na rozpakowaną książkę (punkt 1/6 zgłoszenia)."""

    applied: int = 0
    skipped: list[dict[str, Any]] = field(default_factory=list)
    tiers_used: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.applied + len(self.skipped)

    def as_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "skipped": list(self.skipped),
            "tiers_used": dict(self.tiers_used),
            "total": self.total,
        }


def apply_text_correction(raw_html: str, correction: dict[str, Any]) -> str | None:
    """Poprawka drobnego błędu OCR (np. samotny numer strony, przypadkowy
    znak, "byty" zamiast "były") WEWNĄTRZ bloku - bez dotykania pliku
    źródłowego EPUB-a (patrz drugi punkt logu z 2026-08-12: "warstwa korekt
    wyświetlania... która nie zmienia pliku książki").

    Zwraca poprawiony HTML bloku, albo `None`, jeśli `correction["find"]`:
      - jest puste,
      - nie występuje w bloku wcale,
      - występuje WIĘCEJ NIŻ RAZ (niejednoznaczne - nie zgadujemy, które
        wystąpienie poprawić; to ta sama dyscyplina co przy dopasowaniu
        bloków - punkt 1: brak pewności = pominięcie, nigdy zgadywanie).
    """
    find = str(correction.get("find", ""))
    replace = str(correction.get("replace", ""))
    if not find:
        return None
    occurrences = raw_html.count(find)
    if occurrences != 1:
        return None
    return raw_html.replace(find, replace, 1)


def _apply_document_object(document: dict[str, Any], extracted_root: str | Path) -> ApplyReport:
    """Nakłada notatki z JUŻ WCZYTANEGO (i - jeśli trzeba - zweryfikowanego
    przez wywołującego) słownika `document` na rozpakowaną książkę.

    Obsługuje dwa typy adnotacji (pole `type`, domyślnie `"opracowanie"`):
      - `"opracowanie"` (domyślny) - komentarz wstawiany PO bloku (istniejące
        zachowanie: przekład, prosty język, objaśnienia, notatka czytelnika);
      - `"korekta_ocr"` - drobna poprawka WEWNĄTRZ bloku (patrz
        `apply_text_correction`), np. usunięcie przypadkowego znaku.

    Punkt 1 zgłoszenia: adnotację (dowolnego typu) wolno faktycznie
    zastosować TYLKO gdy `find_matching_block` zwróciło pewny poziom
    dopasowania (`is_confident_tier`) ORAZ (dla korekt) tekst do poprawienia
    faktycznie i jednoznacznie występuje w tym bloku. Niedopasowane/niepewne
    adnotacje są POMIJANE (nigdy nie trafiają do niewłaściwego akapitu ani
    nie poprawiają niewłaściwego tekstu) i lądują w `ApplyReport.skipped`
    z podanym powodem, zamiast przerywać całą operację.

    Wydzielone z `apply_document_to_extracted`, żeby `commit_relink` mogło
    "na sucho" wypróbować kandydata na nowe powiązanie (punkt 4: transakcyjny
    relink) bez zapisywania czegokolwiek ani na dysku EPUB-a, ani w pliku
    notatek, dopóki wynik nie zostanie potwierdzony.
    """
    root = Path(extracted_root)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for annotation in document.get("annotations", []):
        target = annotation.get("target") or {}
        section = str(target.get("section", ""))
        if not section:
            raise EasyReaderAnnotationError("Adnotacja nie wskazuje rozdziału.")
        grouped.setdefault(section, []).append(annotation)

    report = ApplyReport()
    for section, annotations in grouped.items():
        chapter = _safe_target(root, section)
        if not chapter.is_file():
            raise EasyReaderAnnotationError(f"Nie znaleziono rozdziału wskazanego w notatkach: {section}")
        text = chapter.read_text(encoding="utf-8", errors="replace")
        matches = list(BLOCK_RE.finditer(text))

        # Każdy wpis: (pozycja_startowa_do_sortowania, start, end, nowy_tekst).
        # Komentarz to wstawienie o zerowej szerokości w match.end(); korekta
        # to zastąpienie CAŁEGO zakresu bloku jego poprawioną wersją.
        prepared: list[tuple[int, int, int, str]] = []
        for annotation in annotations:
            target = annotation.get("target") or {}
            block_index, tier = find_matching_block(matches, target)
            annotation_id = str(annotation.get("id", "?"))
            if block_index is None or not is_confident_tier(tier):
                logger.info(
                    "Pominięto adnotację %s w rozdziale %s - dopasowanie niepewne (%s).",
                    annotation_id, section, tier,
                )
                report.skipped.append({"id": annotation_id, "section": section, "reason": tier})
                continue

            kind = str(annotation.get("type") or "opracowanie")
            match = matches[block_index]

            if kind == "korekta_ocr":
                corrected = apply_text_correction(match.group(0), annotation.get("correction") or {})
                if corrected is None:
                    logger.info(
                        "Pominięto korektę OCR %s w rozdziale %s - tekst do poprawienia nie "
                        "występuje jednoznacznie w bloku.", annotation_id, section,
                    )
                    report.skipped.append(
                        {"id": annotation_id, "section": section, "reason": "korekta: tekst niejednoznaczny lub nieobecny"}
                    )
                    continue
                prepared.append((match.start(), match.start(), match.end(), corrected))
            else:
                prepared.append((match.end(), match.end(), match.end(), render_annotation(annotation)))

            if tier != "dokładny HTML":
                logger.info(
                    "Adnotacja %s dopasowana przez: %s (blok %d, rozdział %s).",
                    annotation_id, tier, block_index, section,
                )
            report.tiers_used[tier] = report.tiers_used.get(tier, 0) + 1

        # Stosujemy od końca dokumentu wstecz (malejąco po pozycji), aby
        # wcześniejsze (mniejsze) pozycje pozostałych edycji nie przesuwały
        # się po zastosowaniu kolejnych. Działa jednolicie dla wstawień
        # komentarzy (zerowa szerokość, start==end) i korekt OCR (cały
        # zakres bloku zastępowany jego poprawioną wersją).
        for _sort_key, start, end, new_text in sorted(prepared, key=lambda item: item[0], reverse=True):
            text = text[:start] + new_text + text[end:]
            report.applied += 1
        if prepared:
            if 'id="easyreader-reading-style"' not in text and re.search(r"</head\s*>", text, flags=re.I):
                text = re.sub(r"</head\s*>", READING_STYLE + "\n</head>", text, count=1, flags=re.I)
            chapter.write_text(text, encoding="utf-8")
    return report


def apply_document_to_extracted(
    annotation_path: str | Path,
    source_epub: str | Path,
    extracted_root: str | Path,
) -> ApplyReport:
    """Nakłada notatki na rozpakowaną, tymczasową kopię książki.

    Zwraca `ApplyReport` (liczba zastosowanych adnotacji + lista pominiętych
    z powodem) zamiast samej liczby - patrz punkt 1 zgłoszenia: adnotacje,
    których nie dało się pewnie umiejscowić, są pomijane i widoczne w
    raporcie, a NIE wstawiane na chybił trafił.
    """
    document = load_document(annotation_path)
    verify_source(document, source_epub)
    return _apply_document_object(document, extracted_root)


def dry_run_match_report(document: dict[str, Any], extracted_root: str | Path) -> dict[str, Any]:
    """Sprawdza (bez żadnego zapisu na dysk) ile adnotacji dałoby się
    pewnie umieścić w podanej, już rozpakowanej książce. Używane przy
    bezpiecznym ponownym powiązaniu (patrz `analyze_relink`), żeby ocenić
    zgodność struktury i kotwic tekstowych PRZED zaktualizowaniem powiązania.

    Korzysta z tej samej definicji "pewnego dopasowania" (`is_confident_tier`)
    co faktyczne nakładanie notatek (`_apply_document_object`), żeby raport
    zgodności nigdy nie obiecywał więcej, niż realne nałożenie później
    dostarczy.
    """
    root = Path(extracted_root)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for annotation in document.get("annotations", []):
        target = annotation.get("target") or {}
        section = str(target.get("section", ""))
        grouped.setdefault(section, []).append(annotation)

    total = 0
    matched = 0
    unmatched_ids: list[str] = []
    tiers_used: dict[str, int] = {}

    for section, annotations in grouped.items():
        try:
            chapter = _safe_target(root, section)
        except EasyReaderAnnotationError:
            chapter = None
        matches: list[re.Match[str]] = []
        if chapter is not None and chapter.is_file():
            text = chapter.read_text(encoding="utf-8", errors="replace")
            matches = list(BLOCK_RE.finditer(text))
        for annotation in annotations:
            total += 1
            target = annotation.get("target") or {}
            block_index, tier = find_matching_block(matches, target) if matches else (None, "brak sekcji")
            if block_index is not None and is_confident_tier(tier):
                matched += 1
                tiers_used[tier] = tiers_used.get(tier, 0) + 1
            else:
                unmatched_ids.append(str(annotation.get("id", "?")))

    return {
        "total": total,
        "matched": matched,
        "unmatched": len(unmatched_ids),
        "ratio": (matched / total) if total else 1.0,
        "unmatched_ids": unmatched_ids,
        "tiers_used": tiers_used,
    }


def title_similarity(document: dict[str, Any], new_book_title: str | None) -> float | None:
    """Podobieństwo starego i nowego tytułu (0.0-1.0), albo `None`, gdy
    porównanie nie ma sensu (brak jednego z tytułów) - odróżnione od 0.0,
    żeby brak metadanych nie był mylony z realnie niepasującym tytułem
    (patrz punkt 5: tytuł to DODATKOWY sygnał, a nie twardy wymóg, gdy go
    po prostu nie ma)."""
    old_title = str(document.get("book", {}).get("title", "")).strip()
    new_title = str(new_book_title or "").strip()
    if not old_title or not new_title:
        return None
    return difflib.SequenceMatcher(None, old_title.lower(), new_title.lower()).ratio()


# Punkt 5: progi rekomendacji relinku. Sam procent dopasowanych adnotacji
# NIE WYSTARCZA - wymagamy też co najmniej jednego PEWNEGO dopasowania i,
# jeśli oba tytuły są znane, ich rozsądnej zgodności. Przy zerowej liczbie
# adnotacji (`total == 0`) relinku NIGDY nie rekomendujemy automatycznie -
# nie ma czym potwierdzić, że to ta sama książka.
MIN_MATCH_RATIO_FOR_RECOMMENDATION = 0.7
MIN_TITLE_SIMILARITY_FOR_RECOMMENDATION = 0.5


def analyze_relink(
    annotation_path: str | Path,
    new_source_epub: str | Path,
    extracted_root: str | Path,
    *,
    new_book_title: str | None = None,
) -> dict[str, Any]:
    """Ocena zgodności PRZED bezpiecznym ponownym powiązaniem pliku notatek
    z nowym EPUB-em - niczego nie modyfikuje na dysku. Warstwa UI powinna
    pokazać ten raport użytkownikowi (patrz punkt 6: liczba dopasowań
    pewnych/niedopasowanych, wykorzystane poziomy, ostrzeżenie o zmianie
    powiązania) i wywołać `commit_relink` DOPIERO po jego wyraźnym
    potwierdzeniu (patrz punkt 3/4 zgłoszenia)."""
    document = load_document(annotation_path)  # celowo bez verify_source() - o to właśnie chodzi w relinku
    report = dry_run_match_report(document, extracted_root)
    title_sim = title_similarity(document, new_book_title)
    report["title_similarity"] = title_sim
    report["already_matches"] = matches_source(document, new_source_epub)
    report["old_title"] = str(document.get("book", {}).get("title", ""))

    # Tytuł jest sygnałem DODATKOWYM: jeśli nie da się go porównać (None),
    # nie blokujemy rekomendacji z tego powodu; jeśli da się porównać,
    # musi przekroczyć rozsądny próg.
    title_ok = title_sim is None or title_sim >= MIN_TITLE_SIMILARITY_FOR_RECOMMENDATION

    report["recommended"] = bool(
        report["already_matches"]
        or (
            report["total"] > 0
            and report["matched"] > 0
            and report["ratio"] >= MIN_MATCH_RATIO_FOR_RECOMMENDATION
            and title_ok
        )
    )
    if report["total"] == 0 and not report["already_matches"]:
        report["recommendation_reason"] = "Plik notatek nie ma żadnych adnotacji - nie ma czym potwierdzić zgodności."
    elif not title_ok:
        report["recommendation_reason"] = "Tytuły książek znacząco się różnią."
    elif report["matched"] == 0:
        report["recommendation_reason"] = "Żadna adnotacja nie dopasowała się pewnie do nowej wersji."
    elif report["ratio"] < MIN_MATCH_RATIO_FOR_RECOMMENDATION and not report["already_matches"]:
        report["recommendation_reason"] = f"Dopasowano tylko {report['ratio']:.0%} adnotacji."
    else:
        report["recommendation_reason"] = "Struktura książki i tytuł wyglądają na zgodne."
    return report


def backup_annotation_file(annotation_path: str | Path) -> Path:
    """Punkt 3: tworzy kopię bezpieczeństwa pliku `.easyreader` PRZED
    jakąkolwiek ryzykowną operacją (tu: relink). Kopia trafia obok
    oryginału, z sygnaturą czasową w nazwie, żeby wielokrotne relinki nie
    nadpisywały się nawzajem."""
    source = Path(annotation_path)
    if not source.is_file():
        raise EasyReaderAnnotationError(f"Nie można utworzyć kopii zapasowej - plik nie istnieje: {source}")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = source.with_name(f"{source.stem}.backup-{timestamp}{source.suffix}")
    counter = 1
    while backup_path.exists():
        backup_path = source.with_name(f"{source.stem}.backup-{timestamp}-{counter}{source.suffix}")
        counter += 1
    backup_path.write_bytes(source.read_bytes())
    return backup_path


def commit_relink(
    annotation_path: str | Path,
    new_source_epub: str | Path,
    extracted_root: str | Path,
    *,
    new_title: str | None = None,
    require_successful_apply: bool = True,
) -> dict[str, Any]:
    """Aktualizuje powiązanie pliku notatek z NOWYM plikiem EPUB (sha256,
    rozmiar, nazwa) - wywoływać DOPIERO po potwierdzeniu użytkownika na
    podstawie raportu z `analyze_relink`.

    Punkt 4 (transakcyjnie): najpierw PRZYGOTOWUJEMY kandydata na nowy
    dokument w pamięci i PRÓBUJEMY go zastosować (na sucho, przez
    `_apply_document_object`) do już rozpakowanej nowej wersji książki.
    Zapis na dysk (i to dopiero PO kopii bezpieczeństwa - punkt 3) następuje
    WYŁĄCZNIE jeśli ta próba się powiedzie. Jeśli `require_successful_apply`
    jest prawdziwe (domyślnie) i w dokumencie są adnotacje, ale żadna nie
    dopasowała się pewnie, operacja jest przerywana - oryginalny plik
    `.easyreader` NIE zostaje w żaden sposób zmieniony.

    Treść adnotacji i docelowe rozdziały pozostają bez zmian; dokładne
    miejsce w tekście i tak jest ustalane dynamicznie przy każdym
    otwarciu/przeładowaniu (patrz `find_matching_block`), więc dalsze
    drobne różnice między wydaniami są tolerowane automatycznie, a nie
    tylko w chwili relinku.
    """
    original_path = Path(annotation_path)
    document = load_document(original_path)

    candidate = copy.deepcopy(document)
    source = Path(new_source_epub).resolve()
    candidate["book"]["sha256"] = sha256_file(source)
    candidate["book"]["size"] = source.stat().st_size
    candidate["book"]["filename"] = source.name
    if new_title:
        candidate["book"]["title"] = new_title

    # KROK 1 - przygotuj i sprawdź wynik, BEZ dotykania czegokolwiek na dysku
    # poza rozpakowaną, tymczasową kopią podglądu (extracted_root), którą i
    # tak dostajemy już przygotowaną przez wywołującego.
    apply_report = _apply_document_object(candidate, extracted_root)
    if require_successful_apply and candidate.get("annotations") and apply_report.applied == 0:
        raise EasyReaderAnnotationError(
            "Bezpieczny relink przerwany: żadna adnotacja nie dopasowała się pewnie do nowej wersji "
            "książki. Oryginalny plik notatek NIE został zmieniony."
        )

    # KROK 2 - dopiero teraz, gdy wiemy że to zadziała: kopia bezpieczeństwa,
    # a potem właściwy zapis nowego powiązania.
    backup_path = backup_annotation_file(original_path)
    candidate.setdefault("relink_history", []).append(
        {"relinked_at": _utc_now(), "filename": source.name, "backup": str(backup_path)}
    )
    save_document(original_path, candidate)
    return {
        "document": candidate,
        "backup_path": str(backup_path),
        "apply_report": apply_report.as_dict(),
    }
