from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
from tempfile import NamedTemporaryFile
import time
import unicodedata
from urllib.parse import unquote
import zipfile
from xml.etree import ElementTree as ET


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOOKS_ROOT = PROJECT_ROOT / "ksiazki_robocze"
DEFAULT_PROFILE = PROJECT_ROOT / "profil_czytania.md"
ACTIVE_FILE = PROJECT_ROOT / "AKTYWNA_KSIAZKA.txt"

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
        fail("Plik AKTYWNA_KSIAZKA.txt jest pusty.")
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
    <meta property="dcterms:modified">2026-08-01T00:00:00Z</meta>
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
    (book_dir / "zrodlo").mkdir(parents=True, exist_ok=True)
    (book_dir / "temp" / "historia").mkdir(parents=True, exist_ok=True)
    (book_dir / "backups").mkdir(parents=True, exist_ok=True)

    source_copy = book_dir / "zrodlo" / source.name
    shutil.copy2(source, source_copy)
    normalized = book_dir / "zrodlo" / "zrodlo_znormalizowane.epub"
    if source.suffix.lower() == ".epub":
        shutil.copy2(source, normalized)
    elif source.suffix.lower() == ".pdf":
        calibre = find_calibre()
        print("Konwersja PDF do EPUB przez Calibre…")
        calibre_config = book_dir / "temp" / "calibre-config"
        calibre_config.mkdir(parents=True, exist_ok=True)
        calibre_env = os.environ.copy()
        calibre_env["CALIBRE_CONFIG_DIRECTORY"] = str(calibre_config)
        subprocess.run(
            [str(calibre), str(source_copy), str(normalized)],
            check=True,
            cwd=book_dir,
            env=calibre_env,
        )
    else:
        build_epub_from_text(source_copy, normalized)

    errors = validate_epub(normalized)
    if errors:
        fail("Nie udało się utworzyć poprawnego EPUB-a:\n" + "\n".join(errors))

    working = book_dir / f"{name}_easyReader.epub"
    shutil.copy2(normalized, working)
    inject_reading_style(working)
    errors = validate_epub(working)
    if errors:
        fail("Kopia robocza EPUB jest niepoprawna:\n" + "\n".join(errors))
    _, spine = rootfile_and_spine(normalized)
    state = {
        "format": 1,
        "name": name,
        "source_original": str(source_copy),
        "source_epub": str(normalized),
        "working_epub": str(working),
        "spine": spine,
        "cursor": {"section": 0, "raw_block": 0},
        "pending": None,
        "applied": 0,
        "skipped": 0,
        "history": [],
    }
    save_json(book_dir / "postep.json", state)
    ACTIVE_FILE.write_text(str(book_dir), encoding="utf-8")
    shutil.copy2(DEFAULT_PROFILE, book_dir / "profil_czytania.md")
    (book_dir / "notatki_czytelnika.md").write_text(
        f"# Notatki do książki: {source.stem}\n\n", encoding="utf-8"
    )
    launcher = book_dir / "Otworz_w_Podgladzie.bat"
    launcher.write_text(
        "@echo off\n"
        f'call "{PROJECT_ROOT}\\viewer\\Otworz_plik.bat" "{working}"\n',
        encoding="utf-8",
    )
    print(f"Katalog książki: {book_dir}")
    print(f"Kopia do czytania: {working}")


class UncertainEndOfBook(RuntimeError):
    """Skan nie znalazł kandydata, ale napotkał błędy odczytu/dekodowania po
    drodze - wynik jest NIEPEWNY (patrz zgłoszenie: to samo wywołanie na tym
    samym, niezmienionym stanie i pliku bywało niedeterministyczne - raz
    zwracało fałszywy koniec książki, raz poprawny fragment). W przeciwieństwie
    do potwierdzonego końca książki (`print` + normalny powrót, kod wyjścia 0),
    to wyjątek - `main()` kończy program osobnym kodem wyjścia (3), żeby
    wywołujący mógł programowo odróżnić "na pewno koniec" od "spróbuj ponownie"."""


NEXT_SCAN_ATTEMPTS = 3
NEXT_SCAN_RETRY_DELAY_S = 0.3


