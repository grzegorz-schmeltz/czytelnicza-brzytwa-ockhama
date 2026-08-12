#!/usr/bin/env python3
"""
Podgląd EPUB na żywo - punkt wejścia aplikacji.

Użycie:
    python main.py [--annotations notatki.easyreader] [książka.epub]

Program otwiera podany plik EPUB (jeśli podano) wyłącznie do bezpiecznego
czytania z opcjonalnym, oddzielnym plikiem notatek.
"""

from __future__ import annotations

import logging
import os
import sys
import argparse

# Wyłączamy sprzętową akcelerację GPU dla Qt WebEngine w środowiskach, gdzie
# bywa niestabilna (typowe dla maszyn wirtualnych / niektórych sterowników
# Windows). Musi być ustawione PRZED importem modułów QtWebEngine.
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from core.logging_config import setup_logging  # noqa: E402
from core.safe_extract import cleanup_stale_preview_dirs  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Brzytwa Ockhama — Czytnik Prometejski")
    parser.add_argument("epub", nargs="?", help="Plik EPUB do otwarcia")
    parser.add_argument("--annotations", help="Zewnętrzny plik notatek .easyreader")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args(sys.argv[1:])
    log_path = setup_logging(level=logging.INFO)
    logger = logging.getLogger("epub_viewer.main")
    logger.info("Uruchamianie aplikacji. Plik dziennika: %s", log_path)

    cleanup_stale_preview_dirs()
    QApplication.setApplicationName("Brzytwa Ockhama — Czytnik Prometejski")
    QApplication.setOrganizationName("EpubViewerApp")

    app = QApplication(sys.argv)

    window = MainWindow()
    window.show()

    initial_path: str | None = None
    if args.epub:
        candidate = args.epub
        if os.path.isfile(candidate):
            initial_path = os.path.abspath(candidate)
        else:
            QMessageBox.warning(window, "Błąd", f"Podany plik nie istnieje:\n{candidate}")

    if not initial_path:
        last_file = window.last_opened_file()
        if last_file and os.path.isfile(last_file):
            initial_path = last_file

    if initial_path:
        window.open_epub(initial_path, annotation_path=args.annotations)

    try:
        return app.exec()
    except Exception:  # noqa: BLE001
        logger.exception("Nieoczekiwany błąd krytyczny aplikacji.")
        raise


if __name__ == "__main__":
    sys.exit(main())
