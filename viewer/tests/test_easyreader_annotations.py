from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from core.easyreader_annotations import (
    BLOCK_RE,
    MIN_WORDS_FOR_MINHASH,
    EasyReaderAnnotationError,
    _apply_document_object,
    _target_metadata,
    _word_count,
    analyze_relink,
    append_annotation,
    apply_text_correction,
    backup_annotation_file,
    block_sha256,
    block_text_sha256,
    commit_relink,
    create_document,
    find_matching_block,
    is_confident_tier,
    load_document,
    minhash_signature,
    normalize_block_text,
)
from core.notes_export import export_notes_epub
from core.preview_state import PreviewState

from .epub_builder import build_epub3


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def zip_replace(archive_path: Path, replacements: dict[str, bytes]) -> None:
    """Minimalny odpowiednik `tools.easyreader.zip_replace` na potrzeby
    testów - ten moduł (`core.easyreader_annotations`) nigdy nie zapisuje
    do oryginalnego EPUB-a, więc taka funkcja świadomie tu nie istnieje;
    tutaj służy wyłącznie do SYMULOWANIA zewnętrznej edycji pliku."""
    original = zipfile.ZipFile(archive_path, "r")
    entries = {info.filename: original.read(info.filename) for info in original.infolist()}
    original.close()
    entries.update(replacements)
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in entries.items():
            out.writestr(name, data)


def make_annotation(epub: Path, notes: Path) -> None:
    create_document(notes, epub, title="Książka testowa")
    with zipfile.ZipFile(epub) as archive:
        chapter = archive.read("OEBPS/chap1.xhtml").decode("utf-8")
    blocks = list(BLOCK_RE.finditer(chapter))
    target = blocks[1]
    append_annotation(
        notes,
        epub,
        {
            "id": "fragment-0001",
            "target": {
                "section": "OEBPS/chap1.xhtml",
                "raw_index": 1,
                "block_sha256": block_sha256(target.group(0)),
                "block_text_sha256": block_text_sha256(target.group(0)),
                "block_minhash": minhash_signature(normalize_block_text(target.group(0))),
            },
            "content": {"prosty_jezyk": "To jest proste objaśnienie."},
        },
    )


def test_annotations_are_applied_only_to_temporary_preview(tmp_path: Path):
    epub = build_epub3(tmp_path / "book.epub")
    notes = tmp_path / "book.easyreader"
    original_hash = file_hash(epub)
    make_annotation(epub, notes)

    state = PreviewState()
    state.load_initial(str(epub), annotation_path=str(notes))
    try:
        chapter = Path(state.current_chapter_path()).read_text(encoding="utf-8")
        assert "To jest proste objaśnienie." in chapter
        assert "easyreader-opracowanie" in chapter
        assert file_hash(epub) == original_hash
    finally:
        state.cleanup()


def test_easyreader_file_does_not_copy_source_paragraph(tmp_path: Path):
    epub = build_epub3(tmp_path / "book.epub")
    notes = tmp_path / "book.easyreader"
    make_annotation(epub, notes)
    stored = notes.read_text(encoding="utf-8")
    assert "To jest treść z polskimi znakami" not in stored
    assert "To jest proste objaśnienie." in stored


def test_notes_are_skipped_without_blocking_the_book_for_a_different_epub(tmp_path: Path):
    """Punkt 2: niezgodne notatki nie mogą uniemożliwić otwarcia poprawnego EPUB-a -
    książka powinna się otworzyć bez adnotacji, z jasnym powodem w `annotation_error`."""
    epub = build_epub3(tmp_path / "book.epub")
    notes = tmp_path / "book.easyreader"
    make_annotation(epub, notes)
    changed = build_epub3(tmp_path / "changed.epub")
    changed.write_bytes(changed.read_bytes() + b"changed")

    state = PreviewState()
    try:
        state.load_initial(str(changed), annotation_path=str(notes))  # nie zgłasza wyjątku
        assert state.book is not None
        assert state.annotation_path is None
        assert state.annotation_error is not None
        chapter = Path(state.current_chapter_path()).read_text(encoding="utf-8")
        assert "easyreader-opracowanie" not in chapter
    finally:
        state.cleanup()