class _ScanResult:
    """Wynik JEDNEJ próby przeszukania książki od danego kursora."""

    def __init__(self) -> None:
        self.selected: list[dict] = []
        self.selected_section: str | None = None
        self.selected_section_index: int | None = None
        self.section_diagnostics: list[dict] = []
        self.errors: list[tuple[str | None, Exception]] = []


def _scan_for_next_fragment(
    source_epub: Path,
    spine: list[str],
    section_index: int,
    raw_cursor: int,
    chars_limit: int,
) -> _ScanResult:
    """Pojedyncza próba przeszukania książki od podanego kursora w
    poszukiwaniu kolejnego fragmentu.

    Jeśli `result.errors` jest niepuste, wynik (włącznie z ewentualnym
    brakiem kandydatów) jest NIEPEWNY - odczyt lub dekodowanie części
    dokumentu się nie powiodło (np. przejściowy problem z dostępem do pliku:
    blokada przez antywirusa, chmurowa synchronizacja z "plikami na żądanie"
    itp. - wszystkie objawiają się podobnie: plik technicznie istnieje, ale
    pojedynczy odczyt bywa niepełny albo chwilowo się nie udaje). Taką próbę
    należy powtórzyć, zanim uzna się ją za rozstrzygającą - patrz
    `command_next`, które wywołuje tę funkcję do `NEXT_SCAN_ATTEMPTS` razy.
    """
    result = _ScanResult()
    try:
        archive = zipfile.ZipFile(source_epub)
    except (zipfile.BadZipFile, OSError) as exc:
        result.errors.append((None, exc))
        return result

    try:
        bad_member = archive.testzip()
        if bad_member is not None:
            result.errors.append((bad_member, RuntimeError(f"uszkodzony element archiwum: {bad_member}")))
            return result

        while section_index < len(spine):
            section = spine[section_index]
            try:
                raw_bytes = archive.read(section)
            except (KeyError, OSError, zipfile.BadZipFile) as exc:
                result.errors.append((section, exc))
                section_index += 1
                raw_cursor = 0
                continue

            try:
                document = raw_bytes.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                # "strict" celowo: "replace" mogłoby ukryć niepełny/uszkodzony
                # odczyt jako pozornie poprawny (ale okaleczony) tekst. Mierzymy
                # i tak (z "replace"), ale oznaczamy próbę jako niepewną.
                document = raw_bytes.decode("utf-8", errors="replace")
                result.errors.append((section, exc))

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
            result.section_diagnostics.append(
                {
                    "section": section,
                    "section_index": section_index,
                    "raw_cursor": raw_cursor,
                    "block_re_matches": len(all_matches),
                    "candidates": len(candidates),
                }
            )
            if candidates:
                result.selected_section = section
                result.selected_section_index = section_index
                total = 0
                for item in candidates:
                    # Jeden fragment ma odpowiadać jednemu naturalnemu ustępowi.
                    # Pierwszy śródtytuł należy do fragmentu, kolejny rozpoczyna następny.
                    if result.selected and is_section_heading(item):
                        break
                    if result.selected and total + len(item["text"]) > chars_limit:
                        break
                    result.selected.append(item)
                    total += len(item["text"])
                    if total >= chars_limit:
                        break
                break
            section_index += 1
            raw_cursor = 0
    finally:
        archive.close()
    return result



