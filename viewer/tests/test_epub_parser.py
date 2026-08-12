from __future__ import annotations

from pathlib import Path

from core.epub_parser import EpubParseError, parse_epub_book
from core.safe_extract import safe_extract_epub

from .epub_builder import build_epub2, build_epub3


def test_parse_epub3_spine_and_title(tmp_path: Path):
    epub_path = build_epub3(tmp_path / "book3.epub")
    extracted = safe_extract_epub(str(epub_path), dest_parent_dir=str(tmp_path))
    book = parse_epub_book(extracted)

    assert book.title == "Książka testowa - żółć"
    assert book.spine == ["chap1.xhtml", "chap2.xhtml"]


def test_parse_epub3_nav_toc(tmp_path: Path):
    epub_path = build_epub3(tmp_path / "book3.epub")
    extracted = safe_extract_epub(str(epub_path), dest_parent_dir=str(tmp_path))
    book = parse_epub_book(extracted)

    assert len(book.toc) == 2
    assert book.toc[0].title == "Rozdział pierwszy - żółw"
    assert book.toc[0].href == "chap1.xhtml"
    assert book.toc[1].href == "chap2.xhtml"


def test_parse_epub2_ncx_fallback(tmp_path: Path):
    epub_path = build_epub2(tmp_path / "book2.epub")
    extracted = safe_extract_epub(str(epub_path), dest_parent_dir=str(tmp_path))
    book = parse_epub_book(extracted)

    assert book.spine == ["chap1.xhtml"]
    assert len(book.toc) == 1
    assert book.toc[0].title == "Rozdział pierwszy"
    assert book.toc[0].href == "chap1.xhtml"


def test_missing_container_raises(tmp_path: Path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    try:
        parse_epub_book(str(empty_dir))
        assert False, "Powinien zostać zgłoszony EpubParseError"
    except EpubParseError:
        pass


def test_nearest_spine_index_exact_match(tmp_path: Path):
    epub_path = build_epub3(tmp_path / "book3.epub")
    extracted = safe_extract_epub(str(epub_path), dest_parent_dir=str(tmp_path))
    book = parse_epub_book(extracted)

    assert book.nearest_spine_index("chap2.xhtml") == 1


def test_nearest_spine_index_fallback_to_first_when_missing(tmp_path: Path):
    epub_path = build_epub3(tmp_path / "book3.epub")
    extracted = safe_extract_epub(str(epub_path), dest_parent_dir=str(tmp_path))
    book = parse_epub_book(extracted)

    # Rozdział, który nie istnieje w tej książce w ogóle - brak dopasowania po nazwie.
    assert book.nearest_spine_index("nieistniejacy_rozdzial_xyz.xhtml") == 0


def test_nearest_spine_index_matches_by_basename(tmp_path: Path):
    epub_path = build_epub3(tmp_path / "book3.epub")
    extracted = safe_extract_epub(str(epub_path), dest_parent_dir=str(tmp_path))
    book = parse_epub_book(extracted)

    # Symulacja: w nowej wersji plik miałby inną ścieżkę względną, ale tę samą nazwę.
    assert book.nearest_spine_index("subdir/chap2.xhtml") == 1