def test_visible_text_fingerprint_survives_technical_html_rewrite(tmp_path: Path):
    epub = build_epub3(tmp_path / "book.epub")
    notes = tmp_path / "book.easyreader"
    make_annotation(epub, notes)
    document = json.loads(notes.read_text(encoding="utf-8"))
    document["annotations"][0]["target"]["block_sha256"] = "0" * 64
    notes.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")

    state = PreviewState()
    state.load_initial(str(epub), annotation_path=str(notes))
    try:
        chapter = Path(state.current_chapter_path()).read_text(encoding="utf-8")
        assert "To jest proste objaśnienie." in chapter
    finally:
        state.cleanup()


def test_notes_export_is_a_standalone_epub_without_source_text(tmp_path: Path):
    source = build_epub3(tmp_path / "book.epub")
    notes = tmp_path / "book.easyreader"
    make_annotation(source, notes)

    exported = export_notes_epub(notes, tmp_path / "moje_notatki.epub")

    with zipfile.ZipFile(exported) as archive:
        assert archive.read("mimetype") == b"application/epub+zip"
        assert archive.getinfo("mimetype").compress_type == zipfile.ZIP_STORED
        content = archive.read("EPUB/notes.xhtml").decode("utf-8")
    assert "To jest proste objaśnienie." in content
    assert "To jest treść z polskimi znakami" not in content

    preview = PreviewState()
    preview.load_initial(str(exported))
    try:
        assert preview.book is not None
        assert preview.book.title.startswith("Notatki do:")
    finally:
        preview.cleanup()


# ---------------------------------------------------------------------- #
# Punkt 7: dopasowanie kotwic komentarzy powinno tolerować drobne korekty
# OCR (np. usunięcie podziału wyrazu albo numeru strony), zamiast łamać
# się na ścisłym dopasowaniu.
# ---------------------------------------------------------------------- #

def test_find_matching_block_survives_minor_ocr_style_edit():
    before_html = (
        "<p>To jest znacznie dłuższy akapit testowy, który ma imitować "
        "prawdziwy fragment książki z podziałem wy-razu w środku zdania "
        "oraz z typowym numerem strony dopisanym gdzieś w treści przez "
        "proces OCR, a poza tym akapit zawiera wystarczająco dużo słów, "
        "aby drobna korekta stanowiła tylko niewielki ułamek całości.</p>"
    )
    # Symulacja korekty OCR: usunięty podział wyrazu (dywiz + złamanie),
    # dopisany numer strony na końcu - identyczny sens, inny dokładny tekst.
    # Ponieważ akapit jest długi, ta drobna zmiana to mały ułamek shingli.
    after_html = before_html.replace("wy-razu", "wyrazu").replace("</p>", " 42</p>")

    target = {
        "raw_index": 3,
        "block_sha256": block_sha256(before_html),
        "block_text_sha256": block_text_sha256(before_html),
        "block_minhash": minhash_signature(normalize_block_text(before_html)),
    }

    # Ten sam akapit (po korekcie) siedzi teraz pod innym indeksem (5, nie 3),
    # bo wcześniej w rozdziale dopisano dwa nowe akapity.
    surrounding = [f"<p>Inny akapit numer {i}.</p>" for i in range(5)]
    chapter_html = "".join(surrounding[:5]) + after_html + "".join(surrounding)
    matches = list(BLOCK_RE.finditer(chapter_html))

    block_index, tier = find_matching_block(matches, target)

    assert block_index == 5
    assert tier.startswith("podobieństwo tekstu")


