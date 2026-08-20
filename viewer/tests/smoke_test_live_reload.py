"""
Test dymny na żywo: sprawdza, że po nadpisaniu pliku EPUB na dysku
(symulacja zewnętrznego edytora) aplikacja automatycznie wykrywa zmianę
(watchdog + debounce), bezpiecznie przeładowuje książkę i aktualizuje widok,
zachowując przy tym bieżący rozdział.
"""
from __future__ import annotations

import logging
import os
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.epub_builder import CHAP2_XHTML, build_epub3  # noqa: E402


def close_and_wait(window, timeout_ms: int = 1500) -> None:
    """Patrz tests/smoke_test.py - closeEvent teraz odracza faktyczne
    zamknięcie, żeby zdążyć asynchronicznie zapisać pozycję czytania."""
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


def main() -> int:
    import tempfile
    import zipfile
    from pathlib import Path

    tmp_dir = Path(tempfile.mkdtemp(prefix="epubviewer_smoke_live_"))
    epub_path = build_epub3(tmp_dir / "live.epub")

    app = QApplication(sys.argv)

    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()
    window.open_epub(str(epub_path))

    # Przechodzimy na drugi rozdział, żeby zweryfikować zachowanie pozycji po reloadzie.
    window._on_next_chapter()
    assert window.state.current_chapter_href() == "chap2.xhtml"
    print("[1/4] Otwarto EPUB i przeszlismy do rozdzialu 2.")

    result = {"reloaded": False, "failed_msg": None}

    def on_success(_position):
        result["reloaded"] = True

    def on_failed(msg):
        result["failed_msg"] = msg

    window.coordinator.reload_succeeded.connect(on_success)
    window.coordinator.reload_failed.connect(on_failed)

    time.sleep(0.3)  # dajemy watchdogowi chwilę na uruchomienie się

    # Symulujemy zewnętrzny edytor: zmieniamy treść rozdzialu 2 i zapisujemy
    # NOWĄ wersję EPUB pod tą samą ścieżką (typowy zapis "in place").
    new_chap2 = CHAP2_XHTML.replace("Koniec książki testowej.", "ZMODYFIKOWANA TREŚĆ - test live reload.")

    from tests.epub_builder import CHAP1_XHTML, CONTAINER_XML, NAV_XHTML, OPF_EPUB3, STYLE_CSS

    tmp_write = tmp_dir / "live.epub.tmp"
    with zipfile.ZipFile(tmp_write, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip")
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        zf.writestr("OEBPS/content.opf", OPF_EPUB3)
        zf.writestr("OEBPS/nav.xhtml", NAV_XHTML)
        zf.writestr("OEBPS/chap1.xhtml", CHAP1_XHTML)
        zf.writestr("OEBPS/chap2.xhtml", new_chap2)
        zf.writestr("OEBPS/style.css", STYLE_CSS)
    os.replace(tmp_write, epub_path)  # zapis atomowy - typowy dla edytorow/skryptow
    print("[2/4] Nadpisano plik EPUB na dysku (symulacja zewnetrznej edycji).")

    # Pętla zdarzeń czekająca maks. 10 s na sygnał reload_succeeded/reload_failed.
    loop = QEventLoop()
    timeout_timer = QTimer()
    timeout_timer.setSingleShot(True)
    timeout_timer.timeout.connect(loop.quit)

    def check_done():
        if result["reloaded"] or result["failed_msg"] is not None:
            loop.quit()

    poll = QTimer()
    poll.timeout.connect(check_done)
    poll.start(100)
    timeout_timer.start(10000)
    loop.exec()

    assert result["failed_msg"] is None, f"Przeladowanie nieudane: {result['failed_msg']}"
    assert result["reloaded"], "Nie otrzymano sygnalu reload_succeeded w wyznaczonym czasie"
    print("[3/4] Automatyczne przeladowanie zakonczone sukcesem (sygnal reload_succeeded).")

    assert window.state.current_chapter_href() == "chap2.xhtml", "Rozdzial powinien zostac zachowany po reloadzie"
    chapter_path = window.state.current_chapter_path()
    content = Path(chapter_path).read_text(encoding="utf-8")
    assert "ZMODYFIKOWANA TREŚĆ" in content, "Nowa tresc powinna byc widoczna po reloadzie"
    print("[4/4] Rozdzial zachowany, nowa tresc widoczna:", window.state.current_chapter_href())

    close_and_wait(window)
    app.quit()

    print("\nTEST NA ZYWO (AUTO-RELOAD) ZAKONCZONY SUKCESEM.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
