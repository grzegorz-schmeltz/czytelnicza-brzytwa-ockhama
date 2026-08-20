"""
Test dymny punktów 2 i 3 zgłoszenia:
  - otwarcie EPUB-a z niepasującym (przestarzałym) plikiem `.easyreader`
    NIE blokuje już otwarcia książki - książka otwiera się bez notatek,
    z jasnym powodem w `state.annotation_error`;
  - bezpieczny relink (`_on_relink_notes`) po potwierdzeniu użytkownika
    aktualizuje powiązanie i notatki zaczynają się pokazywać.

Modalne okna (QMessageBox) są tu automatycznie klikane w tle (patrz
`start_auto_click_message_boxes`) - bez tego test blokowałby się czekając
na kliknięcie, którego nikt w headless środowisku nie wykona.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from tests.epub_builder import build_epub3  # noqa: E402


def start_auto_click_message_boxes() -> QTimer:
    """QMessageBox.exec() blokuje wątek GUI aż do kliknięcia przycisku -
    w headless/testowym środowisku nikt by go nie kliknął. Zamiast
    monkeypatchować wewnętrzne metody Qt (zawodne - `.question()` i
    `.information()` to niezależne, częściowo zaimplementowane w C++
    funkcje statyczne, nie zawsze przechodzące przez nadpisywalny `.exec()`),
    używamy powtarzalnego QTimer, który wykrywa i klika aktywne modalne
    okno - to działa niezawodnie, bo callbacki QTimer nadal są dostarczane
    wewnątrz zagnieżdżonej pętli zdarzeń uruchomionej przez `exec()`.
    Preferuje przycisk "Yes", potem domyślny, potem pierwszy dostępny."""

    def _tick() -> None:
        widget = QApplication.activeModalWidget()
        if not isinstance(widget, QMessageBox):
            return
        yes_button = widget.button(QMessageBox.StandardButton.Yes)
        if yes_button is not None and yes_button.isVisible():
            yes_button.click()
            return
        default = widget.defaultButton()
        if default is not None:
            default.click()
            return
        buttons = widget.buttons()
        if buttons:
            buttons[0].click()

    timer = QTimer()
    timer.timeout.connect(_tick)
    timer.start(30)
    return timer


def close_and_wait(window, timeout_ms: int = 1500) -> None:
    from PySide6.QtCore import QEventLoop, QTimer

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

    from core.easyreader_annotations import BLOCK_RE, append_annotation, block_sha256, block_text_sha256, create_document

    tmp_dir = Path(tempfile.mkdtemp(prefix="epubviewer_smoke_relink_"))
    epub_path = build_epub3(tmp_dir / "book.epub")
    notes_path = tmp_dir / "book.easyreader"

    create_document(notes_path, epub_path, title="Książka testowa - żółć")
    with zipfile.ZipFile(epub_path) as archive:
        chapter = archive.read("OEBPS/chap1.xhtml").decode("utf-8")
    blocks = list(BLOCK_RE.finditer(chapter))
    target = blocks[1]
    append_annotation(
        notes_path,
        epub_path,
        {
            "id": "fragment-0001",
            "target": {
                "section": "OEBPS/chap1.xhtml",
                "raw_index": 1,
                "block_sha256": block_sha256(target.group(0)),
                "block_text_sha256": block_text_sha256(target.group(0)),
            },
            "content": {"prosty_jezyk": "Objaśnienie testowe."},
        },
    )

    app = QApplication(sys.argv)
    from ui.main_window import MainWindow

    window = MainWindow()
    window.show()

    clicker = start_auto_click_message_boxes()

    # Krok 1: EPUB techniczne zmodyfikowany (inny sha256) - notatki NIE
    # powinny zablokować otwarcia, tylko zgłosić niezgodność. `open_epub`
    # pokazuje w tej sytuacji modalny dialog (`_prompt_annotation_mismatch`,
    # domyślny przycisk "Otwórz bez notatek") - auto-klikacz go zamknie.
    edited_epub = tmp_dir / "book_v2.epub"
    edited_epub.write_bytes(epub_path.read_bytes() + b"\x00")

    window.open_epub(str(edited_epub), annotation_path=str(notes_path))
    assert window.state.book is not None, "Ksiazka powinna otworzyc sie mimo niezgodnych notatek"
    assert window.state.annotation_path is None
    assert window.state.annotation_error is not None
    print("[1/3] EPUB otwarty mimo niezgodnych notatek; annotation_error:", window.state.annotation_error)

    # Krok 2: bezpieczny relink po "potwierdzeniu" - auto-klikacz odpowie
    # "Yes" na oba modalne okna wywoływane przez _on_relink_notes
    # (pytanie potwierdzające i końcowe podsumowanie).
    window._on_relink_notes(str(notes_path))
    clicker.stop()

    assert window.state.annotation_path == str(notes_path), "Relink powinien powiazac notatki"
    assert window.state.annotation_error is None
    chapter_path = window.state.current_chapter_path()
    content = Path(chapter_path).read_text(encoding="utf-8")
    assert "Objaśnienie testowe." in content, "Notatki powinny byc widoczne po relinku"
    print("[2/3] Bezpieczny relink powiodl sie, notatki widoczne w tresci.")

    from core.easyreader_annotations import load_document

    document = load_document(notes_path)
    assert document["relink_history"], "Historia relinku powinna zostac zapisana"
    print("[3/3] Historia relinku zapisana w pliku notatek.")

    close_and_wait(window)
    app.quit()

    print("\nTEST RELINKU (PUNKTY 2 I 3) ZAKONCZONY SUKCESEM.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