def test_find_matching_block_exact_html_wins_over_similarity():
    html_block = "<p>Zdanie bez żadnych zmian.</p>"
    target = {
        "raw_index": 0,
        "block_sha256": block_sha256(html_block),
        "block_text_sha256": block_text_sha256(html_block),
        "block_minhash": minhash_signature(normalize_block_text(html_block)),
    }
    chapter_html = "<p>Coś innego.</p>" + html_block
    matches = list(BLOCK_RE.finditer(chapter_html))

    block_index, tier = find_matching_block(matches, target)

    assert block_index == 1
    assert tier == "dokładny HTML"


def test_find_matching_block_reports_index_only_match_as_uncertain():
    """Poziom 4 istnieje wyłącznie informacyjnie - `is_confident_tier()` dla
    niego zwraca False (patrz `test_apply_document_skips_index_only_match...`
    poniżej, które sprawdza, że taka adnotacja NIE zostaje zastosowana)."""
    target = {"raw_index": 2, "block_sha256": "0" * 64, "block_text_sha256": "1" * 64}
    chapter_html = "".join(f"<p>Zupełnie inny akapit {i}.</p>" for i in range(5))
    matches = list(BLOCK_RE.finditer(chapter_html))

    block_index, tier = find_matching_block(matches, target)

    assert block_index == 2
    assert tier.startswith("niepewne")
    assert not is_confident_tier(tier)


# ---------------------------------------------------------------------- #
# Punkt 1: adnotacji NIE wolno zastosować na podstawie samego raw_index,
# jeśli treść nie została pewnie potwierdzona - musi zostać pominięta i
# widoczna w raporcie, żeby nigdy nie trafiła do niewłaściwego akapitu.
# ---------------------------------------------------------------------- #

def test_apply_document_skips_index_only_match_instead_of_misplacing(tmp_path: Path):
    epub = build_epub3(tmp_path / "book.epub")
    notes = tmp_path / "book.easyreader"
    make_annotation(epub, notes)

    state = PreviewState()
    try:
        state.load_initial(str(epub))  # bez notatek - tylko rozpakowany katalog
        # Podmieniamy CAŁĄ treść rozdziału na coś zupełnie innego, zachowując
        # ten sam indeks (1) - żaden hash/podobieństwo się nie zgodzi, ale
        # blok pod indeksem 1 wciąż istnieje.
        chapter_path = Path(state.book.opf_dir) / "chap1.xhtml"
        replacement = "".join(f"<p>Kompletnie inna, nowa treść numer {i}, bez związku z oryginałem.</p>" for i in range(5))
        chapter_path.write_text(
            f"<html><head><title>x</title></head><body>{replacement}</body></html>", encoding="utf-8",
        )

        document = load_document(notes)
        report = _apply_document_object(document, state.current_temp_dir)

        assert report.applied == 0
        assert len(report.skipped) == 1
        assert report.skipped[0]["id"] == "fragment-0001"
        assert "niepewne" in report.skipped[0]["reason"]

        # Kluczowe: żadna nowa treść komentarza NIE trafiła do pliku rozdziału.
        content = chapter_path.read_text(encoding="utf-8")
        assert "easyreader-opracowanie" not in content
        assert "To jest proste objaśnienie." not in content
    finally:
        state.cleanup()


def test_preview_state_surfaces_skipped_annotations(tmp_path: Path):
    """To samo co powyżej, ale przez publiczne API `PreviewState` używane
    przez UI - `annotation_skipped` musi pokazać pominiętą adnotację."""
    epub = build_epub3(tmp_path / "book.epub")
    notes = tmp_path / "book.easyreader"
    make_annotation(epub, notes)

    state = PreviewState()
    try:
        state.load_initial(str(epub), annotation_path=str(notes))
        chapter_path = Path(state.current_chapter_path())
        replacement = "".join(
            f"<p>Kompletnie inna, nowa treść numer {i}, bez związku z oryginałem.</p>" for i in range(5)
        )
        chapter_path.write_text(
            f"<html><head><title>x</title></head><body>{replacement}</body></html>", encoding="utf-8",
        )

        assert state.try_apply_annotations(str(notes)) is True  # plik nadal "stosuje się" (0 zastosowań to nie błąd)
        assert state.annotation_skipped
        assert state.annotation_skipped[0]["id"] == "fragment-0001"
    finally:
        state.cleanup()


