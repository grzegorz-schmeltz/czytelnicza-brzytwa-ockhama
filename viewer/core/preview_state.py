"""
core.preview_state
===================

Zarządzanie stanem podglądu: aktualnie wczytana książka, bieżący rozdział,
poziom powiększenia oraz cykl życia katalogów tymczasowych.

Ten moduł nie zależy od Qt - trzyma czysty stan aplikacji, dzięki czemu
warstwa UI (ui/main_window.py) pozostaje cienka i łatwa w utrzymaniu.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from typing import Optional

from core.epub_parser import EpubBook, parse_epub_book
from core.easyreader_annotations import apply_document_to_extracted
from core.safe_extract import ExtractionLimits, cleanup_temp_dir, safe_extract_epub
from core.sanitizer import sanitize_book_documents

logger = logging.getLogger("epub_viewer.state")


@dataclasses.dataclass
class ChapterPosition:
    """Zapamiętana pozycja czytelnika - używana do przywracania widoku po reloadzie.

    Pozycja przewinięcia/kotwica czytania NIE jest tu przechowywana - to
    czysto warstwowa (UI) sprawa, obsługiwana przez ui.webview
    (capture_reading_anchor/restore_reading_anchor), ponieważ wymaga
    odczytu z żywego DOM-u przez WebEngine, co nie ma sensu w tej,
    niezależnej od Qt/UI, warstwie stanu.
    """
    spine_href: Optional[str] = None
    zoom_factor: float = 1.0


class PreviewState:
    """
    Przechowuje aktualny stan podglądu EPUB-a: rozpakowaną książkę,
    bieżący indeks w spine oraz katalogi tymczasowe do posprzątania.
    """

    def __init__(self, extraction_limits: Optional[ExtractionLimits] = None):
        self.source_epub_path: Optional[str] = None
        self.annotation_path: Optional[str] = None
        self.book: Optional[EpubBook] = None
        self.current_temp_dir: Optional[str] = None
        self.current_spine_index: int = 0
        self.zoom_factor: float = 1.0
        self._extraction_limits = extraction_limits or ExtractionLimits()

    # ------------------------------------------------------------------ #
    # Ładowanie / przeładowanie
    # ------------------------------------------------------------------ #

    def load_initial(self, epub_path: str, annotation_path: Optional[str] = None) -> None:
        """Pierwsze wczytanie pliku EPUB (bez zachowywania poprzedniego stanu)."""
        new_temp_dir = safe_extract_epub(epub_path, limits=self._extraction_limits)
        try:
            book = parse_epub_book(new_temp_dir)
            sanitize_book_documents(book)
            if annotation_path:
                apply_document_to_extracted(annotation_path, epub_path, new_temp_dir)
        except Exception:
            cleanup_temp_dir(new_temp_dir)
            raise

        self.source_epub_path = epub_path
        self.annotation_path = annotation_path
        self.book = book
        self.current_temp_dir = new_temp_dir
        self.current_spine_index = 0

    def reload(self, epub_path: Optional[str] = None) -> ChapterPosition:
        """
        Przeładowuje książkę z dysku, starając się zachować bieżący rozdział.
        Stary katalog tymczasowy jest usuwany DOPIERO po pomyślnym wczytaniu
        nowej wersji (patrz wymaganie 13/14 specyfikacji).

        Zwraca ChapterPosition, którą warstwa UI może wykorzystać do
        przywrócenia przybliżonej pozycji w nowym dokumencie.
        """
        path = epub_path or self.source_epub_path
        if not path:
            raise ValueError("Brak ścieżki do pliku EPUB - nie można przeładować.")

        previous_href = None
        if self.book is not None and 0 <= self.current_spine_index < len(self.book.spine):
            previous_href = self.book.spine[self.current_spine_index]

        old_temp_dir = self.current_temp_dir

        new_temp_dir = safe_extract_epub(path, limits=self._extraction_limits)
        try:
            new_book = parse_epub_book(new_temp_dir)
            sanitize_book_documents(new_book)
            if self.annotation_path:
                apply_document_to_extracted(self.annotation_path, path, new_temp_dir)
        except Exception:
            # Nowa wersja niepoprawna - sprzątamy nowy katalog i zachowujemy stary podgląd.
            cleanup_temp_dir(new_temp_dir)
            raise

        # Od tego miejsca nowa wersja jest poprawna - można bezpiecznie przełączyć.
        self.book = new_book
        self.current_temp_dir = new_temp_dir
        self.current_spine_index = new_book.nearest_spine_index(previous_href)

        if old_temp_dir and old_temp_dir != new_temp_dir:
            cleanup_temp_dir(old_temp_dir)

        return ChapterPosition(
            spine_href=new_book.spine[self.current_spine_index] if new_book.spine else None,
            zoom_factor=self.zoom_factor,
        )

    # ------------------------------------------------------------------ #
    # Nawigacja
    # ------------------------------------------------------------------ #

    def current_chapter_path(self) -> Optional[str]:
        if self.book is None or not self.book.spine:
            return None
        href = self.book.spine[self.current_spine_index]
        return os.path.normpath(os.path.join(self.book.opf_dir, href))

    def current_chapter_href(self) -> Optional[str]:
        if self.book is None or not self.book.spine:
            return None
        return self.book.spine[self.current_spine_index]

    def go_next(self) -> bool:
        if self.book is None:
            return False
        if self.current_spine_index < len(self.book.spine) - 1:
            self.current_spine_index += 1
            return True
        return False

    def go_previous(self) -> bool:
        if self.book is None:
            return False
        if self.current_spine_index > 0:
            self.current_spine_index -= 1
            return True
        return False

    def go_to_href(self, href: str) -> bool:
        """Przechodzi do rozdziału wskazanego przez href (ignorując fragment '#...')."""
        if self.book is None:
            return False
        target = href.split("#")[0]
        for i, sp in enumerate(self.book.spine):
            if sp == target or os.path.basename(sp) == os.path.basename(target):
                self.current_spine_index = i
                return True
        return False

    def go_to_index(self, index: int) -> bool:
        if self.book is None:
            return False
        if 0 <= index < len(self.book.spine):
            self.current_spine_index = index
            return True
        return False

    # ------------------------------------------------------------------ #
    # Sprzątanie
    # ------------------------------------------------------------------ #

    def cleanup(self) -> None:
        """Usuwa bieżący katalog tymczasowy (np. przy zamknięciu programu)."""
        if self.current_temp_dir:
            cleanup_temp_dir(self.current_temp_dir)
            self.current_temp_dir = None
        self.book = None
        self.source_epub_path = None
        self.annotation_path = None