def command_next(args: argparse.Namespace) -> None:
    book_dir = resolve_book(args.book)
    state = load_state(book_dir)
    if state.get("pending"):
        print("Bieżący fragment czeka już na opracowanie:")
        print(book_dir / "temp" / "fragment_biezacy.txt")
        return
    source_epub = Path(state["source_epub"])
    cursor_before = dict(state["cursor"])
    section_index = int(state["cursor"]["section"])
    raw_cursor = int(state["cursor"]["raw_block"])

    # Zgłoszony problem: to samo wywołanie, na tym samym niezmienionym
    # stanie i pliku, bywało niedeterministyczne - raz zwracało fałszywy
    # koniec książki, raz poprawny fragment (typowy objaw przejściowego
    # problemu z dostępem do pliku, np. blokady antywirusa albo chmurowej
    # synchronizacji "na żądanie" - plik technicznie istnieje, ale
    # pojedynczy odczyt bywa niepełny). Zamiast ufać JEDNEJ próbie,
    # skanujemy do `NEXT_SCAN_ATTEMPTS` razy: sukces (znaleziony kandydat)
    # kończy natychmiast; "koniec książki" uznajemy za potwierdzony dopiero
    # po DWÓCH KOLEJNYCH próbach BEZ żadnych błędów odczytu, które ZGODNIE
    # nie znalazły kandydata.
    last_result: _ScanResult | None = None
    consecutive_clean_empty = 0
    attempts_made = 0
    for attempt in range(1, NEXT_SCAN_ATTEMPTS + 1):
        attempts_made = attempt
        last_result = _scan_for_next_fragment(source_epub, state["spine"], section_index, raw_cursor, args.chars)
        if last_result.selected:
            break
        if last_result.errors:
            consecutive_clean_empty = 0
        else:
            consecutive_clean_empty += 1
            if consecutive_clean_empty >= 2:
                break
        if attempt < NEXT_SCAN_ATTEMPTS:
            time.sleep(NEXT_SCAN_RETRY_DELAY_S)

    assert last_result is not None  # pętla wykonuje się co najmniej raz

    if not last_result.selected:
        confirmed = not last_result.errors and consecutive_clean_empty >= 2
        print(
            "Koniec książki — nie znaleziono następnego fragmentu."
            if confirmed
            else "NIEPEWNY WYNIK: nie udało się jednoznacznie potwierdzić końca książki "
            "(napotkano błędy odczytu pliku źródłowego)."
        )
        print("— Diagnostyka —")
        print(f"  Książka (katalog): {book_dir}")
        print(f"  Źródłowy EPUB: {source_epub}")
        print(f"  Liczba dokumentów w spine: {len(state['spine'])}")
        print(f"  Kursor przed przeszukaniem: sekcja {cursor_before['section']}, blok {cursor_before['raw_block']}")
        print(
            f"  Liczba prób skanowania: {attempts_made} "
            f"(potwierdzone czyste próby pod rząd: {consecutive_clean_empty})"
        )
        total_candidates = sum(entry["candidates"] for entry in last_result.section_diagnostics)
        print(f"  Przeszukano dokumentów spine (od kursora): {len(last_result.section_diagnostics)}")
        print(f"  Kandydatów znalezionych od kursora: {total_candidates}")
        for entry in last_result.section_diagnostics:
            print(
                f"    sekcja={entry['section']!r} section_index={entry['section_index']} "
                f"raw_cursor={entry['raw_cursor']} dopasowań_BLOCK_RE={entry['block_re_matches']} "
                f"kandydatów={entry['candidates']}"
            )
        if last_result.errors:
            print("  Błędy/ostrzeżenia odczytu w ostatniej próbie:")
            for section, exc in last_result.errors:
                print(f"    {section!r}: {type(exc).__name__}: {exc}")
        if not args.book and ACTIVE_FILE.exists():
            print(
                f"  UWAGA: książka nie została podana jawnie - użyto aktywnej "
                f"książki z {ACTIVE_FILE}: {ACTIVE_FILE.read_text(encoding='utf-8').strip()}"
            )
            print("  Jeśli spodziewałeś się innej książki, podaj jawnie: easyreader.py next <ścieżka_katalogu>")
        if not confirmed:
            raise UncertainEndOfBook(
                "Skanowanie zakończyło się niepewnym wynikiem po napotkaniu błędów odczytu - "
                "spróbuj ponownie (stan nie został zmieniony)."
            )
        return

    selected = last_result.selected
    selected_section = last_result.selected_section
    section_index = last_result.selected_section_index

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
    annotation = build_annotation(data, pending["id"])
    anchor = pending["anchor_raw_html"]
    anchor_occurrence = int(pending["anchor_occurrence"])
    next_cursor = pending["next_cursor"]
    if data.get("apply_through_raw_index") is not None:
        raw_index = int(data["apply_through_raw_index"])
        chosen = next(
            (item for item in pending["blocks"] if int(item["raw_index"]) == raw_index),
            None,
        )
        if not chosen:
            fail("Wybrana granica opracowania nie należy do bieżącego fragmentu.")
        source_epub = Path(state["source_epub"])
        with zipfile.ZipFile(source_epub) as source_archive:
            source_document = source_archive.read(pending["section"]).decode(
                "utf-8", errors="replace"
            )
        source_raw = [m.group(0) for m in BLOCK_RE.finditer(source_document)]
        if raw_index < 0 or raw_index >= len(source_raw):
            fail("Wybrana granica opracowania wykracza poza bieżący dokument EPUB-a.")
        # Korekta OCR może zmienić HTML punktu zaczepienia już po pobraniu
        # fragmentu. Używaj jego aktualnej postaci z zachowaniem tego samego
        # indeksu bloku, zamiast nieaktualnej kopii zapisanej w pending.
        anchor = source_raw[raw_index]
        anchor_occurrence = sum(
            1 for raw in source_raw[: raw_index + 1] if raw == anchor
        )
        next_cursor = {
            "section": pending["section_index"],
            "raw_block": raw_index + 1,
        }
    working = Path(state["working_epub"])
    section = pending["section"]
    with zipfile.ZipFile(working) as archive:
        document = archive.read(section).decode("utf-8", errors="replace")
    if f'data-easyreader-id="{pending["id"]}"' in document:
        fail("Ten fragment został już dodany do książki.")
    position = nth_find(document, anchor, anchor_occurrence)
    if position < 0:
        fail("Nie odnaleziono fragmentu źródłowego w kopii roboczej EPUB-a.")
    insert_at = position + len(anchor)
    document = document[:insert_at] + annotation + document[insert_at:]
    backup = book_dir / "backups" / f'{pending["id"]}_przed_zmiana.epub'
    shutil.copy2(working, backup)
    zip_replace(working, {section: document.encode("utf-8")})
    errors = validate_epub(working)
    if errors:
        shutil.copy2(backup, working)
        fail("Zmiana została cofnięta, ponieważ EPUB jest niepoprawny:\n" + "\n".join(errors))
    history_dir = book_dir / "temp" / "historia"
    history_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(book_dir / "temp" / "fragment_biezacy.txt", history_dir / f'{pending["id"]}_oryginal.txt')
    shutil.copy2(data_path, history_dir / f'{pending["id"]}_opracowanie.json')
    state["cursor"] = next_cursor
    state["applied"] += 1
    state["history"].append(
        {"id": pending["id"], "status": "applied", "section": section}
    )
    state["pending"] = None
    save_json(book_dir / "postep.json", state)
    print(f"Dodano {pending['id']} do {working}")
    print(f"Kopia bezpieczeństwa: {backup}")


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
        "book_dir": str(book_dir),
        "name": state["name"],
        "source_epub": state.get("source_epub"),
        "working_epub": state["working_epub"],
        "cursor": state["cursor"],
        "applied": state["applied"],
        "skipped": state["skipped"],
        "pending": state["pending"]["id"] if state.get("pending") else None,
        "resolved_from": "argument" if args.book else "AKTYWNA_KSIAZKA.txt",
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
    ACTIVE_FILE.write_text(str(book_dir), encoding="utf-8")
    print(f"Aktywna książka: {book_dir}")