# ---------------------------------------------------------------------- #
# Punkt 2: dopasowania pewne = dokładny hash HTML, hash znormalizowanego
# tekstu, albo MinHash powyżej bezpiecznego progu. Zachowanie dla krótkich
# i powtarzających się akapitów musi być OSTROŻNE (nigdy zgadywanie).
# ---------------------------------------------------------------------- #

def test_short_paragraph_never_gets_minhash_and_relies_on_exact_match_only():
    """Punkt 2 + 8: krótki akapit (poniżej MIN_WORDS_FOR_MINHASH) w ogóle
    nie dostaje odcisku MinHash - dopasowanie działa tylko przez dokładny
    hash. To jednocześnie usuwa zawodne "podobieństwo" krótkiego tekstu
    (punkt 2) i ryzyko zgadywania krótkiej treści z odcisku (punkt 8)."""
    short_html = "<p>Krótki tytuł.</p>"
    assert _word_count(normalize_block_text(short_html)) < MIN_WORDS_FOR_MINHASH

    metadata = _target_metadata(short_html, raw_index=0)
    assert "block_minhash" not in metadata

    # Zmieniony (ale wciąż krótki) akapit pod tym samym indeksem - bez
    # dokładnego dopasowania i bez MinHash powinien wylądować w trybie
    # informacyjnym (niepewne), NIE w "podobieństwo tekstu".
    edited_html = "<p>Inny tytuł.</p>"
    chapter_html = "<p>Coś przed.</p>" + edited_html
    matches = list(BLOCK_RE.finditer(chapter_html))
    block_index, tier = find_matching_block(matches, metadata)
    assert not tier.startswith("podobieństwo")
    assert not is_confident_tier(tier)


def test_repeated_paragraph_is_not_confidently_matched_when_ambiguous():
    """Punkt 2: gdy w oknie wyszukiwania jest kilka akapitów o niemal
    identycznej (powtórzonej) treści, dopasowanie przez podobieństwo NIE
    powinno zgadywać, który to ten właściwy."""
    refrain = (
        "<p>To jest powtarzający się refren tej opowieści, wystarczająco "
        "długi, aby normalnie kwalifikować się do dopasowania przez "
        "podobieństwo tekstu w tym module testowym.</p>"
    )
    target = {
        "raw_index": 2,
        "block_sha256": "0" * 64,  # udajemy, że oryginalny dokładny HTML już nie istnieje (np. inna wersja pliku)
        "block_text_sha256": "1" * 64,
        "block_minhash": minhash_signature(normalize_block_text(refrain)),
    }
    # Ten sam refren powtórzony DWUKROTNIE blisko siebie - typowa sytuacja
    # dla piosenek/inwokacji/powtórzeń retorycznych w tekście literackim.
    chapter_html = "<p>Inny akapit.</p>" + refrain + "<p>Coś pomiędzy.</p>" + refrain
    matches = list(BLOCK_RE.finditer(chapter_html))

    block_index, tier = find_matching_block(matches, target)

    # Oba wystąpienia dają niemal identyczne (tu: identyczne) podobieństwo -
    # różnica poniżej AMBIGUITY_MARGIN - więc dopasowanie NIE jest pewne.
    assert not tier.startswith("podobieństwo")


