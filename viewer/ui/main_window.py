"""
ui.main_window
===============

Główne okno aplikacji "Podgląd EPUB na żywo".

Łączy ze sobą:
  * core.preview_state.PreviewState  - stan aktualnie wczytanej książki,
  * core.reload_worker.ReloadCoordinator - obserwację i bezpieczne
    przeładowywanie w tle,
  * ui.webview.BookWebView - bezpieczne wyświetlanie rozdziałów XHTML.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime

from PySide6.QtCore import QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QWidget,
)

from core.epub_parser import EpubParseError, TocEntry
from core.easyreader_annotations import EasyReaderAnnotationError, analyze_relink, commit_relink
from core.notes_export import NotesExportError, export_notes_epub
from core.preview_state import ChapterPosition, PreviewState
from core.reload_worker import ReloadCoordinator
from core.safe_extract import EpubSecurityError
from ui.webview import BookWebView

logger = logging.getLogger("epub_viewer.ui")

VIEWER_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

LIGHT_STYLESHEET = ""  # domyślny (systemowy) wygląd Qt
DARK_STYLESHEET = """
QWidget { background-color: #232629; color: #e0e0e0; }
QMainWindow, QDockWidget, QToolBar { background-color: #2b2e31; }
QTreeWidget { background-color: #1f2224; color: #e0e0e0; alternate-background-color: #26292c; }
QTreeWidget::item:selected { background-color: #3a6ea5; }
QLineEdit, QToolBar QToolButton { color: #e0e0e0; }
QStatusBar { background-color: #2b2e31; color: #cfcfcf; }
QMenuBar, QMenu { background-color: #2b2e31; color: #e0e0e0; }
QMenu::item:selected { background-color: #3a6ea5; }
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.annotation_path: str | None = None
        self.setWindowTitle("Brzytwa Ockhama — Czytnik Prometejski")
        self.resize(1100, 780)
        self.setAcceptDrops(True)

        # Plik INI obok programu jest pewniejszy niż rejestr Windows i pozwala
        # zachować ustawienia również przy uruchamianiu viewera z pliku BAT.
        self.settings = QSettings(
            os.path.join(VIEWER_ROOT, "reader_settings.ini"),
            QSettings.Format.IniFormat,
        )
        self.state = PreviewState()
        self.coordinator = ReloadCoordinator(self.state)
        self._pending_anchor: dict | None = None
        self._last_reading_anchor: dict | None = None
        self._anchor_hook_call_id: int = 0
        self._is_dark_theme = False

        self._build_ui()
        self._connect_signals()
        self._restore_settings()
        self._reading_position_timer = QTimer(self)
        self._reading_position_timer.setInterval(2000)
        self._reading_position_timer.timeout.connect(self._save_precise_reading_position)
        self._reading_position_timer.start()

    # ------------------------------------------------------------------ #
    # Budowa interfejsu
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        # --- widok WWW ---
        self.web_view = BookWebView(opf_dir_provider=lambda: self.state.book.opf_dir if self.state.book else None)
        self.setCentralWidget(self.web_view)

        # --- panel spisu treści ---
        self.toc_tree = QTreeWidget()
        self.toc_tree.setHeaderHidden(True)
        self.toc_dock = QDockWidget("Spis treści", self)
        self.toc_dock.setWidget(self.toc_tree)
        self.toc_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetMovable | QDockWidget.DockWidgetFeature.DockWidgetClosable
        )
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.toc_dock)

        # --- pasek narzędzi ---
        toolbar = QToolBar("Główny pasek narzędzi", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        self.action_open = QAction("Otwórz EPUB", self)
        self.action_open.setShortcut(QKeySequence.StandardKey.Open)
        toolbar.addAction(self.action_open)

        toolbar.addSeparator()

        self.action_prev = QAction("◀ Poprzedni rozdział", self)
        self.action_prev.setShortcut(QKeySequence("Ctrl+Left"))
        toolbar.addAction(self.action_prev)

        self.action_next = QAction("Następny rozdział ▶", self)
        self.action_next.setShortcut(QKeySequence("Ctrl+Right"))
        toolbar.addAction(self.action_next)

        toolbar.addSeparator()

        self.action_set_marker = QAction("🔖 Ustaw znacznik", self)
        self.action_set_marker.setShortcut(QKeySequence("Ctrl+M"))
        toolbar.addAction(self.action_set_marker)

        self.action_go_to_marker = QAction("↩ Wróć do znacznika", self)
        self.action_go_to_marker.setShortcut(QKeySequence("Ctrl+Shift+M"))
        self.action_go_to_marker.setEnabled(False)
        toolbar.addAction(self.action_go_to_marker)

        toolbar.addSeparator()

        self.action_zoom_out = QAction("Pomniejsz (A-)", self)
        self.action_zoom_out.setShortcut(QKeySequence("Ctrl+-"))
        toolbar.addAction(self.action_zoom_out)

        self.action_zoom_in = QAction("Powiększ (A+)", self)
        self.action_zoom_in.setShortcut(QKeySequence("Ctrl+="))
        toolbar.addAction(self.action_zoom_in)

        toolbar.addSeparator()

        self.action_reload_now = QAction("⟳ Przeładuj teraz", self)
        self.action_reload_now.setShortcut(QKeySequence("F5"))
        toolbar.addAction(self.action_reload_now)

        self.auto_reload_checkbox = QCheckBox("Automatyczne przeładowanie")
        self.auto_reload_checkbox.setChecked(True)
        toolbar.addWidget(self.auto_reload_checkbox)

        # --- menu ---
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("Plik")
        file_menu.addAction(self.action_open)
        self.action_export_notes = QAction("Eksportuj notatki do EPUB…", self)
        self.action_export_notes.setEnabled(False)
        file_menu.addAction(self.action_export_notes)
        self.action_choose_notes = QAction("Wybierz plik notatek…", self)
        file_menu.addAction(self.action_choose_notes)
        self.action_relink_notes = QAction("Połącz ponownie notatki z tą książką…", self)
        file_menu.addAction(self.action_relink_notes)
        exit_action = QAction("Zakończ", self)
        exit_action.setShortcut(QKeySequence.StandardKey.Quit)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        view_menu = menu_bar.addMenu("Widok")
        self.action_toggle_theme = QAction("Przełącz motyw jasny/ciemny", self)
        view_menu.addAction(self.action_toggle_theme)
        view_menu.addAction(self.action_zoom_in)
        view_menu.addAction(self.action_zoom_out)
        view_menu.addAction(self.toc_dock.toggleViewAction())

        help_menu = menu_bar.addMenu("Pomoc")
        about_action = QAction("O programie", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

        # --- pasek stanu ---
        status = self.statusBar()
        self.status_filename = QLabel("Brak otwartego pliku")
        self.status_chapter = QLabel("")
        self.status_reload_time = QLabel("")
        self.status_watcher = QLabel("Obserwacja: nieaktywna")
        self.status_annotations = QLabel("")
        for w in (
            self.status_filename,
            self.status_annotations,
            self.status_chapter,
            self.status_reload_time,
            self.status_watcher,
        ):
            status.addPermanentWidget(w)
            w.setContentsMargins(8, 0, 8, 0)

    def _connect_signals(self) -> None:
        self.action_open.triggered.connect(self._on_open_clicked)
        self.action_export_notes.triggered.connect(self._on_export_notes)
        self.action_choose_notes.triggered.connect(self._on_choose_notes_file)
        self.action_relink_notes.triggered.connect(self._on_relink_notes_from_menu)
        self.action_prev.triggered.connect(self._on_prev_chapter)
        self.action_next.triggered.connect(self._on_next_chapter)
        self.action_set_marker.triggered.connect(self._set_reading_marker)
        self.action_go_to_marker.triggered.connect(self._go_to_reading_marker)
        self.action_zoom_in.triggered.connect(lambda: self._change_zoom(0.1))
        self.action_zoom_out.triggered.connect(lambda: self._change_zoom(-0.1))
        self.action_reload_now.triggered.connect(self.coordinator.trigger_manual_reload)
        self.action_toggle_theme.triggered.connect(self._toggle_theme)
        self.auto_reload_checkbox.toggled.connect(self._on_auto_reload_toggled)

        self.toc_tree.itemClicked.connect(self._on_toc_item_clicked)

        self.web_view.internal_link_activated.connect(self._on_internal_link_activated)
        self.web_view.external_link_requested.connect(self._on_external_link_requested)

        self.coordinator.reload_succeeded.connect(self._on_reload_succeeded)
        self.coordinator.reload_failed.connect(self._on_reload_failed)
        self.coordinator.watcher_status_changed.connect(self._on_watcher_status_changed)
        self.coordinator.set_pre_reload_hook(self._capture_anchor_before_reload)

    # ------------------------------------------------------------------ #
    # Otwieranie pliku
    # ------------------------------------------------------------------ #

    def _on_open_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Wybierz plik EPUB", "", "Pliki EPUB (*.epub)")
        if path:
            self.open_epub(path)

    def open_epub(self, path: str, annotation_path: str | None = None) -> None:
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            QMessageBox.warning(self, "Błąd", f"Plik nie istnieje:\n{path}")
            return
        if not path.lower().endswith(".epub"):
            QMessageBox.warning(self, "Błąd", "Wybrany plik nie ma rozszerzenia .epub.")
            return

        # UWAGA (punkt 2 zgłoszenia): CELOWO nie sprawdzamy tu jeszcze
        # zgodności sumy kontrolnej z kandydatem - to samo w sobie nie
        # rozstrzyga niczego, bo i tak weryfikujemy ją niżej przez
        # PreviewState.load_initial(), które NIE blokuje już otwarcia
        # książki przy niezgodności (patrz `annotation_error` poniżej).
        # Kandydat "z ostatniej sesji" jest brany pod uwagę tylko wtedy,
        # gdy ścieżka EPUB-a dokładnie się zgadza - a i tak, jeśli treść
        # pod tą ścieżką w międzyczasie się zmieniła (inna książka pod tą
        # samą nazwą pliku), poniższa weryfikacja to wykryje i zaproponuje
        # wybór właściwego pliku zamiast cichego, błędnego dopasowania.
        resolved_annotations: str | None = None
        if annotation_path:
            candidate = os.path.abspath(annotation_path)
        elif os.path.normcase(str(self.settings.value("last_opened_file", ""))) == os.path.normcase(path):
            candidate = str(self.settings.value("last_annotation_file", "") or "")
        else:
            candidate = os.path.splitext(path)[0] + ".easyreader"
        if candidate:
            if not os.path.isfile(candidate):
                if annotation_path:
                    QMessageBox.warning(self, "Błąd", f"Plik notatek nie istnieje:\n{candidate}")
                    return
            elif not candidate.lower().endswith(".easyreader"):
                QMessageBox.warning(self, "Błąd", "Plik notatek musi mieć rozszerzenie .easyreader.")
                return
            else:
                resolved_annotations = candidate

        # Zachowaj pozycję poprzednio otwartej książki również wtedy, gdy
        # użytkownik przełącza się na inny EPUB bez zamykania programu.
        self._save_book_position()
        self.coordinator.stop_watching()

        old_state = self.state
        try:
            self.state = PreviewState()
            self.state.load_initial(path, annotation_path=resolved_annotations)
        except (EpubSecurityError, EpubParseError, FileNotFoundError, Exception) as exc:  # noqa: BLE001
            logger.exception("Nie udało się otworzyć pliku EPUB: %s", path)
            QMessageBox.critical(self, "Nie można otworzyć EPUB-a", f"Wystąpił błąd podczas otwierania pliku:\n\n{exc}")
            self.state = old_state
            return

        # Poprzednia książka (jeśli była) - sprzątamy jej katalog tymczasowy.
        old_state.cleanup()
        self._pending_anchor = None
        self._last_reading_anchor = None

        self.coordinator = ReloadCoordinator(self.state)
        self._connect_coordinator_signals()
        self.coordinator.set_auto_reload_enabled(self.auto_reload_checkbox.isChecked())
        resolved_annotations = self.state.annotation_path  # None, jeśli load_initial odrzuciło niezgodny plik
        extra_paths = [resolved_annotations] if resolved_annotations else []
        self.coordinator.start_watching(path, extra_paths=extra_paths)
        self.annotation_path = resolved_annotations
        self.action_export_notes.setEnabled(bool(resolved_annotations))

        saved_anchor = self._restore_book_position(path)
        self._populate_toc()
        self._display_current_chapter(restore_anchor=saved_anchor)
        self._update_status_bar(reload_time=None)

        self.settings.setValue("last_opened_file", path)
        self.settings.setValue("last_annotation_file", resolved_annotations or "")
        self._update_marker_action()
        logger.info("Otwarto plik EPUB: %s (tytuł: %s)", path, self.state.book.title if self.state.book else "?")

        if self.state.annotation_error:
            # Notatki istniały (kandydat), ale nie pasowały do tej książki -
            # książka jest już otwarta i w pełni użyteczna; proponujemy
            # naprawę zamiast tylko pokazywać komunikat o błędzie.
            self._prompt_annotation_mismatch(candidate, self.state.annotation_error)

    def _prompt_annotation_mismatch(self, candidate_notes: str, reason: str) -> None:
        """Punkt 2/3: niezgodny plik notatek nie blokuje już otwarcia książki -
        tutaj proponujemy dalsze kroki, zamiast zostawiać czytelnika z samym
        komunikatem o błędzie."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Notatki nie pasują do tej książki")
        box.setText(
            f"Plik notatek:\n{candidate_notes}\n\nnie pasuje do otwartej książki:\n{reason}\n\n"
            "Książka jest otwarta bez notatek. Co chcesz zrobić?"
        )
        button_continue = box.addButton("Otwórz bez notatek", QMessageBox.ButtonRole.RejectRole)
        button_choose = box.addButton("Wybierz plik notatek…", QMessageBox.ButtonRole.ActionRole)
        button_relink = box.addButton("Połącz ponownie (bezpieczny relink)…", QMessageBox.ButtonRole.ActionRole)
        box.setDefaultButton(button_continue)
        box.exec()

        clicked = box.clickedButton()
        if clicked is button_choose:
            self._on_choose_notes_file()
        elif clicked is button_relink:
            self._on_relink_notes(candidate_notes)

    def _on_choose_notes_file(self) -> None:
        """Pozwala ręcznie wskazać plik `.easyreader` dla aktualnie otwartej książki."""
        if not self.state.source_epub_path:
            QMessageBox.information(self, "Notatki", "Najpierw otwórz książkę.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Wybierz plik notatek", "", "Pliki notatek (*.easyreader)"
        )
        if not path:
            return
        if self.state.try_apply_annotations(path):
            self._on_notes_linked(path)
        else:
            QMessageBox.warning(
                self,
                "Notatki",
                f"Ten plik notatek również nie pasuje do otwartej książki:\n\n{self.state.annotation_error}\n\n"
                "Możesz spróbować bezpiecznego ponownego powiązania z menu „Plik”.",
            )

    def _on_relink_notes_from_menu(self) -> None:
        """Wywoływane z menu "Plik" (poza dialogiem niezgodności) - ustala,
        który plik notatek ma zostać poddany bezpiecznemu relinkowi."""
        if not self.state.source_epub_path:
            QMessageBox.information(self, "Relink", "Najpierw otwórz książkę.")
            return
        candidate = self.annotation_path or (
            os.path.splitext(self.state.source_epub_path)[0] + ".easyreader"
        )
        if not os.path.isfile(candidate):
            path, _ = QFileDialog.getOpenFileName(
                self, "Wybierz plik notatek do połączenia", "", "Pliki notatek (*.easyreader)"
            )
            if not path:
                return
            candidate = path
        self._on_relink_notes(candidate)

    def _on_relink_notes(self, candidate_notes: str) -> None:
        """Punkt 3/4/6: bezpieczne, transakcyjne ponowne powiązanie - pokazuje
        pełny raport zgodności i aktualizuje powiązanie DOPIERO po wyraźnym
        potwierdzeniu użytkownika. `commit_relink` samo tworzy kopię
        bezpieczeństwa i nie modyfikuje oryginalnego pliku, jeśli próbne
        nałożenie notatek na nową wersję książki się nie powiedzie."""
        if not self.state.book or not self.state.source_epub_path or not self.state.current_temp_dir:
            QMessageBox.information(self, "Relink", "Najpierw otwórz książkę.")
            return
        try:
            report = analyze_relink(
                candidate_notes,
                self.state.source_epub_path,
                self.state.current_temp_dir,
                new_book_title=self.state.book.title,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Nie udało się przeanalizować pliku notatek do relinku: %s", candidate_notes)
            QMessageBox.critical(self, "Relink", f"Nie można odczytać pliku notatek:\n\n{exc}")
            return

        # Punkt 6: liczba dopasowań pewnych/niedopasowanych, wykorzystane
        # poziomy dopasowania oraz jawne ostrzeżenie o zmianie powiązania.
        title_sim = report["title_similarity"]
        title_text = f"{title_sim:.0%}" if title_sim is not None else "nie można porównać"
        tiers_text = (
            ", ".join(f"{name}: {count}" for name, count in report["tiers_used"].items())
            if report["tiers_used"] else "brak"
        )
        summary = (
            f"Dopasowania pewne: {report['matched']} z {report['total']} adnotacji ({report['ratio']:.0%}).\n"
            f"Niedopasowane: {report['unmatched']}.\n"
            f"Wykorzystane poziomy dopasowania: {tiers_text}.\n"
            f"Podobieństwo tytułu: {title_text}.\n\n"
            f"{report['recommendation_reason']}\n\n"
            "UWAGA: ponowne powiązanie zmieni, do której książki przypisany jest ten plik notatek "
            "(zostanie zapisana kopia bezpieczeństwa przed zmianą)."
        )

        answer = QMessageBox.question(
            self,
            "Potwierdź ponowne powiązanie",
            summary + "\n\nZaktualizować powiązanie notatek z tą książką?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            result = commit_relink(
                candidate_notes,
                self.state.source_epub_path,
                self.state.current_temp_dir,
                new_title=self.state.book.title,
            )
        except EasyReaderAnnotationError as exc:
            # Punkt 4: nieudana próba nie modyfikuje oryginalnego pliku notatek.
            QMessageBox.warning(
                self,
                "Relink",
                f"Bezpieczny relink nie powiódł się:\n\n{exc}\n\n"
                "Oryginalny plik notatek NIE został zmieniony.",
            )
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("Nieoczekiwany błąd podczas relinku: %s", candidate_notes)
            QMessageBox.critical(self, "Relink", f"Nieoczekiwany błąd:\n\n{exc}")
            return

        # `commit_relink` już nałożyło notatki na rozpakowany podgląd jako
        # część próby transakcyjnej (patrz jego docstring) - NIE wywołujemy
        # tu ponownie `try_apply_annotations`, żeby nie wstawić adnotacji
        # po raz drugi do tego samego, już zmodyfikowanego pliku rozdziału.
        self.state.annotation_path = candidate_notes
        self.state.annotation_error = None
        self.state.annotation_skipped = result["apply_report"]["skipped"]
        self._on_notes_linked(candidate_notes)

        skipped_count = len(result["apply_report"]["skipped"])
        note = f" ({skipped_count} adnotacji pominięto - patrz dziennik)" if skipped_count else ""
        QMessageBox.information(
            self,
            "Relink",
            f"Notatki zostały ponownie połączone z książką.{note}\n\nKopia bezpieczeństwa: {result['backup_path']}",
        )

    def _on_notes_linked(self, notes_path: str) -> None:
        """Odświeża UI po tym, jak notatki zostały pomyślnie (ponownie) powiązane."""
        self.annotation_path = notes_path
        self.action_export_notes.setEnabled(True)
        self.settings.setValue("last_annotation_file", notes_path)
        self.coordinator.start_watching(self.state.source_epub_path, extra_paths=[notes_path])
        self._display_current_chapter()

    def _on_export_notes(self) -> None:
        if not self.annotation_path:
            QMessageBox.information(self, "Eksport notatek", "Ta książka nie ma pliku notatek .easyreader.")
            return
        default_path = os.path.splitext(self.annotation_path)[0] + "_notatki.epub"
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Eksportuj notatki do EPUB",
            default_path,
            "Pliki EPUB (*.epub)",
        )
        if not destination:
            return
        try:
            exported = export_notes_epub(self.annotation_path, destination)
        except (NotesExportError, Exception) as exc:  # noqa: BLE001
            logger.exception("Nie udało się wyeksportować notatek: %s", exc)
            QMessageBox.critical(self, "Błąd eksportu", str(exc))
            return
        QMessageBox.information(
            self,
            "Eksport zakończony",
            f"Utworzono samodzielny EPUB z notatkami:\n\n{exported}",
        )

    def _connect_coordinator_signals(self) -> None:
        self.coordinator.reload_succeeded.connect(self._on_reload_succeeded)
        self.coordinator.reload_failed.connect(self._on_reload_failed)
        self.coordinator.watcher_status_changed.connect(self._on_watcher_status_changed)
        self.coordinator.set_pre_reload_hook(self._capture_anchor_before_reload)

    # ------------------------------------------------------------------ #
    # Wyświetlanie rozdziałów
    # ------------------------------------------------------------------ #

    def _display_current_chapter(self, fragment: str = "", restore_anchor: dict | None = None) -> None:
        chapter_path = self.state.current_chapter_path()
        if not chapter_path or not os.path.isfile(chapter_path):
            QMessageBox.warning(self, "Błąd", "Nie można znaleźć pliku bieżącego rozdziału.")
            return

        if restore_anchor is not None:
            def _on_loaded(ok: bool) -> None:
                try:
                    self.web_view.loadFinished.disconnect(_on_loaded)
                except (RuntimeError, TypeError):
                    pass
                if ok:
                    self.web_view.restore_reading_anchor(restore_anchor)
                    # Po pierwszym renderowaniu WebEngine może jeszcze
                    # przeliczać wysokość strony (czcionki, obrazy i nowo
                    # dołączone komentarze). Ponawiamy przywrócenie, aby
                    # późniejsze przesunięcia układu nie zrzucały czytelnika
                    # na początek rozdziału.
                    QTimer.singleShot(
                        150,
                        lambda anchor=restore_anchor: self.web_view.restore_reading_anchor(anchor),
                    )
                    QTimer.singleShot(
                        500,
                        lambda anchor=restore_anchor: self.web_view.restore_reading_anchor(anchor),
                    )

            # WAŻNE: podłączamy handler PRZED wywołaniem load_chapter()
            # (a nie po nim), aby nigdy nie przegapić sygnału loadFinished -
            # w przeciwnym razie, gdyby nawigacja zakończyła się bardzo
            # szybko, mogłoby dojść do jego emisji przed podłączeniem slotu.
            self.web_view.loadFinished.connect(_on_loaded)

        self.web_view.load_chapter(chapter_path, fragment=fragment)
        self.web_view.set_zoom_factor(self.state.zoom_factor)

        self._save_book_position()
        self._update_status_bar()
        self._highlight_current_toc_item()

    def _on_prev_chapter(self) -> None:
        if self.state.go_previous():
            self._display_current_chapter()

    def _on_next_chapter(self) -> None:
        if self.state.go_next():
            self._display_current_chapter()

    def _on_internal_link_activated(self, href: str) -> None:
        base_href, _, fragment = href.partition("#")
        if self.state.go_to_href(base_href):
            self._display_current_chapter(fragment=fragment)

    def _on_external_link_requested(self, url: str) -> None:
        answer = QMessageBox.question(
            self,
            "Odnośnik zewnętrzny",
            f"Ten odnośnik prowadzi poza książkę:\n\n{url}\n\nOtworzyć go w domyślnej przeglądarce?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl(url))

    # ------------------------------------------------------------------ #
    # Spis treści
    # ------------------------------------------------------------------ #

    def _populate_toc(self) -> None:
        self.toc_tree.clear()
        if not self.state.book:
            return

        def add_entries(entries: list[TocEntry], parent) -> None:
            for entry in entries:
                item = QTreeWidgetItem([entry.title])
                item.setData(0, Qt.ItemDataRole.UserRole, entry.href)
                if parent is None:
                    self.toc_tree.addTopLevelItem(item)
                else:
                    parent.addChild(item)
                add_entries(entry.children, item)

        add_entries(self.state.book.toc, None)
        self.toc_tree.expandAll()

    def _on_toc_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        href = item.data(0, Qt.ItemDataRole.UserRole)
        if not href:
            return
        base_href, _, fragment = href.partition("#")
        if self.state.go_to_href(base_href):
            self._display_current_chapter(fragment=fragment)

    def _highlight_current_toc_item(self) -> None:
        current_href = self.state.current_chapter_href()
        if not current_href:
            return

        def walk(item: QTreeWidgetItem):
            for i in range(item.childCount()):
                walk(item.child(i))
            href = item.data(0, Qt.ItemDataRole.UserRole)
            if href and href.split("#")[0] == current_href:
                self.toc_tree.setCurrentItem(item)

        for i in range(self.toc_tree.topLevelItemCount()):
            walk(self.toc_tree.topLevelItem(i))

    # ------------------------------------------------------------------ #
    # Powiększenie
    # ------------------------------------------------------------------ #

    def _change_zoom(self, delta: float) -> None:
        self.state.zoom_factor = max(0.25, min(self.state.zoom_factor + delta, 5.0))
        self.web_view.set_zoom_factor(self.state.zoom_factor)

    # ------------------------------------------------------------------ #
    # Przeładowanie - obsługa sygnałów z ReloadCoordinator
    # ------------------------------------------------------------------ #

    def _capture_anchor_before_reload(self, proceed) -> None:
        """
        Wywoływane przez ReloadCoordinator (jako `pre_reload_hook`) TUŻ
        PRZED faktycznym startem przeładowania w tle - a więc zanim
        cokolwiek w treści może się zmienić. Musimy poczekać na wynik
        asynchronicznego `runJavaScript()` (funkcja `capture_reading_anchor`
        korzysta z callbacku), zanim przeładowanie faktycznie ruszy - stąd
        `proceed()` jest wywoływane dopiero z tego callbacku, a nie od razu.

        Punkt 6 (starsze generacje nie mogą nadpisać nowszej pozycji):
        `ReloadCoordinator` już chroni przed uruchomieniem DWÓCH wątków
        przeładowania naraz (licznik generacji), ale samo to wywołanie
        haka może zostać "przeterminowane" wcześniej, niż jego własny,
        wciąż oczekujący callback JS zdąży się wykonać - np. gdy w
        międzyczasie użytkownik kliknie "Przeładuj teraz" i uruchomi
        NOWSZE wywołanie haka. Bez poniższego znacznika `call_id`, spóźniony
        callback STAREGO wywołania nadpisałby `_pending_anchor` już
        NIEAKTUALNĄ kotwicą, mimo że nowsze wywołanie zdążyło już zapisać
        świeższą. Dlatego każde wywołanie haka dostaje własny, rosnący
        identyfikator, i tylko NAJŚWIEŻSZE z nich wolno zapisywać stan.
        """
        self._anchor_hook_call_id = getattr(self, "_anchor_hook_call_id", 0) + 1
        call_id = self._anchor_hook_call_id

        self.status_reload_time.setText("Przeładowywanie…")
        # Zachowaj ostatnią poprawną kotwicę jako zabezpieczenie. WebEngine
        # może sporadycznie nie zwrócić wyniku JavaScript przed limitem
        # czasu; wyzerowanie jej zrzucałoby wtedy widok na początek.
        self._pending_anchor = self._last_reading_anchor

        state = {"proceeded": False}

        def _proceed_once() -> None:
            if not state["proceeded"]:
                state["proceeded"] = True
                proceed()

        def _on_anchor_captured(anchor) -> None:
            # Jeśli w międzyczasie wystartowało NOWSZE wywołanie haka (inny
            # call_id), ten callback jest przeterminowany - nie wolno mu
            # już nadpisywać `_pending_anchor`/`_last_reading_anchor`, bo
            # nowsze wywołanie mogło już zapisać świeższą kotwicę.
            if isinstance(anchor, dict) and call_id == self._anchor_hook_call_id:
                self._pending_anchor = anchor
                self._last_reading_anchor = anchor
                self._store_reading_marker(anchor, automatic=True)
            _proceed_once()

        # Zabezpieczenie: gdyby z jakiegoś powodu runJavaScript() nigdy nie
        # wywołało swojego callbacku, przeładowanie nie może zawiesić się
        # w nieskończoność - po chwili ruszamy dalej bez kotwicy (zadziała
        # wtedy sam procent przewinięcia zapamiętany przy poprzednim odczycie,
        # albo po prostu rozdział otworzy się od góry).
        QTimer.singleShot(1500, _proceed_once)

        self.web_view.capture_reading_anchor(_on_anchor_captured)

    def _on_reload_succeeded(self, position: ChapterPosition) -> None:
        anchor = self._pending_anchor
        self._populate_toc()
        self._display_current_chapter(restore_anchor=anchor)
        self._update_status_bar(reload_time=datetime.now())
        logger.info("Podgląd zaktualizowany po zmianie pliku źródłowego.")

    def _on_reload_failed(self, message: str) -> None:
        self.status_reload_time.setText(f"Błąd przeładowania: {message}")
        logger.warning("Przeładowanie nie powiodło się: %s", message)

    def _on_watcher_status_changed(self, active: bool) -> None:
        self.status_watcher.setText("Obserwacja: aktywna" if active else "Obserwacja: nieaktywna")

    def _on_auto_reload_toggled(self, checked: bool) -> None:
        self.coordinator.set_auto_reload_enabled(checked)

    # ------------------------------------------------------------------ #
    # Pasek stanu
    # ------------------------------------------------------------------ #

    def _update_status_bar(self, reload_time: datetime | None = "unset") -> None:
        if self.state.source_epub_path:
            self.status_filename.setText(os.path.basename(self.state.source_epub_path))
        skipped = len(self.state.annotation_skipped) if self.state.annotation_skipped else 0
        if self.annotation_path and skipped:
            annotations_text = f"Notatki: {os.path.basename(self.annotation_path)} ({skipped} pominiętych)"
        elif self.annotation_path:
            annotations_text = f"Notatki: {os.path.basename(self.annotation_path)}"
        else:
            annotations_text = "Notatki: brak"
        self.status_annotations.setText(annotations_text)
        if self.state.book and self.state.book.spine:
            idx = self.state.current_spine_index + 1
            total = len(self.state.book.spine)
            self.status_chapter.setText(f"Rozdział {idx} / {total}")
        if reload_time != "unset":
            if reload_time is None:
                self.status_reload_time.setText("Nie przeładowywano")
            else:
                self.status_reload_time.setText(f"Ostatnie przeładowanie: {reload_time.strftime('%H:%M:%S')}")

    # ------------------------------------------------------------------ #
    # Motyw
    # ------------------------------------------------------------------ #

    def _toggle_theme(self) -> None:
        self._is_dark_theme = not self._is_dark_theme
        self._apply_theme()
        self.settings.setValue("dark_theme", self._is_dark_theme)

    def _apply_theme(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.instance().setStyleSheet(DARK_STYLESHEET if self._is_dark_theme else LIGHT_STYLESHEET)

    # ------------------------------------------------------------------ #
    # Przeciągnij i upuść
    # ------------------------------------------------------------------ #

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile() and url.toLocalFile().lower().endswith(".epub"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        for url in event.mimeData().urls():
            if url.isLocalFile() and url.toLocalFile().lower().endswith(".epub"):
                self.open_epub(url.toLocalFile())
                break

    # ------------------------------------------------------------------ #
    # Ustawienia aplikacji (QSettings)
    # ------------------------------------------------------------------ #

    def _restore_settings(self) -> None:
        geometry = self.settings.value("window_geometry")
        if geometry:
            self.restoreGeometry(geometry)

        self._is_dark_theme = self.settings.value("dark_theme", False, type=bool)
        self._apply_theme()

        auto_reload = self.settings.value("auto_reload_enabled", True, type=bool)
        self.auto_reload_checkbox.setChecked(auto_reload)

    def _save_settings(self) -> None:
        self.settings.setValue("window_geometry", self.saveGeometry())
        self.settings.setValue("auto_reload_enabled", self.auto_reload_checkbox.isChecked())

    @staticmethod
    def _book_settings_key(path: str) -> str:
        """Stabilny, bezpieczny klucz ustawień przypisany do ścieżki EPUB-a."""
        normalized = os.path.normcase(os.path.realpath(os.path.abspath(path)))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def _save_book_position(self) -> None:
        """Zapamiętuje bieżący rozdział osobno dla każdej książki."""
        path = self.state.source_epub_path
        href = self.state.current_chapter_href()
        if not path or not href:
            return
        key = self._book_settings_key(path)
        self.settings.setValue(f"books/{key}/last_chapter_href", href)
        self.settings.sync()

    def _save_precise_reading_position(self, on_done=None) -> None:
        """Okresowo zapisuje widoczny akapit jako trwałą zakładkę książki.

        `on_done` (opcjonalnie) jest wywoływane PO zakończeniu zapisu (albo
        od razu, jeśli nie ma czego zapisywać) - używane przy zamykaniu
        programu, żeby poczekać na wynik asynchronicznego `runJavaScript()`
        zamiast zamykać okno (i niszczyć WebEngine) zanim zapis się wykona.
        """
        path = self.state.source_epub_path
        href = self.state.current_chapter_href()
        if not path or not href:
            if on_done:
                on_done()
            return

        key = self._book_settings_key(path)

        def _store(anchor) -> None:
            if isinstance(anchor, dict):
                payload = {"chapter_href": href, "anchor": anchor}
                self.settings.setValue(
                    f"books/{key}/reading_anchor",
                    json.dumps(payload, ensure_ascii=False),
                )
                self.settings.setValue(f"books/{key}/last_chapter_href", href)
                self.settings.sync()
            if on_done:
                on_done()

        self.web_view.capture_reading_anchor(_store)

    def _marker_settings_key(self) -> str | None:
        path = self.state.source_epub_path
        if not path:
            return None
        return f"books/{self._book_settings_key(path)}/last_marker"

    def _load_reading_marker(self) -> dict | None:
        key = self._marker_settings_key()
        if not key:
            return None
        raw = self.settings.value(key, None)
        if not raw:
            return None
        try:
            marker = json.loads(str(raw))
        except (TypeError, ValueError):
            logger.warning("Nie udało się odczytać znacznika książki.")
            return None
        if not isinstance(marker, dict):
            return None
        if not isinstance(marker.get("chapter_href"), str):
            return None
        if not isinstance(marker.get("anchor"), dict):
            return None
        return marker

    def _store_reading_marker(self, anchor: dict, automatic: bool = False) -> bool:
        key = self._marker_settings_key()
        href = self.state.current_chapter_href()
        if not key or not href or not isinstance(anchor, dict) or not anchor.get("found"):
            return False
        marker = {
            "chapter_href": href,
            "anchor": anchor,
            "automatic": automatic,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.settings.setValue(key, json.dumps(marker, ensure_ascii=False))
        self.settings.sync()
        self._update_marker_action()
        return True

    def _update_marker_action(self) -> None:
        self.action_go_to_marker.setEnabled(self._load_reading_marker() is not None)

    def _set_reading_marker(self) -> None:
        if not self.state.source_epub_path:
            QMessageBox.information(self, "Znacznik", "Najpierw otwórz książkę.")
            return

        def _save(anchor) -> None:
            if self._store_reading_marker(anchor, automatic=False):
                self.status_reload_time.setText("Znacznik zapisany")
            else:
                QMessageBox.warning(
                    self,
                    "Znacznik",
                    "Nie udało się rozpoznać widocznego fragmentu tekstu.",
                )

        self.web_view.capture_reading_anchor(_save)

    def _go_to_reading_marker(self) -> None:
        marker = self._load_reading_marker()
        if not marker:
            QMessageBox.information(self, "Znacznik", "Ta książka nie ma jeszcze zapisanego znacznika.")
            self._update_marker_action()
            return
        href = marker["chapter_href"]
        anchor = marker["anchor"]
        if not self.state.go_to_href(href):
            QMessageBox.warning(self, "Znacznik", "Rozdział zapisany w znaczniku nie jest już dostępny.")
            return
        self._display_current_chapter(restore_anchor=anchor)
        self.status_reload_time.setText("Przywrócono znacznik")

    def _restore_book_position(self, path: str) -> dict | None:
        """Przywraca ostatni rozdział i zwraca zapisaną kotwicę przewinięcia."""
        key = self._book_settings_key(path)
        href = self.settings.value(f"books/{key}/last_chapter_href", None)
        if href:
            self.state.go_to_href(str(href))
        raw = self.settings.value(f"books/{key}/reading_anchor", None)
        if not raw:
            return None
        try:
            payload = json.loads(str(raw))
        except (TypeError, ValueError):
            logger.warning("Nie udało się odczytać trwałej zakładki książki.")
            return None
        if not isinstance(payload, dict) or payload.get("chapter_href") != self.state.current_chapter_href():
            return None
        anchor = payload.get("anchor")
        return anchor if isinstance(anchor, dict) else None

    def last_opened_file(self) -> str | None:
        return self.settings.value("last_opened_file", None)

    # ------------------------------------------------------------------ #
    # Zamykanie aplikacji
    # ------------------------------------------------------------------ #

    def closeEvent(self, event) -> None:
        if getattr(self, "_close_confirmed", False):
            logger.info("Zamykanie aplikacji - zatrzymywanie obserwatora i sprzątanie katalogów tymczasowych.")
            self.coordinator.stop_watching()
            self.state.cleanup()
            super().closeEvent(event)
            return

        # Punkt 5: zapis okresowy (co 2s) mógł nie zdążyć złapać ostatnich
        # kilku sekund czytania. `capture_reading_anchor()` jest asynchroniczne
        # (runJavaScript z callbackiem) - gdybyśmy pozwolili oknu zamknąć się
        # od razu, WebEngine zostałby zniszczony zanim callback zdąży
        # wystartować, i zapis by przepadł. Zamiast tego WSTRZYMUJEMY
        # zamknięcie (event.ignore()), czekamy na wynik zapisu (z krótkim
        # limitem czasu na wypadek, gdyby JS nigdy nie odpowiedział), a
        # dopiero potem zamykamy okno naprawdę.
        event.ignore()
        self._reading_position_timer.stop()
        self._save_book_position()

        state = {"done": False}

        def _finish_close() -> None:
            if state["done"]:
                return
            state["done"] = True
            self._close_confirmed = True
            self._save_settings()
            self.close()

        QTimer.singleShot(800, _finish_close)
        self._save_precise_reading_position(on_done=_finish_close)

    def _show_about(self) -> None:
        description = (
            "Brzytwa Ockhama — Czytnik Prometejski\n\n"
            "Bezpieczny czytnik EPUB wspierający rozumienie trudnego tekstu. "
            "Oryginalna książka jest otwierana tylko do odczytu, a objaśnienia "
            "pochodzą z osobnego pliku .easyreader.\n"
            "Podgląd powstaje w katalogu tymczasowym i jest usuwany po zamknięciu programu."
        )
        QMessageBox.information(
            self,
            "O programie",
            description,
        )
