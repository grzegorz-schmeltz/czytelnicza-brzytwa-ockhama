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
from core.easyreader_annotations import EasyReaderAnnotationError, apply_document_to_extracted
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
        self.annotation_error: Optional[str] = None
        self.annotation_skipped: list[dict] = []
        self.book: Optional[EpubBook] = None
        self.current_temp_dir: Optional[str] = None
        self.current_spine_index: int = 0
        self.zoom_factor: float = 1.0
        self._extraction_limits = extraction_limits or ExtractionLimits()

    # ------------------------------------------------------------------ #
    # Ładowanie / przeładowanie
    # ------------------------------------------------------------------ #

    def _apply_annotations_best_effort(self, annotation_path: str, epub_path: str, extracted_root: str) -> None:
        """Próbuje nałożyć notatki na już poprawnie rozpakowaną książkę.

        CELOWO nie pozwala niezgodnym/uszkodzonym notatkom zablokować
        otwarcia samego EPUB-a (patrz punkt 2 zgłoszenia) - książka, którą
        rozpakowaliśmy, jest zupełnie sprawna niezależnie od tego, czy plik
        notatek do niej pasuje. Wynik trafia do `self.annotation_error`
        (`None` przy sukcesie), a warstwa UI decyduje, jak to zakomunikować
        (np. zaproponować wybór innego pliku albo bezpieczny relink).

        Adnotacje, których nie dało się PEWNIE umiejscowić (patrz punkt 1 -
        `apply_document_to_extracted` wstawia tylko pewne dopasowania), nie
        są traktowane jako błąd - trafiają do `self.annotation_skipped`,
        żeby UI mogło pokazać, ile komentarzy nie zostało wyświetlonych.
        """
        self.annotation_skipped = []
        try:
            report = apply_document_to_extracted(annotation_path, epub_path, extracted_root)
        except EasyReaderAnnotationError as exc:
            logger.warning("Nie zastosowano notatek %s: %s", annotation_path, exc)
            self.annotation_path = None
            self.annotation_error = str(exc)
        else:
            self.annotation_path = annotation_path
            self.annotation_error = None
            self.annotation_skipped = report.skipped
            if report.skipped:
                logger.info(
                    "Zastosowano %d adnotacji, pominięto %d (niepewne dopasowanie): %s",
                    report.applied, len(report.skipped),
                    ", ".join(item["id"] for item in report.skipped),
                )

    def load_initial(self, epub_path: str, annotation_path: Optional[str] = None) -> None:
        """Pierwsze wczytanie pliku EPUB (bez zachowywania poprzedniego stanu)."""
        new_temp_dir = safe_extract_epub(epub_path, limits=self._extraction_limits)
        try:
            book = parse_epub_book(new_temp_dir)
            sanitize_book_documents(book)
        except Exception:
            cleanup_temp_dir(new_temp_dir)
            raise

        self.source_epub_path = epub_path
        self.annotation_path = None
        self.annotation_error = None
        self.book = book
        self.current_temp_dir = new_temp_dir
        self.current_spine_index = 0

        if annotation_path:
            self._apply_annotations_best_effort(annotation_path, epub_path, new_temp_dir)

    def try_apply_annotations(self, annotation_path: str) -> bool:
        """Próbuje (ponownie) nałożyć wskazany plik notatek na JUŻ wczytaną
        książkę - bez pełnego przeładowania z dysku. Używane przez UI po
        tym, jak użytkownik ręcznie wybierze inny plik `.easyreader` albo
        potwierdzi bezpieczny relink (patrz punkty 2 i 3 zgłoszenia).
        Zwraca True przy sukcesie; szczegóły błędu (jeśli jakiś) trafiają
        do `self.annotation_error`, tak samo jak przy `load_initial`.
        """
        if not self.source_epub_path or not self.current_temp_dir:
            self.annotation_error = "Najpierw otwórz książkę."
            return False
        self._apply_annotations_best_effort(annotation_path, self.source_epub_path, self.current_temp_dir)
        return self.annotation_path == annotation_path

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
        except Exception:
            # Nowa wersja niepoprawna - sprzątamy nowy katalog i zachowujemy stary podgląd.
            cleanup_temp_dir(new_temp_dir)
            raise

        # Od tego miejsca nowa wersja EPUB-a jest poprawna - można bezpiecznie
        # przełączyć podgląd, NIEZALEŻNIE od tego, czy notatki nadal pasują
        # (patrz `_apply_annotations_best_effort` / punkt 2 zgłoszenia:
        # niezgodne notatki nie mogą zamrozić podglądu na starej wersji).
        if self.annotation_path:
            self._apply_annotations_best_effort(self.annotation_path, path, new_temp_dir)

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
        self.annotation_error = None
        self.annotation_skipped = []