def test_repeated_paragraph_matches_confidently_when_one_occurrence_is_closer_and_clearly_better():
    """Kontrast do powyższego: gdy tylko JEDNO wystąpienie w oknie faktycznie
    ma wysokie podobieństwo (drugie jest wystarczająco inne), dopasowanie
    powinno się udać - to nie jest przypadek niejednoznaczny."""
    text = (
        "<p>To jest dość długi, unikalny akapit z wystarcza-jącą liczbą "
        "słów, żeby drobna korekta OCR zmieniła tylko niewielki ułamek "
        "całości i nie wpłynęła znacząco na wynik podobieństwa MinHash.</p>"
    )
    edited = text.replace("wystarcza-jącą", "wystarczającą").replace("</p>", " 7</p>")
    unrelated = "<p>Zupełnie inny, niepowiązany fragment o zupełnie innej tematyce i słownictwie.</p>"

    target = {
        "raw_index": 3,
        "block_sha256": block_sha256(text),
        "block_text_sha256": "0" * 64,  # symulujemy, że dokładny hash tekstu też już nie pasuje
        "block_minhash": minhash_signature(normalize_block_text(text)),
    }
    chapter_html = unrelated * 3 + edited
    matches = list(BLOCK_RE.finditer(chapter_html))

    block_index, tier = find_matching_block(matches, target)

    assert block_index == 3
    assert tier.startswith("podobieństwo tekstu")


def test_annotation_with_minor_ocr_edit_still_applies_via_similarity(tmp_path: Path):
    epub = build_epub3(tmp_path / "book.epub")
    notes = tmp_path / "book.easyreader"
    make_annotation(epub, notes)

    with zipfile.ZipFile(epub) as archive:
        chapter = archive.read("OEBPS/chap1.xhtml").decode("utf-8")
    blocks = list(BLOCK_RE.finditer(chapter))
    original_block = blocks[1].group(0)
    # Drobna korekta OCR w docelowym bloku: dopisany numer strony na końcu
    # zmienia OBA skróty (surowy i znormalizowany), ale nie zmienia sensu.
    edited_block = original_block[:-4] + " 17</p>"
    edited_chapter = chapter[: blocks[1].start()] + edited_block + chapter[blocks[1].end():]


    zip_replace(epub, {"OEBPS/chap1.xhtml": edited_chapter.encode("utf-8")})

    # UWAGA: edycja zmienia sha256 CAŁEGO pliku EPUB, więc normalny odczyt
    # (ze ścisłą weryfikacją źródła) musiałby teraz odrzucić notatki - to
    # oczekiwane (patrz `test_notes_are_rejected_for_a_different_epub`).
    # Tu sprawdzamy wyłącznie samą logikę dopasowania bloku (poziom 3),
    # używaną też przy bezpiecznym relinku po jego zatwierdzeniu.
    document = load_document(notes)
    target = document["annotations"][0]["target"]
    matches = list(BLOCK_RE.finditer(edited_chapter))
    block_index, tier = find_matching_block(matches, target)

    assert block_index == 1
    assert tier.startswith("podobieństwo tekstu")


# ---------------------------------------------------------------------- #
# Punkt 3: bezpieczne ponowne powiązanie notatek z nowym wydaniem EPUB-a.
# ---------------------------------------------------------------------- #

def test_analyze_relink_recommends_when_structure_matches(tmp_path: Path):
    epub = build_epub3(tmp_path / "book.epub")
    notes = tmp_path / "book.easyreader"
    make_annotation(epub, notes)

    # Nowe "wydanie": ten sam plik fizycznie zmodyfikowany (inny sha256),
    # ale treść i struktura rozdziałów pozostają praktycznie identyczne.
    new_epub = tmp_path / "book_corrected.epub"
    new_epub.write_bytes(epub.read_bytes())
    with zipfile.ZipFile(new_epub) as archive:
        chapter = archive.read("OEBPS/chap1.xhtml").decode("utf-8")


    zip_replace(new_epub, {"OEBPS/chap1.xhtml": chapter.replace("Rozdział pierwszy", "ROZDZIAŁ PIERWSZY").encode("utf-8")})

    state = PreviewState()
    try:
        state.load_initial(str(new_epub))  # bez notatek - tylko żeby mieć rozpakowany katalog
        report = analyze_relink(notes, new_epub, state.current_temp_dir, new_book_title="Książka testowa")
    finally:
        state.cleanup()

    assert report["total"] == 1
    assert report["matched"] == 1
    assert report["ratio"] == 1.0
    assert report["recommended"] is True
    assert report["title_similarity"] == 1.0


