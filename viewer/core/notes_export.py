"""Eksport zewnętrznych notatek ``.easyreader`` do samodzielnego EPUB-a.

Eksport nie kopiuje treści książki źródłowej. Zawiera wyłącznie opracowania
użytkownika i AI oraz techniczne wskazanie miejsca, którego dotyczą.
"""

from __future__ import annotations

from datetime import datetime, timezone
import html
from pathlib import Path
import zipfile

from core.easyreader_annotations import load_document, render_annotation


class NotesExportError(Exception):
    """Nie udało się utworzyć samodzielnego EPUB-a z notatkami."""


def _xhtml_document(title: str, body: str) -> str:
    return f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="pl" lang="pl">
<head>
  <meta charset="utf-8"/>
  <title>{html.escape(title)}</title>
  <link rel="stylesheet" type="text/css" href="styles.css"/>
</head>
<body>
{body}
</body>
</html>
'''


def _notes_body(document: dict) -> str:
    book = document["book"]
    annotations = document["annotations"]
    title = str(book.get("title") or book.get("filename") or "Książka")
    parts = [
        f"<h1>Notatki do: {html.escape(title)}</h1>",
        "<p class=\"notice\">Ten plik zawiera wyłącznie notatki i objaśnienia. "
        "Nie zawiera tekstu książki źródłowej.</p>",
    ]
    if not annotations:
        parts.append("<p>Brak zapisanych notatek.</p>")
        return "\n".join(parts)

    for number, annotation in enumerate(annotations, start=1):
        target = annotation.get("target") or {}
        section = str(target.get("section", ""))
        raw_index = target.get("raw_index")
        location = section
        if raw_index is not None:
            location += f", fragment {int(raw_index) + 1}"
        parts.append(f'<article id="note-{number}">')
        parts.append(f"<h2>Notatka {number}</h2>")
        if location:
            parts.append(f'<p class="location">Miejsce w książce: {html.escape(location)}</p>')
        parts.append(render_annotation(annotation))
        parts.append("</article>")
    return "\n".join(parts)


def export_notes_epub(annotation_path: str | Path, destination: str | Path) -> Path:
    """Tworzy EPUB zawierający tylko notatki z pliku ``.easyreader``."""
    source = Path(annotation_path)
    output = Path(destination)
    if output.suffix.lower() != ".epub":
        output = output.with_suffix(".epub")
    if source.resolve() == output.resolve():
        raise NotesExportError("Plik eksportu nie może zastąpić pliku .easyreader.")

    document = load_document(source)
    title = str(document["book"].get("title") or "Notatki")
    notes_title = f"Notatki do: {title}"
    identifier = "urn:easyreader:" + str(document["book"].get("sha256", "notes"))
    modified = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    container = '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
'''
    package = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id" xml:lang="pl">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">{html.escape(identifier)}</dc:identifier>
    <dc:title>{html.escape(notes_title)}</dc:title>
    <dc:language>pl</dc:language>
    <dc:creator>Czytelnicza Brzytwa Ockhama</dc:creator>
    <meta property="dcterms:modified">{modified}</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="notes" href="notes.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="styles.css" media-type="text/css"/>
  </manifest>
  <spine><itemref idref="notes"/></spine>
</package>
'''
    nav = _xhtml_document(
        notes_title,
        '<nav epub:type="toc" xmlns:epub="http://www.idpf.org/2007/ops"><h1>Spis treści</h1>'
        '<ol><li><a href="notes.xhtml">Notatki</a></li></ol></nav>',
    )
    notes = _xhtml_document(notes_title, _notes_body(document))
    styles = '''body { max-width: 44em; margin: 2em auto; padding: 0 1.2em; font-family: serif; line-height: 1.55; }
h1, h2, h4 { line-height: 1.2; }
article { margin: 2.4em 0; padding-top: 1em; border-top: 1px solid #aaa; }
.notice { padding: .8em 1em; background: #f2f4f6; }
.location { color: #666; font-size: .9em; }
.easyreader-opracowanie { margin: 1em 0; padding-left: 1em; border-left: .3em solid #4f7396; }
.easyreader-opracowanie section { margin: 1em 0; }
'''

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr("EPUB/package.opf", package, compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr("EPUB/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr("EPUB/notes.xhtml", notes, compress_type=zipfile.ZIP_DEFLATED)
            archive.writestr("EPUB/styles.css", styles, compress_type=zipfile.ZIP_DEFLATED)
        temporary.replace(output)
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        if isinstance(exc, NotesExportError):
            raise
        raise NotesExportError(f"Nie udało się utworzyć EPUB-a z notatkami: {exc}") from exc
    return output
