from __future__ import annotations

import argparse
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
from tempfile import NamedTemporaryFile
import unicodedata
from urllib.parse import unquote
import zipfile
from xml.etree import ElementTree as ET


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIEWER_ROOT = PROJECT_ROOT / "viewer"
if str(VIEWER_ROOT) not in sys.path:
    sys.path.insert(0, str(VIEWER_ROOT))

from core.easyreader_annotations import (  # noqa: E402
    append_annotation,
    block_sha256,
    block_text_sha256,
    create_document,
    load_document,
    render_annotation,
)

BOOKS_ROOT = Path(
    os.environ.get("EASYREADER_BOOKS_ROOT", PROJECT_ROOT / "books")
).expanduser()
DEFAULT_PROFILE = Path(
    os.environ.get("EASYREADER_DEFAULT_PROFILE", PROJECT_ROOT / "profiles" / "default.md")
).expanduser()
STATE_ROOT = Path(
    os.environ.get("EASYREADER_STATE_ROOT", PROJECT_ROOT / ".easyreader")
).expanduser()
ACTIVE_FILE = Path(
    os.environ.get("EASYREADER_ACTIVE_FILE", STATE_ROOT / "active_book.txt")
).expanduser()
READER_LAUNCHER = Path(
    os.environ.get("EASYREADER_READER_LAUNCHER", PROJECT_ROOT / "scripts" / "open_reader.bat")
).expanduser()

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