def test_commit_relink_updates_identifier_and_apply_then_succeeds(tmp_path: Path):
    epub = build_epub3(tmp_path / "book.epub")
    notes = tmp_path / "book.easyreader"
    make_annotation(epub, notes)
    notes_bytes_before = notes.read_bytes()

    new_epub = tmp_path / "book_v2.epub"
    new_epub.write_bytes(epub.read_bytes() + b"\x00")  # dowolna techniczna zmiana sumy kontrolnej

    # Przed relinkiem: notatki nie pasują, więc książka otwiera się BEZ nich
    # (nie blokuje otwarcia - patrz punkt 2), ale zostaje jasny powód dlaczego.
    state = PreviewState()
    try:
        state.load_initial(str(new_epub), annotation_path=str(notes))
        assert state.annotation_path is None
        assert state.annotation_error is not None
        extracted_root = state.current_temp_dir

        result = commit_relink(notes, new_epub, extracted_root)
    finally:
        state.cleanup()

    # Punkt 3: powstała kopia bezpieczeństwa z treścią SPRZED relinku.
    backup_path = Path(result["backup_path"])
    assert backup_path.is_file()
    assert backup_path.read_bytes() == notes_bytes_before
    assert result["apply_report"]["applied"] == 1

    # Po potwierdzonym relinku: ten sam plik notatek otwiera się już bez błędu.
    state2 = PreviewState()
    try:
        state2.load_initial(str(new_epub), annotation_path=str(notes))
        chapter = Path(state2.current_chapter_path()).read_text(encoding="utf-8")
        assert "To jest proste objaśnienie." in chapter
    finally:
        state2.cleanup()

    document = load_document(notes)
    assert document["relink_history"][0]["filename"] == new_epub.name


# ---------------------------------------------------------------------- #
# Punkt 4: relink transakcyjny - nieudana próba nie może zmienić
# oryginalnego pliku notatek (ani utworzyć niepotrzebnej kopii zapasowej).
# ---------------------------------------------------------------------- #

def test_failed_relink_does_not_modify_or_backup_the_original_file(tmp_path: Path):
    epub = build_epub3(tmp_path / "book.epub")
    notes = tmp_path / "book.easyreader"
    make_annotation(epub, notes)
    notes_bytes_before = notes.read_bytes()

    # Zupełnie inna książka - żadna adnotacja nie ma szans się dopasować.
    unrelated_epub = tmp_path / "unrelated.epub"
    unrelated_epub.write_bytes(epub.read_bytes())

    existing_files_before = set(tmp_path.iterdir())

    state = PreviewState()
    try:
        state.load_initial(str(unrelated_epub))
        chapter_path = Path(state.book.opf_dir) / "chap1.xhtml"
        replacement = "".join(
            f"<p>Zupełnie inna książka, akapit {i}, bez żadnego związku z oryginałem.</p>" for i in range(5)
        )
        chapter_path.write_text(
            f"<html><head><title>x</title></head><body>{replacement}</body></html>", encoding="utf-8",
        )
        extracted_root = state.current_temp_dir

        with pytest.raises(EasyReaderAnnotationError):
            commit_relink(notes, unrelated_epub, extracted_root)
    finally:
        state.cleanup()

    # Plik notatek jest BAJT W BAJT taki sam jak przed nieudaną próbą.
    assert notes.read_bytes() == notes_bytes_before
    # I nie powstała żadna zbędna kopia zapasowa.
    assert set(tmp_path.iterdir()) == existing_files_before


# ---------------------------------------------------------------------- #
# Punkt 5: rekomendacja relinku nie może opierać się wyłącznie na procencie
# dopasowanych adnotacji.
# ---------------------------------------------------------------------- #

