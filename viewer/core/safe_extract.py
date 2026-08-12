"""
core.safe_extract
==================

Bezpieczne rozpakowywanie archiwum EPUB (ZIP) do katalogu tymczasowego.

EPUB jest traktowany jako NIEZAUFANE archiwum pochodzące z zewnątrz, dlatego
moduł ten:

  * chroni przed atakiem typu "Zip Slip" (wpisy z ".." lub ścieżkami
    bezwzględnymi, które próbują zapisać plik poza katalogiem docelowym),
  * odrzuca dowiązania symboliczne zapisane wewnątrz archiwum,
  * ogranicza łączny rozmiar rozpakowanych danych oraz liczbę plików,
  * ogranicza rozmiar pojedynczego pliku oraz "podejrzany" współczynnik
    kompresji (ochrona przed tzw. zip-bombami),
  * nie wykonuje żadnego kodu zawartego w archiwum.

Moduł nie zależy od Qt, dzięki czemu można go łatwo testować jednostkowo.
"""

from __future__ import annotations

import dataclasses
import os
import stat
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Optional


class EpubSecurityError(Exception):
    """Zgłaszany, gdy archiwum EPUB narusza zasady bezpiecznego rozpakowania."""


@dataclasses.dataclass
class ExtractionLimits:
    """Limity bezpieczeństwa stosowane podczas rozpakowywania archiwum."""

    max_total_uncompressed_bytes: int = 300 * 1024 * 1024  # 300 MB łącznie
    max_single_file_bytes: int = 60 * 1024 * 1024           # 60 MB / plik
    max_file_count: int = 20_000
    # Jeżeli skompresowany rozmiar pliku > 0, a stosunek rozmiaru
    # rozpakowanego do skompresowanego przekracza tę wartość, uznajemy
    # to za potencjalną "zip-bombę".
    max_compression_ratio: int = 200


def _is_within_directory(base_dir: Path, target: Path) -> bool:
    """Sprawdza, czy `target` znajduje się wewnątrz `base_dir` (po normalizacji)."""
    try:
        base_resolved = base_dir.resolve()
        target_resolved = target.resolve()
    except OSError:
        return False
    return base_resolved == target_resolved or base_resolved in target_resolved.parents


def _looks_like_symlink(info: zipfile.ZipInfo) -> bool:
    """Wykrywa wpisy ZIP oznaczone jako dowiązania symboliczne (Unix)."""
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(unix_mode) if unix_mode else False


