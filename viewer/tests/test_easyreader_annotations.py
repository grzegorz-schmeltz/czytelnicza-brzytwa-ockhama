from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import pytest

from core.easyreader_annotations import (
    BLOCK_RE,
    EasyReaderAnnotationError,
    append_annotation,
    block_sha256,
    block_text_sha256,
    create_document,
)
from core.notes_export import export_notes_epub
from core.preview_state import PreviewState

from .epub_builder import build_epub3


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def test_notes_are_rejected_for_a_different_epub(tmp_path: Path):
    epub = build_epub3(tmp_path / "book.epub")
    notes = tmp_path / "book.easyreader"
    make_annotation(epub, notes)
    changed = build_epub3(tmp_path / "changed.epub")
    changed.write_bytes(changed.read_bytes() + b"changed")

    state = PreviewState()
    with pytest.raises(EasyReaderAnnotationError):
        state.load_initial(str(changed), annotation_path=str(notes))


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