def test_relink_not_recommended_with_zero_annotations(tmp_path: Path):
    epub = build_epub3(tmp_path / "book.epub")
    notes = tmp_path / "book.easyreader"
    create_document(notes, epub, title="Pusta książka")  # brak adnotacji

    new_epub = tmp_path / "book_v2.epub"
    new_epub.write_bytes(epub.read_bytes() + b"\x00")

    state = PreviewState()
    try:
        state.load_initial(str(new_epub))
        report = analyze_relink(notes, new_epub, state.current_temp_dir, new_book_title="Pusta książka")
    finally:
        state.cleanup()

    assert report["total"] == 0
    assert report["recommended"] is False


def test_relink_not_recommended_when_title_differs_despite_high_match_ratio(tmp_path: Path):
    epub = build_epub3(tmp_path / "book.epub")
    notes = tmp_path / "book.easyreader"
    make_annotation(epub, notes)  # tytuł zapisany: "Książka testowa"

    new_epub = tmp_path / "book_v2.epub"
    new_epub.write_bytes(epub.read_bytes() + b"\x00")  # treść identyczna, więc dopasowanie adnotacji będzie 100%

    state = PreviewState()
    try:
        state.load_initial(str(new_epub))
        report = analyze_relink(
            notes, new_epub, state.current_temp_dir,
            new_book_title="Zupełnie inny tytuł zupełnie innej książki",
        )
    finally:
        state.cleanup()

    assert report["ratio"] == 1.0  # samo dopasowanie adnotacji jest doskonałe
    assert report["title_similarity"] is not None
    assert report["title_similarity"] < 0.5
    assert report["recommended"] is False  # ale tytuł się nie zgadza, więc NIE rekomendujemy


# ---------------------------------------------------------------------- #
# Log z 2026-08-12, punkt 2: drobne korekty OCR ("^santuri", "byty"->"były",
# samotny numer strony) - poprawka WEWNĄTRZ bloku, bez dotykania EPUB-a
# źródłowego, z tą samą dyscypliną co reszta modułu: brak pewności = pominięcie.
# ---------------------------------------------------------------------- #

def test_apply_text_correction_replaces_unique_occurrence():
    raw = "<p>^santuri grało w tle, gdy byty już ciche.</p>"
    corrected = apply_text_correction(raw, {"find": "^santuri", "replace": "santuri"})
    assert corrected == "<p>santuri grało w tle, gdy byty już ciche.</p>"


def test_apply_text_correction_returns_none_when_text_not_found():
    raw = "<p>Zwykły akapit bez błędów.</p>"
    assert apply_text_correction(raw, {"find": "^santuri", "replace": "santuri"}) is None


def test_apply_text_correction_returns_none_when_ambiguous():
    """Punkt 1 (ta sama dyscyplina): tekst występujący wielokrotnie w bloku
    nie może zostać poprawiony na chybił trafił - nie wiadomo, które
    wystąpienie autor miał na myśli."""
    raw = "<p>byty i znowu byty, wciąż te same byty.</p>"
    assert apply_text_correction(raw, {"find": "byty", "replace": "były"}) is None


def test_ocr_correction_annotation_fixes_text_without_touching_source_epub(tmp_path: Path):
    epub = build_epub3(tmp_path / "book.epub")
    notes = tmp_path / "book.easyreader"
    original_hash = file_hash(epub)
    create_document(notes, epub, title="Książka testowa")

    with zipfile.ZipFile(epub) as archive:
        chapter = archive.read("OEBPS/chap1.xhtml").decode("utf-8")
    blocks = list(BLOCK_RE.finditer(chapter))
    target_block = blocks[1]  # akapit "To jest treść z polskimi znakami: ..."

    append_annotation(
        notes,
        epub,
        {
            "id": "korekta-0001",
            "type": "korekta_ocr",
            "target": {
                "section": "OEBPS/chap1.xhtml",
                "raw_index": 1,
                "block_sha256": block_sha256(target_block.group(0)),
                "block_text_sha256": block_text_sha256(target_block.group(0)),
            },
            "correction": {"find": "polskimi", "replace": "poprawionymi"},
        },
    )

    state = PreviewState()
    try:
        state.load_initial(str(epub), annotation_path=str(notes))
        chapter_after = Path(state.current_chapter_path()).read_text(encoding="utf-8")
        assert "poprawionymi znakami" in chapter_after
        assert "polskimi znakami" not in chapter_after
        # Komentarz ("opracowanie") NIE powstał - to inny typ adnotacji.
        # (CSS klasa .easyreader-opracowanie jest zawsze wstrzykiwana w
        # <head>, więc sprawdzamy konkretny znacznik <aside>, nie samą nazwę klasy.)
        assert '<aside class="easyreader-opracowanie"' not in chapter_after
        # Źródłowy EPUB pozostaje bajt w bajt niezmieniony.
        assert file_hash(epub) == original_hash
        assert not state.annotation_skipped
    finally:
        state.cleanup()


