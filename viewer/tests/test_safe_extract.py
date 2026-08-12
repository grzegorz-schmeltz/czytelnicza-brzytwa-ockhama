from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from core.safe_extract import (
    EpubSecurityError,
    ExtractionLimits,
    cleanup_temp_dir,
    safe_extract_epub,
    cleanup_stale_preview_dirs,
)

from .epub_builder import (
    build_epub3,
    build_zip_slip_absolute_epub,
    build_zip_slip_epub,
)


def test_valid_epub_extracts_successfully(tmp_path: Path):
    epub_path = build_epub3(tmp_path / "ok.epub")
    dest = safe_extract_epub(str(epub_path), dest_parent_dir=str(tmp_path))
    try:
        assert os.path.isdir(dest)
        assert os.path.isfile(os.path.join(dest, "META-INF", "container.xml"))
        assert os.path.isfile(os.path.join(dest, "OEBPS", "chap1.xhtml"))
    finally:
        cleanup_temp_dir(dest)


def test_zip_slip_relative_traversal_is_blocked(tmp_path: Path):
    epub_path = build_zip_slip_epub(tmp_path / "evil_relative.epub")
    with pytest.raises(EpubSecurityError):
        safe_extract_epub(str(epub_path), dest_parent_dir=str(tmp_path))

    # Upewniamy się, że złośliwy plik NIE wylądował poza katalogiem docelowym.
    assert not (tmp_path.parent / "evil.txt").exists()


def test_zip_slip_absolute_path_is_blocked(tmp_path: Path):
    epub_path = build_zip_slip_absolute_epub(tmp_path / "evil_absolute.epub")
    with pytest.raises(EpubSecurityError):
        safe_extract_epub(str(epub_path), dest_parent_dir=str(tmp_path))

    assert not os.path.exists("/etc/evil.txt")


def test_partial_extraction_is_cleaned_up_on_failure(tmp_path: Path):
    epub_path = build_zip_slip_epub(tmp_path / "evil.epub")
    before = set(os.listdir(tmp_path))
    with pytest.raises(EpubSecurityError):
        safe_extract_epub(str(epub_path), dest_parent_dir=str(tmp_path))
    after = set(os.listdir(tmp_path))

    # Katalog tymczasowy utworzony podczas nieudanej próby powinien zostać usunięty.
    new_entries = after - before
    remaining_dirs = [e for e in new_entries if os.path.isdir(tmp_path / e)]
    assert remaining_dirs == []


def test_missing_file_raises_file_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        safe_extract_epub(str(tmp_path / "does_not_exist.epub"), dest_parent_dir=str(tmp_path))


def test_corrupt_zip_raises_bad_zip_file(tmp_path: Path):
    bad_path = tmp_path / "corrupt.epub"
    bad_path.write_bytes(b"to nie jest archiwum zip")
    with pytest.raises(zipfile.BadZipFile):
        safe_extract_epub(str(bad_path), dest_parent_dir=str(tmp_path))


def test_file_count_limit_enforced(tmp_path: Path):
    many_files_zip = tmp_path / "many.epub"
    with zipfile.ZipFile(many_files_zip, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")
        for i in range(10):
            zf.writestr(f"file_{i}.txt", "x")

    limits = ExtractionLimits(max_file_count=5)
    with pytest.raises(EpubSecurityError):
        safe_extract_epub(str(many_files_zip), limits=limits, dest_parent_dir=str(tmp_path))


def test_single_file_size_limit_enforced(tmp_path: Path):
    big_zip = tmp_path / "big.epub"
    with zipfile.ZipFile(big_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("big.txt", "a" * 10_000)

    limits = ExtractionLimits(max_single_file_bytes=1000)
    with pytest.raises(EpubSecurityError):
        safe_extract_epub(str(big_zip), limits=limits, dest_parent_dir=str(tmp_path))


def test_each_extraction_uses_a_new_unique_directory(tmp_path: Path):
    epub_path = build_epub3(tmp_path / "ok.epub")
    dest1 = safe_extract_epub(str(epub_path), dest_parent_dir=str(tmp_path))
    dest2 = safe_extract_epub(str(epub_path), dest_parent_dir=str(tmp_path))
    try:
        assert dest1 != dest2
        assert os.path.isdir(dest1)
        assert os.path.isdir(dest2)
    finally:
        cleanup_temp_dir(dest1)
        cleanup_temp_dir(dest2)
