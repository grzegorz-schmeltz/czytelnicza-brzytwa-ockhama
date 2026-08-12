"""
core.reload_worker
===================

Warstwa łącząca obserwację pliku (watchdog) ze stanem podglądu (PreviewState),
działająca w tle, aby operacje wejścia/wyjścia i rozpakowywania nigdy nie
blokowały interfejsu użytkownika.

Przepływ zdarzeń:

  1. `EpubFileWatcher` (wątek watchdog) wykrywa zmianę pliku i emituje sygnał.
  2. `ReloadCoordinator` (wątek GUI) odbiera zdarzenie i uruchamia debounce
     poprzez QTimer (700-1200 ms) - kolejne zdarzenia w tym czasie resetują
     odliczanie.
  3. Po upływie ciszy, jeśli zarejestrowano hak `pre_reload_hook` (patrz
     `set_pre_reload_hook`), jest on wywoływany JAKO PIERWSZY i musi sam
     zawiadomić coordinatora (wywołując przekazaną funkcję `proceed`), że
     można kontynuować - dzięki temu UI może np. bezpiecznie, w pełni
     asynchronicznie (przez `runJavaScript()`) zrzucić stan przewijania z
     WebEngine PRZED podmianą treści, a przeładowanie w tle na pewno nie
     wystartuje, zanim ten odczyt się nie zakończy.
  4. Dopiero wtedy uruchamiany jest `ReloadWorker` w osobnym QThread,
     który: czeka na stabilność pliku (z ponawianiem prób), bezpiecznie
     rozpakowuje nową wersję i parsuje jej strukturę.
  5. Wynik (sukces/błąd) wraca do wątku GUI przez sygnały Qt.
"""

from __future__ import annotations

import logging
from typing import Callable, Iterable, Optional

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from core.epub_parser import EpubParseError
from core.file_watcher import EpubFileWatcher, wait_until_stable
from core.preview_state import ChapterPosition, PreviewState
from core.safe_extract import EpubSecurityError

logger = logging.getLogger("epub_viewer.reload")

DEFAULT_DEBOUNCE_MS = 900
STABILITY_ATTEMPTS = 10
STABILITY_POLL_INTERVAL_S = 0.3


class ReloadWorker(QObject):
    """Wykonuje sprawdzenie stabilności oraz przeładowanie stanu w osobnym wątku."""

    finished = Signal(object)   # ChapterPosition przy sukcesie
    failed = Signal(str)        # komunikat błędu

    def __init__(self, state: PreviewState, epub_path: str):
        super().__init__()
        self._state = state
        self._epub_path = epub_path
        self._aborted = False

    def abort(self) -> None:
        self._aborted = True

    def run(self) -> None:
        try:
            stability = wait_until_stable(
                self._epub_path,
                attempts=STABILITY_ATTEMPTS,
                poll_interval_s=STABILITY_POLL_INTERVAL_S,
                should_abort=lambda: self._aborted,
            )
            if self._aborted:
                return
            if not stability.ok:
                self.failed.emit(f"Plik nie jest gotowy do odczytu: {stability.reason}")
                return

            position = self._state.reload(self._epub_path)
            if self._aborted:
                return
            self.finished.emit(position)

        except FileNotFoundError:
            self.failed.emit("Plik EPUB nie istnieje.")
        except EpubSecurityError as exc:
            self.failed.emit(f"Odrzucono archiwum ze względów bezpieczeństwa: {exc}")
        except EpubParseError as exc:
            self.failed.emit(f"Niepoprawna struktura EPUB: {exc}")
        except Exception as exc:  # noqa: BLE001 - chcemy pokazać każdy błąd w UI
            logger.exception("Nieoczekiwany błąd podczas przeładowania.")
            self.failed.emit(f"Nieoczekiwany błąd: {exc}")