def test_ocr_correction_is_skipped_when_target_text_changed(tmp_path: Path):
    """Jeśli tekst do poprawienia już nie istnieje w bloku (np. ktoś go
    wcześniej poprawił inaczej, albo blok się zmienił), korekta jest
    pomijana i widoczna w raporcie - a NIE stosowana na chybił trafił."""
    epub = build_epub3(tmp_path / "book.epub")
    notes = tmp_path / "book.easyreader"
    create_document(notes, epub, title="Książka testowa")

    with zipfile.ZipFile(epub) as archive:
        chapter = archive.read("OEBPS/chap1.xhtml").decode("utf-8")
    blocks = list(BLOCK_RE.finditer(chapter))
    target_block = blocks[1]

    append_annotation(
        notes,
        epub,
        {
            "id": "korekta-0001",
            "type": "korekta_ocr",
            "target": {
                "section": "OEBPS/chap1.xhtml",
                "raw_index": 1,
                "block_sha256": block_sha256(target_block.group(0)),
                "block_text_sha256": block_text_sha256(target_block.group(0)),
            },
            "correction": {"find": "tekst_ktorego_na_pewno_tam_nie_ma", "replace": "cokolwiek"},
        },
    )

    state = PreviewState()
    try:
        state.load_initial(str(epub), annotation_path=str(notes))
        assert state.annotation_skipped
        assert state.annotation_skipped[0]["id"] == "korekta-0001"
        assert "korekta" in state.annotation_skipped[0]["reason"]
    finally:
        state.cleanup()


def test_ocr_correction_and_comment_can_coexist_on_same_block(tmp_path: Path):
    """Korekta OCR (w bloku) i zwykły komentarz (po bloku) na TYM SAMYM
    akapicie nie powinny sobie przeszkadzać."""
    epub = build_epub3(tmp_path / "book.epub")
    notes = tmp_path / "book.easyreader"
    create_document(notes, epub, title="Książka testowa")

    with zipfile.ZipFile(epub) as archive:
        chapter = archive.read("OEBPS/chap1.xhtml").decode("utf-8")
    blocks = list(BLOCK_RE.finditer(chapter))
    target_block = blocks[1]
    target_meta = {
        "section": "OEBPS/chap1.xhtml",
        "raw_index": 1,
        "block_sha256": block_sha256(target_block.group(0)),
        "block_text_sha256": block_text_sha256(target_block.group(0)),
    }

    append_annotation(
        notes, epub,
        {
            "id": "korekta-0001", "type": "korekta_ocr",
            "target": dict(target_meta),
            "correction": {"find": "polskimi", "replace": "poprawionymi"},
        },
    )
    append_annotation(
        notes, epub,
        {
            "id": "fragment-0001",
            "target": dict(target_meta),
            "content": {"prosty_jezyk": "To jest proste objaśnienie."},
        },
    )

    state = PreviewState()
    try:
        state.load_initial(str(epub), annotation_path=str(notes))
        chapter_after = Path(state.current_chapter_path()).read_text(encoding="utf-8")
        assert "poprawionymi znakami" in chapter_after
        assert "To jest proste objaśnienie." in chapter_after
        assert not state.annotation_skipped
    finally:
        state.cleanup()
