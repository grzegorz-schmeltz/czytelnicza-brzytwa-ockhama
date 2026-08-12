from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from core.epub_parser import EpubParseError
from core.preview_state import PreviewState
from core.safe_extract import EpubSecurityError

from .epub_builder import build_epub3, build_zip_slip_epub


def test_load_initial_sets_up_state(tmp_path: Path):
    epub_path = build_epub3(tmp_path / "book.epub")
    state = PreviewState()
    state.load_initial(str(epub_path))
    try:
        assert state.book is not None
        assert state.current_spine_index == 0
        assert state.current_chapter_href() == "chap1.xhtml"
        assert os.path.isdir(state.current_temp_dir)
    finally:
        state.cleanup()


def test_scripts_are_stripped_after_load(tmp_path: Path):
    epub_path = build_epub3(tmp_path / "book_script.epub", with_script=True)
    state = PreviewState()
    state.load_initial(str(epub_path))
    try:
        chapter_path = state.current_chapter_path()
        content = Path(chapter_path).read_text(encoding="utf-8")
        assert "<script" not in content.lower()
        assert "onload" not in content
        assert "onclick" not in content
    finally:
        state.cleanup()


def test_reload_preserves_current_chapter(tmp_path: Path):
    epub_path = build_epub3(tmp_path / "book.epub")
    state = PreviewState()
    state.load_initial(str(epub_path))
    try:
        assert state.go_next() is True
        assert state.current_chapter_href() == "chap2.xhtml"

        # Symulujemy zewnętrzny zapis nowej wersji tego samego pliku.
        time.sleep(0.05)
        build_epub3(epub_path)

        old_temp_dir = state.current_temp_dir
        position = state.reload()

        assert position.spine_href == "chap2.xhtml"
        assert state.current_chapter_href() == "chap2.xhtml"
        # Stary katalog tymczasowy powinien zostać usunięty po udanym reloadzie.
        assert not os.path.isdir(old_temp_dir)
        assert os.path.isdir(state.current_temp_dir)
    finally:
        state.cleanup()


def test_reload_falls_back_to_nearest_chapter_when_missing(tmp_path: Path):
    """
    Wymaganie 16: jeśli identyczny rozdział nie istnieje w nowej wersji,
    program przechodzi do najbliższego istniejącego rozdziału (tu: rozdział 0,
    ponieważ nazwa pliku 'chap2.xhtml' nie występuje w nowej, jednorozdziałowej
    wersji książki).
    """
    epub_path = build_epub3(tmp_path / "book.epub")
    state = PreviewState()
    state.load_initial(str(epub_path))
    try:
        state.go_next()  # teraz na chap2.xhtml

        # Nowa wersja pliku ma inną strukturę (tylko jeden rozdział o innej nazwie).
        import zipfile

        from .epub_builder import CONTAINER_XML, NAV_XHTML, STYLE_CSS

        new_opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Nowa wersja</dc:title></metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="only" href="only.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="only"/></spine>
</package>
"""
        with zipfile.ZipFile(epub_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("mimetype", "application/epub+zip")
            zf.writestr("META-INF/container.xml", CONTAINER_XML)
            zf.writestr("OEBPS/content.opf", new_opf)
            zf.writestr("OEBPS/nav.xhtml", NAV_XHTML)
            zf.writestr("OEBPS/only.xhtml", "<html><body>Jedyny rozdzial</body></html>")
            zf.writestr("OEBPS/style.css", STYLE_CSS)

        position = state.reload()
        assert position.spine_href == "only.xhtml"
        assert state.current_spine_index == 0
    finally:
        state.cleanup()


def test_reload_keeps_old_preview_when_new_version_invalid(tmp_path: Path):
    epub_path = build_epub3(tmp_path / "book.epub")
    state = PreviewState()
    state.load_initial(str(epub_path))
    old_temp_dir = state.current_temp_dir
    old_title = state.book.title
    try:
        # Nadpisujemy plik uszkodzoną zawartością (symulacja niekompletnego zapisu).
        epub_path.write_bytes(b"uszkodzone dane, nie zip")

        with pytest.raises(Exception):
            state.reload()

        # Stary, poprawny podgląd powinien pozostać nienaruszony.
        assert state.current_temp_dir == old_temp_dir
        assert state.book.title == old_title
        assert os.path.isdir(old_temp_dir)
    finally:
        state.cleanup()


def test_reload_rejects_malicious_replacement(tmp_path: Path):
    epub_path = build_epub3(tmp_path / "book.epub")
    state = PreviewState()
    state.load_initial(str(epub_path))
    old_temp_dir = state.current_temp_dir
    try:
        build_zip_slip_epub(epub_path)
        with pytest.raises(EpubSecurityError):
            state.reload()
        # Podgląd sprzed złośliwej podmiany nadal działa.
        assert state.current_temp_dir == old_temp_dir
        assert os.path.isdir(old_temp_dir)
    finally:
        state.cleanup()


def test_cleanup_removes_temp_dir(tmp_path: Path):
    epub_path = build_epub3(tmp_path / "book.epub")
    state = PreviewState()
    state.load_initial(str(epub_path))
    temp_dir = state.current_temp_dir
    state.cleanup()
    assert not os.path.isdir(temp_dir)
    assert state.book is None
    assert state.source_epub_path is None
    assert state.annotation_path is None


def test_go_to_href_and_navigation_bounds(tmp_path: Path):
    epub_path = build_epub3(tmp_path / "book.epub")
    state = PreviewState()
    state.load_initial(str(epub_path))
    try:
        assert state.go_previous() is False  # już na pierwszym rozdziale
        assert state.go_to_href("chap2.xhtml") is True
        assert state.current_spine_index == 1
        assert state.go_next() is False  # już na ostatnim rozdziale
        assert state.go_to_href("nieistniejacy.xhtml") is False
    finally:
        state.cleanup()
