"""
Nieautomatyczny (uruchamiany ręcznie) test dymny GUI: tworzy prawdziwe
MainWindow w trybie offscreen, otwiera przykładowy EPUB, sprawdza spine/TOC,
nawigację i podstawowe działanie okna - bez interakcji użytkownika.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.epub_builder import build_epub3  # noqa: E402


def main() -> int:
    import tempfile
    from pathlib import Path

    tmp_dir = Path(tempfile.mkdtemp(prefix="epubviewer_smoke_"))
    epub_path = build_epub3(tmp_dir / "smoke.epub")

    app = QApplication(sys.argv)

    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()

    print("[1/7] Okno utworzone poprawnie.")

    window.open_epub(str(epub_path))
    assert window.state.book is not None, "Ksiazka nie zostala wczytana"
    assert window.state.book.title == "Książka testowa - żółć"
    print("[2/7] EPUB otwarty poprawnie, tytul:", window.state.book.title)

    assert window.state.current_chapter_href() == "chap1.xhtml"
    print("[3/7] Pierwszy rozdzial wyswietlony:", window.state.current_chapter_href())

    assert window.toc_tree.topLevelItemCount() == 2, "TOC powinien miec 2 pozycje"
    print("[4/7] Spis tresci zawiera", window.toc_tree.topLevelItemCount(), "pozycje.")

    window._on_next_chapter()
    assert window.state.current_chapter_href() == "chap2.xhtml"
    print("[5/7] Nawigacja do nastepnego rozdzialu dziala:", window.state.current_chapter_href())

    assert window.coordinator.is_watching(), "Watcher powinien byc aktywny po otwarciu pliku"
    print("[6/7] Watchdog aktywnie obserwuje plik.")

    window.close()

    reopened_window = MainWindow()
    reopened_window.open_epub(str(epub_path))
    assert reopened_window.state.current_chapter_href() == "chap2.xhtml"
    print("[7/7] Po ponownym otwarciu przywrocono ostatni rozdzial.")
    reopened_window.close()
    app.quit()

    print("\nWSZYSTKIE SPRAWDZENIA DYMNE ZAKONCZONE SUKCESEM.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
