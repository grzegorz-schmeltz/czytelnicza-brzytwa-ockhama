"""
Test dymny funkcji znaczników i pozycji czytania (punkt 9 zgłoszenia:
"znaczniki i pozycja czytania nadal działają"). Sprawdza przez prawdziwe
MainWindow:
  - "Ustaw znacznik" (`action_set_marker` / `_set_reading_marker`) zapisuje
    bieżący rozdział i widoczny akapit,
  - po przejściu do innego rozdziału "Wróć do znacznika"
    (`action_go_to_marker` / `_go_to_reading_marker`) poprawnie wraca,
  - trwała pozycja czytania (osobna od znacznika, zapisywana okresowo i
    przy zamknięciu) przetrwa zamknięcie i ponowne otwarcie tej samej
    książki.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.epub_builder import build_epub3  # noqa: E402


def close_and_wait(window, timeout_ms: int = 1500) -> None:
    window.close()
    loop = QEventLoop()

    def _check():
        if getattr(window, "_close_confirmed", False):
            loop.quit()

    poll = QTimer()
    poll.timeout.connect(_check)
    poll.start(20)
    QTimer.singleShot(timeout_ms, loop.quit)
    if not getattr(window, "_close_confirmed", False):
        loop.exec()


def pump(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def main() -> int:
    import tempfile
    from pathlib import Path

    tmp_dir = Path(tempfile.mkdtemp(prefix="epubviewer_smoke_marker_"))
    epub_path = build_epub3(tmp_dir / "book.epub")

    app = QApplication(sys.argv)
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.open_epub(str(epub_path))
    pump(200)  # dajemy stronie chwilę na załadowanie się (loadFinished)

    assert window.state.current_chapter_href() == "chap1.xhtml"
    print("[1/6] Otwarto ksiazke, jestesmy na chap1.")

    # "Ustaw znacznik" na rozdziale 1.
    window._set_reading_marker()
    pump(200)
    marker = window._load_reading_marker()
    assert marker is not None, "Znacznik powinien zostac zapisany"
    assert marker["chapter_href"] == "chap1.xhtml"
    assert window.action_go_to_marker.isEnabled(), "Przycisk 'Wroc do znacznika' powinien byc aktywny"
    print("[2/6] Znacznik ustawiony na chap1, przycisk 'Wroc do znacznika' aktywny.")

    # Przechodzimy do rozdzialu 2 - znacznik powinien pozostac na chap1.
    window._on_next_chapter()
    pump(100)
    assert window.state.current_chapter_href() == "chap2.xhtml"
    print("[3/6] Przeszlismy do chap2.")

    # "Wroc do znacznika" - powinnismy wrocic na chap1.
    window._go_to_reading_marker()
    pump(200)
    assert window.state.current_chapter_href() == "chap1.xhtml", "Powrot do znacznika powinien przywrocic chap1"
    print("[4/6] 'Wroc do znacznika' poprawnie przywrocilo chap1.")

    # Trwala pozycja czytania: przechodzimy do chap2, zamykamy program,
    # otwieramy ponownie te sama ksiazke - powinnismy wrocic na chap2.
    window._on_next_chapter()
    pump(100)
    assert window.state.current_chapter_href() == "chap2.xhtml"
    close_and_wait(window)
    print("[5/6] Zamknieto program na chap2 (trwala pozycja powinna zostac zapisana).")

    reopened = MainWindow()
    reopened.open_epub(str(epub_path))
    pump(200)
    assert reopened.state.current_chapter_href() == "chap2.xhtml", "Powinnismy wrocic tam, gdzie zamknieto program"
    print("[6/6] Po ponownym otwarciu przywrocono chap2 - trwala pozycja dziala.")

    close_and_wait(reopened)
    app.quit()

    print("\nTEST ZNACZNIKOW I POZYCJI CZYTANIA ZAKONCZONY SUKCESEM.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