def safe_extract_epub(
    epub_path: str,
    limits: Optional[ExtractionLimits] = None,
    dest_parent_dir: Optional[str] = None,
) -> str:
    """
    Bezpiecznie rozpakowuje plik EPUB do nowego katalogu tymczasowego.

    Zwraca ścieżkę do katalogu, w którym rozpakowano zawartość.
    W razie naruszenia zasad bezpieczeństwa zgłasza EpubSecurityError.
    Katalog tymczasowy tworzony jest zawsze jako NOWY (unikalna nazwa),
    aby stary podgląd mógł nadal korzystać z poprzedniej wersji aż do
    potwierdzenia poprawności nowej.
    """
    limits = limits or ExtractionLimits()

    if not os.path.isfile(epub_path):
        raise FileNotFoundError(epub_path)

    dest_dir = tempfile.mkdtemp(prefix="epubviewer_", dir=dest_parent_dir)
    dest_path = Path(dest_dir)

    try:
        with zipfile.ZipFile(epub_path, "r") as zf:
            infos = zf.infolist()

            if len(infos) > limits.max_file_count:
                raise EpubSecurityError(
                    f"Archiwum zawiera zbyt wiele plików ({len(infos)} > "
                    f"{limits.max_file_count})."
                )

            total_uncompressed = 0
            for info in infos:
                # Katalogi pomijamy przy walidacji rozmiaru, ale tworzymy je.
                name = info.filename

                if name.startswith("/") or name.startswith("\\"):
                    raise EpubSecurityError(f"Niedozwolona ścieżka bezwzględna w archiwum: {name}")

                # Normalizacja separatorów (ZIP zawsze używa '/').
                normalized = os.path.normpath(name)
                if normalized.startswith("..") or os.path.isabs(normalized):
                    raise EpubSecurityError(f"Wykryto próbę Zip Slip: {name}")

                target_path = dest_path / normalized
                if not _is_within_directory(dest_path, target_path.parent if not name.endswith("/") else target_path):
                    raise EpubSecurityError(f"Wpis archiwum wychodzi poza katalog docelowy: {name}")

                if _looks_like_symlink(info):
                    raise EpubSecurityError(f"Archiwum zawiera dowiązanie symboliczne: {name}")

                if info.is_dir():
                    continue

                if info.file_size > limits.max_single_file_bytes:
                    raise EpubSecurityError(
                        f"Plik '{name}' przekracza dopuszczalny rozmiar "
                        f"({info.file_size} > {limits.max_single_file_bytes} B)."
                    )

                if info.compress_size > 0:
                    ratio = info.file_size / max(info.compress_size, 1)
                    if ratio > limits.max_compression_ratio and info.file_size > 1024 * 1024:
                        raise EpubSecurityError(
                            f"Podejrzanie wysoki współczynnik kompresji dla '{name}' "
                            f"({ratio:.1f}x) - możliwa zip-bomba."
                        )

                total_uncompressed += info.file_size
                if total_uncompressed > limits.max_total_uncompressed_bytes:
                    raise EpubSecurityError(
                        "Łączny rozmiar rozpakowanych danych przekracza dopuszczalny limit "
                        f"({limits.max_total_uncompressed_bytes} B)."
                    )

            # Wszystkie wpisy zwalidowane - dopiero teraz faktycznie rozpakowujemy.
            for info in infos:
                normalized = os.path.normpath(info.filename)
                target_path = dest_path / normalized

                if info.is_dir():
                    target_path.mkdir(parents=True, exist_ok=True)
                    continue

                target_path.parent.mkdir(parents=True, exist_ok=True)

                if not _is_within_directory(dest_path, target_path):
                    # Podwójna kontrola tuż przed zapisem.
                    raise EpubSecurityError(
                        f"Wpis archiwum wychodzi poza katalog docelowy: {info.filename}"
                    )

                with zf.open(info, "r") as src, open(target_path, "wb") as dst:
                    _copy_with_limit(src, dst, limits.max_single_file_bytes)

        return str(dest_path)

    except (zipfile.BadZipFile, EpubSecurityError, FileNotFoundError, OSError):
        # Sprzątamy częściowo rozpakowany katalog i przekazujemy wyjątek dalej.
        _cleanup_dir(dest_path)
        raise


def _copy_with_limit(src, dst, limit_bytes: int, chunk_size: int = 1024 * 1024) -> None:
    written = 0
    while True:
        chunk = src.read(chunk_size)
        if not chunk:
            break
        written += len(chunk)
        if written > limit_bytes:
            raise EpubSecurityError("Przekroczono limit rozmiaru podczas rozpakowywania pliku.")
        dst.write(chunk)


def _cleanup_dir(path: Path) -> None:
    import shutil

    try:
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def cleanup_temp_dir(path: Optional[str]) -> None:
    """Publiczna funkcja pomocnicza do usuwania starych katalogów tymczasowych."""
    if not path:
        return
    _cleanup_dir(Path(path))


def cleanup_stale_preview_dirs(max_age_seconds: int = 24 * 60 * 60) -> int:
    """Usuwa stare katalogi ``epubviewer_*`` pozostawione po awarii programu.

    Funkcja dotyka wyłącznie katalogów o naszym jednoznacznym prefiksie,
    położonych bezpośrednio w prywatnym katalogu tymczasowym użytkownika.
    """
    temp_root = Path(tempfile.gettempdir()).resolve()
    now = time.time()
    removed = 0
    for candidate in temp_root.glob("epubviewer_*"):
        try:
            resolved = candidate.resolve()
            if candidate.is_dir() and resolved.parent == temp_root:
                age = now - candidate.stat().st_mtime
                if age >= max_age_seconds:
                    _cleanup_dir(candidate)
                    if not candidate.exists():
                        removed += 1
        except OSError:
            continue
    return removed