class ReloadCoordinator(QObject):
    """
    Spina EpubFileWatcher (wykrywanie zmian) z debounce (QTimer) i
    ReloadWorker (praca w tle). Udostępnia sygnały informujące UI o
    kolejnych etapach.
    """

    reload_started = Signal()
    reload_succeeded = Signal(object)   # ChapterPosition
    reload_failed = Signal(str)
    watcher_status_changed = Signal(bool)  # True = aktywny

    def __init__(self, state: PreviewState, debounce_ms: int = DEFAULT_DEBOUNCE_MS, parent=None):
        super().__init__(parent)
        self._state = state
        self._debounce_ms = debounce_ms
        self._watchers: list[EpubFileWatcher] = []
        self._auto_reload_enabled = True

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._start_reload)

        self._thread: QThread | None = None
        self._worker: ReloadWorker | None = None

        self._pre_reload_hook: Optional[Callable[[Callable[[], None]], None]] = None
        self._reload_generation = 0

    # ------------------------------------------------------------------ #

    @property
    def auto_reload_enabled(self) -> bool:
        return self._auto_reload_enabled

    def set_auto_reload_enabled(self, enabled: bool) -> None:
        self._auto_reload_enabled = enabled
        if not enabled:
            self._debounce_timer.stop()

    def set_pre_reload_hook(self, hook: Optional[Callable[[Callable[[], None]], None]]) -> None:
        """
        Rejestruje opcjonalny hak wywoływany TUŻ PRZED faktycznym startem
        przeładowania w tle (po debounce, przed uruchomieniem wątku
        roboczego). `hook` otrzymuje jeden argument - funkcję `proceed`,
        którą MUSI wywołać (synchronicznie albo później, np. z callbacku
        `runJavaScript()`), aby przeładowanie mogło faktycznie ruszyć.
        Przekazanie `None` wyłącza hak (przeładowanie startuje natychmiast,
        jak dotychczas) - przydatne np. w testach niekorzystających z UI.
        """
        self._pre_reload_hook = hook

    def start_watching(self, epub_path: str, extra_paths: Iterable[str] = ()) -> None:
        self.stop_watching()
        # EpubFileWatcher jest tworzony w wątku GUI (tym samym, co self),
        # dzięki czemu jego sygnał `changed`, emitowany z wątku watchdog,
        # zostanie przez Qt bezpiecznie zakolejkowany do wykonania tutaj.
        paths = [epub_path, *(path for path in extra_paths if path)]
        for path in dict.fromkeys(paths):
            watcher = EpubFileWatcher(path, parent=self)
            watcher.changed.connect(self._on_fs_event)
            watcher.start()
            self._watchers.append(watcher)
        self.watcher_status_changed.emit(True)

    def stop_watching(self) -> None:
        if self._watchers:
            for watcher in self._watchers:
                try:
                    watcher.changed.disconnect(self._on_fs_event)
                except (RuntimeError, TypeError):
                    pass
                watcher.stop()
                watcher.deleteLater()
            self._watchers.clear()
            self.watcher_status_changed.emit(False)
        self._debounce_timer.stop()
        self._abort_running_worker()

    def is_watching(self) -> bool:
        return bool(self._watchers) and all(watcher.is_running for watcher in self._watchers)

    def trigger_manual_reload(self) -> None:
        """Wywoływane przyciskiem "Przeładuj teraz" - pomija debounce."""
        self._debounce_timer.stop()
        self._start_reload()

    # ------------------------------------------------------------------ #
    # Wewnętrzne
    # ------------------------------------------------------------------ #

    def _on_fs_event(self) -> None:
        # Ten slot jest wywoływany bezpiecznie w wątku GUI: EpubFileWatcher
        # ma tu (w wątku GUI) swoje powinowactwo wątkowe, więc emisja jego
        # sygnału `changed` z wątku watchdog jest przez Qt automatycznie
        # zakolejkowana i dostarczona tutaj, do pętli zdarzeń GUI - dopiero
        # stąd startujemy debounce (QTimer), który już musi żyć w wątku GUI.
        self._schedule_debounced_reload()

    def _schedule_debounced_reload(self) -> None:
        if not self._auto_reload_enabled:
            return
        logger.info("Wykryto zmianę pliku - planuję przeładowanie za %d ms.", self._debounce_ms)
        self._debounce_timer.start(self._debounce_ms)

    def _start_reload(self) -> None:
        if self._state.source_epub_path is None:
            return
        self._abort_running_worker()

        self._reload_generation += 1
        generation = self._reload_generation

        self.reload_started.emit()

        if self._pre_reload_hook is not None:
            # Hak (zwykle UI) MUSI sam wywołać `proceed`, zanim faktycznie
            # wystartujemy - patrz docstring `set_pre_reload_hook`.
            self._pre_reload_hook(lambda: self._begin_reload_thread(generation))
        else:
            self._begin_reload_thread(generation)

    def _begin_reload_thread(self, generation: int) -> None:
        # Jeśli w międzyczasie nadeszło kolejne żądanie przeładowania (np.
        # użytkownik kliknął "Przeładuj teraz" zanim hak z poprzedniego
        # żądania zdążył wywołać `proceed`), to zgłoszenie jest już
        # nieaktualne - pomijamy je, aby nie uruchomić dwóch wątków naraz.
        if generation != self._reload_generation:
            return

        self._thread = QThread(self)
        self._worker = ReloadWorker(self._state, self._state.source_epub_path)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker.failed.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.finished.connect(self._clear_thread_refs)

        self._thread.start()

    def _clear_thread_refs(self) -> None:
        # Wywoływane po zakończeniu wątku (sukces, błąd lub abort) - zapobiega
        # dostępowi do już usuniętych obiektów C++ (QThread/QObject) przy
        # kolejnym wywołaniu _abort_running_worker()/stop_watching().
        self._thread = None
        self._worker = None

    def _abort_running_worker(self) -> None:
        # Unieważnia też ewentualny hak przeładowania, który jest właśnie
        # "w locie" (np. czeka na wynik runJavaScript()) - gdy w końcu
        # wywoła swój `proceed`, zostanie to rozpoznane jako nieaktualne.
        self._reload_generation += 1
        if self._worker is not None:
            self._worker.abort()
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(3000)
        self._thread = None
        self._worker = None

    def _on_worker_finished(self, position: ChapterPosition) -> None:
        logger.info("Przeładowanie zakończone sukcesem.")
        self.reload_succeeded.emit(position)

    def _on_worker_failed(self, message: str) -> None:
        logger.warning("Przeładowanie nieudane: %s", message)
        self.reload_failed.emit(message)