def command_list(args: argparse.Namespace) -> None:
    active = resolve_book(None) if ACTIVE_FILE.exists() else None
    for path in sorted(BOOKS_ROOT.iterdir() if BOOKS_ROOT.exists() else []):
        if not (path / "postep.json").exists():
            continue
        marker = "*" if active and path.resolve() == active else " "
        print(f"{marker} {path}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Czytelnicza Brzytwa Ockhama")
    sub = root.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Utwórz nową książkę roboczą")
    init.add_argument("source")
    init.add_argument("--name")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=command_init)
    nxt = sub.add_parser("next", help="Pobierz następny fragment")
    nxt.add_argument("book", nargs="?")
    nxt.add_argument("--chars", type=int, default=1600)
    nxt.set_defaults(func=command_next)
    apply = sub.add_parser("apply", help="Dodaj opracowanie do EPUB-a")
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
    return root


def main() -> int:
    try:
        args = parser().parse_args()
        args.func(args)
        return 0
    except UncertainEndOfBook as exc:
        # Osobny kod wyjścia (3): "next" napotkało błędy odczytu i NIE może
        # potwierdzić, czy to naprawdę koniec książki - w odróżnieniu od
        # kodu 0 (potwierdzony koniec albo sukces) i kodu 1 (twardy błąd).
        # Patrz zgłoszenie: dotąd kod wyjścia 0 nie pozwalał wywołującemu
        # odróżnić prawdziwego końca od tej niepewnej sytuacji.
        print(f"[NIEPEWNY KONIEC] {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"[BŁĄD] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