def fail(message: str) -> None:
    raise RuntimeError(message)


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return value or "ksiazka"


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def load_state(book_dir: Path) -> dict:
    path = book_dir / "postep.json"
    if not path.exists():
        fail(f"Brak pliku postępu: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_book(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    if not ACTIVE_FILE.exists():
        fail("Nie wybrano aktywnej książki. Najpierw użyj init albo activate.")
    active = ACTIVE_FILE.read_text(encoding="utf-8").strip()
    if not active:
        fail(f"Plik aktywnej książki jest pusty: {ACTIVE_FILE}")
    return Path(active).resolve()


def find_calibre() -> Path:
    candidates = [
        shutil.which("ebook-convert"),
        r"C:\Program Files\Calibre2\ebook-convert.exe",
        r"C:\Program Files (x86)\Calibre2\ebook-convert.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    fail("Nie znaleziono programu ebook-convert z pakietu Calibre.")


def zip_replace(epub: Path, replacements: dict[str, bytes]) -> None:
    with zipfile.ZipFile(epub, "r") as source:
        if "mimetype" not in source.namelist():
            fail("To archiwum nie zawiera pliku mimetype.")
        with NamedTemporaryFile(dir=epub.parent, suffix=".epub", delete=False) as fh:
            temporary = Path(fh.name)
        with zipfile.ZipFile(temporary, "w") as target:
            target.writestr(
                "mimetype", source.read("mimetype"), compress_type=zipfile.ZIP_STORED
            )
            for info in source.infolist():
                if info.filename == "mimetype":
                    continue
                target.writestr(
                    info, replacements.get(info.filename, source.read(info.filename))
                )
    temporary.replace(epub)


def rootfile_and_spine(epub: Path) -> tuple[str, list[str]]:
    with zipfile.ZipFile(epub) as archive:
        container = ET.fromstring(archive.read("META-INF/container.xml"))
        rootfile = next(
            (
                el.attrib.get("full-path")
                for el in container.iter()
                if el.tag.rsplit("}", 1)[-1] == "rootfile"
            ),
            None,
        )
        if not rootfile:
            fail("Nie znaleziono pliku OPF w META-INF/container.xml.")
        opf = ET.fromstring(archive.read(rootfile))
        manifest = {}
        for element in opf.iter():
            if element.tag.rsplit("}", 1)[-1] == "item":
                item_id = element.attrib.get("id")
                href = element.attrib.get("href")
                media = element.attrib.get("media-type", "")
                if item_id and href:
                    manifest[item_id] = (href, media)
        opf_dir = PurePosixPath(rootfile).parent
        spine = []
        for element in opf.iter():
            if element.tag.rsplit("}", 1)[-1] != "itemref":
                continue
            if element.attrib.get("linear", "yes").lower() == "no":
                continue
            item = manifest.get(element.attrib.get("idref", ""))
            if not item:
                continue
            href, media = item
            if "html" not in media and not href.lower().endswith((".xhtml", ".html", ".htm")):
                continue
            name = str(opf_dir / PurePosixPath(unquote(href)))
            if name not in archive.namelist():
                encoded_name = str(opf_dir / PurePosixPath(href))
                if encoded_name in archive.namelist():
                    name = encoded_name
                else:
                    fail(f"Brak dokumentu spine w EPUB-ie: {name}")
            spine.append(name)
        if not spine:
            fail("EPUB nie zawiera dokumentów do czytania w spine.")
        return rootfile, spine


def validate_epub(epub: Path) -> list[str]:
    errors: list[str] = []
    try:
        with zipfile.ZipFile(epub) as archive:
            infos = archive.infolist()
            if not infos or infos[0].filename != "mimetype":
                errors.append("Plik mimetype nie jest pierwszy.")
            elif infos[0].compress_type != zipfile.ZIP_STORED:
                errors.append("Plik mimetype jest skompresowany.")
            if archive.read("mimetype") != b"application/epub+zip":
                errors.append("Nieprawidłowa treść pliku mimetype.")
            if broken := archive.testzip():
                errors.append(f"Uszkodzony element ZIP: {broken}")
            rootfile, spine = rootfile_and_spine(epub)
            for name in [rootfile, *spine]:
                try:
                    ET.fromstring(archive.read(name))
                except Exception as exc:
                    errors.append(f"{name}: błąd XML: {exc}")
    except Exception as exc:
        errors.append(str(exc))
    return errors


def inject_reading_style(epub: Path) -> None:
    _, spine = rootfile_and_spine(epub)
    replacements = {}
    with zipfile.ZipFile(epub) as archive:
        for name in spine:
            text = archive.read(name).decode("utf-8", errors="replace")
            if 'id="easyreader-reading-style"' in text:
                continue
            if not re.search(r"</head\s*>", text, flags=re.I):
                continue
            text = re.sub(
                r"</head\s*>", READING_STYLE + "\n</head>", text, count=1, flags=re.I
            )
            replacements[name] = text.encode("utf-8")
    if replacements:
        zip_replace(epub, replacements)


def build_epub_from_text(source: Path, destination: Path) -> None:
    data = source.read_bytes()
    try:
        raw = data.decode("utf-8")
    except UnicodeDecodeError:
        # Starsze polskie e-booki TXT najczęściej używają strony kodowej
        # Windows-1250. Dekodowanie z errors="replace" niszczyło znaki
        # bezpowrotnie już na etapie tworzenia kopii roboczej.
        raw = data.decode("cp1250")
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip()]
    body = "\n".join(
        f"<p>{html.escape(p).replace(chr(10), '<br/>')}</p>" for p in paragraphs
    )
    title = html.escape(source.stem)
    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">easyreader-{slugify(source.stem)}</dc:identifier>
    <dc:title>{title}</dc:title><dc:language>pl</dc:language>
    <meta property="dcterms:modified">{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}</meta>
  </metadata>
  <manifest><item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>
  <spine><itemref idref="chapter"/></spine>
</package>"""
    chapter = f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>{title}</title></head>
<body><h1>{title}</h1>{body}</body></html>"""
    with zipfile.ZipFile(destination, "w") as archive:
        archive.writestr("mimetype", b"application/epub+zip", zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container, zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/content.opf", opf, zipfile.ZIP_DEFLATED)
        archive.writestr("OEBPS/chapter.xhtml", chapter, zipfile.ZIP_DEFLATED)


BLOCK_RE = re.compile(
    r"<(?P<tag>h[1-6]|p|blockquote|li)\b[^>]*>.*?</(?P=tag)\s*>",
    flags=re.I | re.S,
)


def text_from_html(raw: str) -> str:
    value = re.sub(r"<(script|style)\b.*?</\1\s*>", " ", raw, flags=re.I | re.S)
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value).replace("\u00a0", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def blocks(document: str) -> list[dict]:
    result = []
    for index, match in enumerate(BLOCK_RE.finditer(document)):
        raw = match.group(0)
        text = text_from_html(raw)
        if not text or re.fullmatch(r"[\d\s.\-–—]+", text):
            continue
        result.append(
            {
                "raw_index": index,
                "tag": match.group("tag").lower(),
                "raw_html": raw,
                "text": text,
            }
        )
    return result


def is_section_heading(item: dict) -> bool:
    """Rozpoznaj krótki śródtytuł, także gdy konwersja PDF zapisała go jako <p>."""
    if item.get("tag", "").lower().startswith("h"):
        return True
    text = str(item.get("text", "")).strip()
    if not text or len(text) > 80 or len(text.split()) > 12:
        return False
    if re.search(r"[.!?;:]$", text):
        return False
    words = re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿĀ-ž]+", text)
    connectors = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to"}
    return bool(words) and all(
        word.lower() in connectors or word[0].isupper() for word in words
    )


def command_init(args: argparse.Namespace) -> None:
    source = Path(args.source).expanduser().resolve()
    if not source.exists() or not source.is_file():
        fail(f"Nie znaleziono książki: {source}")
    if source.suffix.lower() not in {".epub", ".pdf", ".txt"}:
        fail("Na tym etapie obsługiwane są pliki EPUB, PDF i TXT.")
    name = slugify(args.name or source.stem)
    book_dir = BOOKS_ROOT / name
    if book_dir.exists() and any(book_dir.iterdir()) and not args.force:
        fail(f"Katalog książki już istnieje: {book_dir}")
    (book_dir / "temp" / "historia").mkdir(parents=True, exist_ok=True)

    if source.suffix.lower() == ".epub":
        source_epub = source
    elif source.suffix.lower() == ".pdf":
        calibre = find_calibre()
        print("Konwersja PDF do EPUB przez Calibre…")
        generated_dir = book_dir / "wygenerowane"
        generated_dir.mkdir(parents=True, exist_ok=True)
        source_epub = generated_dir / "zrodlo_znormalizowane.epub"
        calibre_config = book_dir / "temp" / "calibre-config"
        calibre_config.mkdir(parents=True, exist_ok=True)
        calibre_env = os.environ.copy()
        calibre_env["CALIBRE_CONFIG_DIRECTORY"] = str(calibre_config)
        subprocess.run(
            [str(calibre), str(source), str(source_epub)],
            check=True,
            cwd=book_dir,
            env=calibre_env,
        )
    else:
        generated_dir = book_dir / "wygenerowane"
        generated_dir.mkdir(parents=True, exist_ok=True)
        source_epub = generated_dir / "zrodlo_znormalizowane.epub"
        build_epub_from_text(source, source_epub)

    errors = validate_epub(source_epub)
    if errors:
        fail("Nie udało się utworzyć poprawnego EPUB-a:\n" + "\n".join(errors))

    annotation_file = book_dir / f"{name}.easyreader"
    create_document(annotation_file, source_epub, title=source.stem)
    _, spine = rootfile_and_spine(source_epub)
    state = {
        "format": 2,
        "name": name,
        "source_original": str(source),
        "source_epub": str(source_epub),
        "annotations_file": str(annotation_file),
        "spine": spine,
        "cursor": {"section": 0, "raw_block": 0},
        "pending": None,
        "applied": 0,
        "skipped": 0,
        "history": [],
    }
    save_json(book_dir / "postep.json", state)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    ACTIVE_FILE.write_text(str(book_dir), encoding="utf-8")
    shutil.copy2(DEFAULT_PROFILE, book_dir / "profil_czytania.md")
    (book_dir / "notatki_czytelnika.md").write_text(
        f"# Notatki do książki: {source.stem}\n\n", encoding="utf-8"
    )
    launcher = book_dir / "Otworz_w_Podgladzie.bat"
    launcher.write_text(
        "@echo off\n"
        f'call "{READER_LAUNCHER}" "{source_epub}" "{annotation_file}"\n',
        encoding="utf-8",
    )
    print(f"Katalog książki: {book_dir}")
    print(f"Oryginał otwierany tylko do odczytu: {source_epub}")
    print(f"Osobny plik notatek: {annotation_file}")


def command_next(args: argparse.Namespace) -> None:
    book_dir = resolve_book(args.book)
    state = load_state(book_dir)
    if state.get("pending"):
        print("Bieżący fragment czeka już na opracowanie:")
        print(book_dir / "temp" / "fragment_biezacy.txt")
        return
    source_epub = Path(state["source_epub"])
    section_index = int(state["cursor"]["section"])
    raw_cursor = int(state["cursor"]["raw_block"])
    selected = []
    selected_section = None

    with zipfile.ZipFile(source_epub) as archive:
        while section_index < len(state["spine"]):
            section = state["spine"][section_index]
            document = archive.read(section).decode("utf-8", errors="replace")
            all_matches = list(BLOCK_RE.finditer(document))
            candidates = []
            for raw_index, match in enumerate(all_matches):
                if raw_index < raw_cursor:
                    continue
                raw = match.group(0)
                text = text_from_html(raw)
                if not text or re.fullmatch(r"[\d\s.\-–—]+", text):
                    continue
                candidates.append(
                    {
                        "raw_index": raw_index,
                        "tag": match.group("tag").lower(),
                        "raw_html": raw,
                        "text": text,
                    }
                )
            if candidates:
                selected_section = section
                total = 0
                for item in candidates:
                    # Jeden fragment ma odpowiadać jednemu naturalnemu ustępowi.
                    # Pierwszy śródtytuł należy do fragmentu, kolejny rozpoczyna następny.
                    if selected and is_section_heading(item):
                        break
                    if selected and total + len(item["text"]) > args.chars:
                        break
                    selected.append(item)
                    total += len(item["text"])
                    if total >= args.chars:
                        break
                break
            section_index += 1
            raw_cursor = 0

    if not selected or selected_section is None:
        print("Koniec książki — nie znaleziono następnego fragmentu.")
        return

    last = selected[-1]
    identical_before = sum(
        1 for item in selected if item["raw_html"] == last["raw_html"]
    )
    # Uwzględnij identyczne bloki występujące wcześniej w tym samym dokumencie.
    with zipfile.ZipFile(source_epub) as archive:
        document = archive.read(selected_section).decode("utf-8", errors="replace")
    all_raw = [m.group(0) for m in BLOCK_RE.finditer(document)]
    occurrence = sum(
        1
        for raw in all_raw[: last["raw_index"] + 1]
        if raw == last["raw_html"]
    )
    sequence = state["applied"] + state["skipped"] + 1
    fragment_id = f"fragment-{sequence:04d}"
    pending = {
        "id": fragment_id,
        "section": selected_section,
        "section_index": section_index,
        "blocks": selected,
        "anchor_raw_html": last["raw_html"],
        "anchor_occurrence": occurrence,
        "next_cursor": {
            "section": section_index,
            "raw_block": last["raw_index"] + 1,
        },
    }
    state["pending"] = pending
    save_json(book_dir / "postep.json", state)
    joined = "\n\n".join(item["text"] for item in selected)
    (book_dir / "temp" / "fragment_biezacy.txt").write_text(
        f"KSIĄŻKA: {state['name']}\n"
        f"FRAGMENT: {fragment_id}\n"
        f"DOKUMENT EPUB: {selected_section}\n\n{joined}\n",
        encoding="utf-8",
    )
    save_json(book_dir / "temp" / "fragment_biezacy.json", pending)
    template = {
        "fragment_id": fragment_id,
        "modernizacja": "",
        "prosty_jezyk": "",
        "objasnienia": [],
        "komentarz_ai": "",
        "notatka_czytelnika": "",
    }
    save_json(book_dir / "temp" / "opracowanie.json", template)
    print(book_dir / "temp" / "fragment_biezacy.txt")
    print(book_dir / "temp" / "opracowanie.json")


def nth_find(text: str, needle: str, occurrence: int) -> int:
    start = 0
    for _ in range(occurrence):
        found = text.find(needle, start)
        if found < 0:
            return -1
        start = found + len(needle)
    return found


def html_paragraphs(value: str) -> str:
    parts = [part.strip() for part in re.split(r"\n\s*\n", value) if part.strip()]
    return "".join(
        "<p>" + html.escape(part).replace("\n", "<br/>") + "</p>" for part in parts
    )


def build_annotation(data: dict, fragment_id: str) -> str:
    sections = []
    for key, title, css in [
        ("modernizacja", "Przekład lub uwspółcześnienie", "easyreader-modernizacja"),
        ("prosty_jezyk", "Prostym językiem", "easyreader-prosto"),
        ("komentarz_ai", "Komentarz AI", "easyreader-ai"),
        ("notatka_czytelnika", "Notatka czytelnika", "easyreader-notatka"),
    ]:
        value = str(data.get(key, "")).strip()
        if value:
            sections.append(
                f'<section class="{css}"><h4>{title}</h4>{html_paragraphs(value)}</section>'
            )
    explanations = data.get("objasnienia") or []
    if explanations:
        items = []
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
        fail("Plik opracowanie.json nie zawiera żadnego tekstu do dodania.")
    return (
        f'\n<aside class="easyreader-opracowanie" data-easyreader-id="{fragment_id}">'
        + "".join(sections)
        + "</aside>\n"
    )


def command_apply(args: argparse.Namespace) -> None:
    book_dir = resolve_book(args.book)
    state = load_state(book_dir)
    pending = state.get("pending")
    if not pending:
        fail("Nie ma bieżącego fragmentu do zastosowania.")
    data_path = Path(args.data).resolve() if args.data else book_dir / "temp" / "opracowanie.json"
    data = json.loads(data_path.read_text(encoding="utf-8"))
    if data.get("fragment_id") != pending["id"]:
        fail("Identyfikator opracowania nie pasuje do bieżącego fragmentu.")
    chosen = pending["blocks"][-1]
    next_cursor = pending["next_cursor"]
    if data.get("apply_through_raw_index") is not None:
        raw_index = int(data["apply_through_raw_index"])
        chosen = next(
            (item for item in pending["blocks"] if int(item["raw_index"]) == raw_index),
            None,
        )
        if not chosen:
            fail("Wybrana granica opracowania nie należy do bieżącego fragmentu.")
        next_cursor = {
            "section": pending["section_index"],
            "raw_block": raw_index + 1,
        }
    content = {
        key: data.get(key, [] if key == "objasnienia" else "")
        for key in (
            "modernizacja",
            "prosty_jezyk",
            "objasnienia",
            "komentarz_ai",
            "notatka_czytelnika",
        )
    }
    annotation_record = {
        "id": pending["id"],
        "target": {
            "section": pending["section"],
            "raw_index": int(chosen["raw_index"]),
            "block_sha256": block_sha256(chosen["raw_html"]),
            "block_text_sha256": block_text_sha256(chosen["raw_html"]),
        },
        "content": content,
    }
    # Walidujemy treść przed zapisem. render_annotation zgłosi czytelny błąd,
    # jeżeli wszystkie pola opracowania są puste.
    render_annotation(annotation_record)
    source_epub = Path(state["source_epub"])
    annotations_file = Path(state["annotations_file"])
    append_annotation(annotations_file, source_epub, annotation_record)
    history_dir = book_dir / "temp" / "historia"
    history_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(book_dir / "temp" / "fragment_biezacy.txt", history_dir / f'{pending["id"]}_oryginal.txt')
    shutil.copy2(data_path, history_dir / f'{pending["id"]}_opracowanie.json')
    state["cursor"] = next_cursor
    state["applied"] += 1
    state["history"].append(
        {"id": pending["id"], "status": "applied", "section": pending["section"]}
    )
    state["pending"] = None
    save_json(book_dir / "postep.json", state)
    print(f"Dodano {pending['id']} do pliku notatek: {annotations_file}")
    print("Oryginalny EPUB nie został zmieniony.")


def command_skip(args: argparse.Namespace) -> None:
    book_dir = resolve_book(args.book)
    state = load_state(book_dir)
    pending = state.get("pending")
    if not pending:
        fail("Nie ma bieżącego fragmentu do pominięcia.")
    state["cursor"] = pending["next_cursor"]
    state["skipped"] += 1
    state["history"].append(
        {"id": pending["id"], "status": "skipped", "section": pending["section"]}
    )
    state["pending"] = None
    save_json(book_dir / "postep.json", state)
    print(f"Pominięto {pending['id']}.")


def command_skip_section(args: argparse.Namespace) -> None:
    book_dir = resolve_book(args.book)
    state = load_state(book_dir)
    current_section = int(state["cursor"]["section"])
    pending = state.get("pending")
    if pending:
        current_section = int(pending["section_index"])
        state["skipped"] += 1
        state["history"].append(
            {
                "id": pending["id"],
                "status": "skipped-section",
                "section": pending["section"],
            }
        )
        state["pending"] = None
    state["cursor"] = {"section": current_section + 1, "raw_block": 0}
    save_json(book_dir / "postep.json", state)
    print(f"Przejście do dokumentu spine nr {current_section + 1}.")


def command_status(args: argparse.Namespace) -> None:
    book_dir = resolve_book(args.book)
    state = load_state(book_dir)
    result = {
        "name": state["name"],
        "source_epub": state["source_epub"],
        "annotations_file": state.get("annotations_file"),
        "cursor": state["cursor"],
        "applied": state["applied"],
        "skipped": state["skipped"],
        "pending": state["pending"]["id"] if state.get("pending") else None,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


def command_validate(args: argparse.Namespace) -> None:
    epub = Path(args.epub).resolve()
    errors = validate_epub(epub)
    if errors:
        fail("\n".join(errors))
    print(f"EPUB poprawny: {epub}")


def command_activate(args: argparse.Namespace) -> None:
    book_dir = Path(args.book).expanduser().resolve()
    load_state(book_dir)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    ACTIVE_FILE.write_text(str(book_dir), encoding="utf-8")
    print(f"Aktywna książka: {book_dir}")


def command_list(args: argparse.Namespace) -> None:
    active = resolve_book(None) if ACTIVE_FILE.exists() else None
    for path in sorted(BOOKS_ROOT.iterdir() if BOOKS_ROOT.exists() else []):
        if not (path / "postep.json").exists():
            continue
        marker = "*" if active and path.resolve() == active else " "
        print(f"{marker} {path}")


def command_migrate(args: argparse.Namespace) -> None:
    """Przenosi komentarze ze starej trwałej kopii EPUB do `.easyreader`.

    Stary EPUB i jego kopie bezpieczeństwa pozostają nietknięte jako punkt
    powrotu. Po udanej migracji dalsza praca korzysta już z oryginału oraz
    osobnego pliku notatek.
    """
    book_dir = resolve_book(args.book)
    state = load_state(book_dir)
    if int(state.get("format", 1)) >= 2 and state.get("annotations_file"):
        print(f"Książka korzysta już z pliku: {state['annotations_file']}")
        return
    source_epub = Path(state["source_epub"])
    working_epub = Path(state.get("working_epub", ""))
    if not source_epub.is_file() or not working_epub.is_file():
        fail("Migracja wymaga źródłowego EPUB-a i dotychczasowej kopii roboczej.")

    annotation_file = book_dir / f"{state['name']}.easyreader"
    if not annotation_file.exists():
        create_document(annotation_file, source_epub, title=state["name"])
    existing_ids = {str(item.get("id")) for item in load_document(annotation_file)["annotations"]}

    proposals = book_dir / "temp" / "historia"
    migrated = 0
    with zipfile.ZipFile(source_epub) as source_archive, zipfile.ZipFile(working_epub) as working_archive:
        for entry in state.get("history", []):
            if entry.get("status") != "applied":
                continue
            fragment_id = str(entry["id"])
            if fragment_id in existing_ids:
                continue
            section = str(entry["section"])
            proposal_path = proposals / f"{fragment_id}_opracowanie.json"
            if not proposal_path.is_file():
                fail(f"Brak zachowanego opracowania: {proposal_path}")
            data = json.loads(proposal_path.read_text(encoding="utf-8"))
            source_document = source_archive.read(section).decode("utf-8", errors="replace")
            working_document = working_archive.read(section).decode("utf-8", errors="replace")

            aside_pattern = re.compile(
                rf'<aside\b[^>]*data-easyreader-id=["\']{re.escape(fragment_id)}["\'][^>]*>.*?</aside\s*>',
                re.IGNORECASE | re.DOTALL,
            )
            aside_match = aside_pattern.search(working_document)
            if not aside_match:
                fail(f"Nie znaleziono {fragment_id} w starej kopii roboczej EPUB-a.")
            source_blocks = [match.group(0) for match in BLOCK_RE.finditer(source_document)]
            source_index_by_raw: dict[str, list[int]] = {}
            for index, raw in enumerate(source_blocks):
                source_index_by_raw.setdefault(raw, []).append(index)
            preceding = [match.group(0) for match in BLOCK_RE.finditer(working_document[: aside_match.start()])]
            anchor_raw = next((raw for raw in reversed(preceding) if raw in source_index_by_raw), None)
            if anchor_raw is None:
                fail(f"Nie udało się odtworzyć miejsca dla {fragment_id}.")
            raw_index = source_index_by_raw[anchor_raw][-1]
            content = {
                key: data.get(key, [] if key == "objasnienia" else "")
                for key in (
                    "modernizacja",
                    "prosty_jezyk",
                    "objasnienia",
                    "komentarz_ai",
                    "notatka_czytelnika",
                )
            }
            record = {
                "id": fragment_id,
                "target": {
                    "section": section,
                    "raw_index": raw_index,
                    "block_sha256": block_sha256(anchor_raw),
                    "block_text_sha256": block_text_sha256(anchor_raw),
                },
                "content": content,
            }
            render_annotation(record)
            append_annotation(annotation_file, source_epub, record)
            existing_ids.add(fragment_id)
            migrated += 1

    backup_state = book_dir / "postep_format1_backup.json"
    if not backup_state.exists():
        shutil.copy2(book_dir / "postep.json", backup_state)
    state["format"] = 2
    state["annotations_file"] = str(annotation_file)
    state["legacy_working_epub"] = state.pop("working_epub", str(working_epub))
    save_json(book_dir / "postep.json", state)
    launcher = book_dir / "Otworz_w_Podgladzie.bat"
    launcher.write_text(
        "@echo off\n"
        f'call "{READER_LAUNCHER}" "{source_epub}" "{annotation_file}"\n',
        encoding="utf-8",
    )
    print(f"Przeniesiono opracowania: {migrated}")
    print(f"Plik notatek: {annotation_file}")
    print(f"Kopia starego stanu: {backup_state}")
    print("Stary opracowany EPUB nie został usunięty.")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Czytelnicza Brzytwa Ockhama")
    sub = root.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Utwórz zewnętrzny plik notatek do książki")
    init.add_argument("source")
    init.add_argument("--name")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=command_init)
    nxt = sub.add_parser("next", help="Pobierz następny fragment")
    nxt.add_argument("book", nargs="?")
    nxt.add_argument("--chars", type=int, default=1600)
    nxt.set_defaults(func=command_next)
    apply = sub.add_parser("apply", help="Dodaj opracowanie do pliku .easyreader")
    apply.add_argument("book", nargs="?")
    apply.add_argument("--data")
    apply.set_defaults(func=command_apply)
    skip = sub.add_parser("skip", help="Pomiń bieżący fragment")
    skip.add_argument("book", nargs="?")
    skip.set_defaults(func=command_skip)
    skip_section = sub.add_parser(
        "skip-section", help="Pomiń resztę bieżącego dokumentu EPUB"
    )
    skip_section.add_argument("book", nargs="?")
    skip_section.set_defaults(func=command_skip_section)
    status = sub.add_parser("status", help="Pokaż postęp")
    status.add_argument("book", nargs="?")
    status.set_defaults(func=command_status)
    validate = sub.add_parser("validate", help="Sprawdź EPUB")
    validate.add_argument("epub")
    validate.set_defaults(func=command_validate)
    activate = sub.add_parser("activate", help="Ustaw aktywną książkę")
    activate.add_argument("book")
    activate.set_defaults(func=command_activate)
    listing = sub.add_parser("list", help="Pokaż rozpoczęte książki")
    listing.set_defaults(func=command_list)
    migrate = sub.add_parser("migrate", help="Przenieś starą kopię roboczą do pliku .easyreader")
    migrate.add_argument("book", nargs="?")
    migrate.set_defaults(func=command_migrate)
    return root


def main() -> int:
    try:
        args = parser().parse_args()
        args.func(args)
        return 0
    except Exception as exc:
        print(f"[BŁĄD] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
