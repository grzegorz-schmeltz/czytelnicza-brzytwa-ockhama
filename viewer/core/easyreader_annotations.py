"""Przenośne notatki ``.easyreader`` nakładane na tymczasowy podgląd EPUB-a.

Plik notatek nie zawiera pełnej treści książki. Przechowuje cyfrowy odcisk
oryginału, pozycje opracowanych bloków oraz treść komentarzy. Oryginalny EPUB
jest zawsze otwierany tylko do odczytu, a adnotacje trafiają wyłącznie do jego
rozpakowanej kopii w katalogu tymczasowym viewera.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any


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


def block_text_sha256(raw_html: str) -> str:
    """Odcisk widocznej treści, odporny na techniczne zmiany znaczników HTML."""
    without_tags = re.sub(r"<[^>]+>", " ", raw_html)
    visible_text = " ".join(html.unescape(without_tags).split())
    return hashlib.sha256(visible_text.encode("utf-8")).hexdigest()


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


def _safe_target(root: Path, section: str) -> Path:
    pure = PurePosixPath(section)
    if pure.is_absolute() or ".." in pure.parts:
        raise EasyReaderAnnotationError(f"Niedozwolona ścieżka rozdziału: {section}")
    target = (root / Path(*pure.parts)).resolve()
    root_resolved = root.resolve()
    if root_resolved != target and root_resolved not in target.parents:
        raise EasyReaderAnnotationError(f"Ścieżka rozdziału wychodzi poza książkę: {section}")
    return target


def apply_document_to_extracted(
    annotation_path: str | Path,
    source_epub: str | Path,
    extracted_root: str | Path,
) -> int:
    """Nakłada notatki na rozpakowaną, tymczasową kopię książki."""
    document = load_document(annotation_path)
    verify_source(document, source_epub)
    root = Path(extracted_root)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for annotation in document["annotations"]:
        target = annotation.get("target") or {}
        section = str(target.get("section", ""))
        if not section:
            raise EasyReaderAnnotationError("Adnotacja nie wskazuje rozdziału.")
        grouped.setdefault(section, []).append(annotation)

    applied = 0
    for section, annotations in grouped.items():
        chapter = _safe_target(root, section)
        if not chapter.is_file():
            raise EasyReaderAnnotationError(f"Nie znaleziono rozdziału wskazanego w notatkach: {section}")
        text = chapter.read_text(encoding="utf-8", errors="replace")
        # Wstawiamy od końca dokumentu, aby wcześniejsze indeksy bloków nie
        # przesuwały się po dodaniu kolejnych komentarzy.
        prepared: list[tuple[int, dict[str, Any], re.Match[str]]] = []
        matches = list(BLOCK_RE.finditer(text))
        for annotation in annotations:
            target = annotation.get("target") or {}
            raw_index = int(target.get("raw_index", -1))
            if raw_index < 0 or raw_index >= len(matches):
                raise EasyReaderAnnotationError(
                    f"Nie można odnaleźć miejsca adnotacji {annotation.get('id', '?')} w rozdziale {section}."
                )
            match = matches[raw_index]
            expected_hash = str(target.get("block_sha256", ""))
            expected_text_hash = str(target.get("block_text_sha256", ""))
            raw_matches = not expected_hash or block_sha256(match.group(0)) == expected_hash
            text_matches = (
                bool(expected_text_hash)
                and block_text_sha256(match.group(0)) == expected_text_hash
            )
            if not raw_matches and not text_matches:
                raise EasyReaderAnnotationError(
                    f"Treść przy adnotacji {annotation.get('id', '?')} uległa zmianie."
                )
            prepared.append((match.end(), annotation, match))
        for position, annotation, _match in sorted(prepared, key=lambda item: item[0], reverse=True):
            text = text[:position] + render_annotation(annotation) + text[position:]
            applied += 1
        if 'id="easyreader-reading-style"' not in text and re.search(r"</head\s*>", text, flags=re.I):
            text = re.sub(r"</head\s*>", READING_STYLE + "\n</head>", text, count=1, flags=re.I)
        chapter.write_text(text, encoding="utf-8")
    return applied
