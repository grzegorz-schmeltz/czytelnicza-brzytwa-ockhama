"""
core.file_watcher
==================

Obserwacja pliku EPUB pod kątem zmian oraz sprawdzanie jego stabilności
przed ponownym wczytaniem.

Watchdog obserwuje KATALOG nadrzędny pliku (a nie sam plik), ponieważ wiele
edytorów/skryptów zapisuje nową wersję poprzez utworzenie pliku tymczasowego
i "atomowe" zastąpienie oryginału (rename/replace) - takie zdarzenie bywa
widoczne jako "created" lub "moved", a nie "modified" na oryginalnej ścieżce.

Funkcja `wait_until_stable` sprawdza kilkukrotnie w krótkich odstępach, czy:
  * plik nadal istnieje,
  * jego rozmiar przestał się zmieniać,
  * archiwum ZIP da się poprawnie otworzyć (test_zip / próbne otwarcie).

Funkcje `wait_until_stable` i `check_zip_openable` są wolne od Qt (łatwe do
testowania jednostkowego w izolacji). Klasa `EpubFileWatcher` korzysta z
sygnału Qt wyłącznie po to, by bezpiecznie i wątkowo-poprawnie powiadomić
GUI o zmianie pliku - patrz jej docstring.
"""

from __future__ import annotations

import logging
import os
import time
import zipfile
from dataclasses import dataclass
from typing import Callable, Optional

from PySide6.QtCore import QObject, Signal
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logger = logging.getLogger("epub_viewer.watcher")


@dataclass
class StabilityResult:
    ok: bool
    reason: str = ""


def check_zip_openable(path: str) -> StabilityResult:
    """Próbuje otworzyć archiwum ZIP i zweryfikować jego integralność."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            bad_file = zf.testzip()
            if bad_file is not None:
                return StabilityResult(False, f"Uszkodzony plik w archiwum: {bad_file}")
        return StabilityResult(True)
    except (zipfile.BadZipFile, OSError) as exc:
        return StabilityResult(False, f"Nie można otworzyć archiwum ZIP: {exc}")


def wait_until_stable(
    path: str,
    attempts: int = 8,
    poll_interval_s: float = 0.25,
    required_stable_polls: int = 2,
    should_abort: Optional[Callable[[], bool]] = None,
) -> StabilityResult:
    """
    Czeka, aż plik przestanie się zmieniać (rozmiar stabilny przez
    `required_stable_polls` kolejnych sprawdzeń) i da się poprawnie otworzyć
    jako ZIP. Ponawia próby maksymalnie `attempts` razy.

    `should_abort` - opcjonalny callback pozwalający przerwać oczekiwanie
    (np. gdy użytkownik otworzył w międzyczasie inny plik).
    """
    last_size = -1
    stable_count = 0

    for attempt in range(1, attempts + 1):
        if should_abort and should_abort():
            return StabilityResult(False, "Oczekiwanie przerwane.")

        if not os.path.isfile(path):
            logger.debug("Plik nie istnieje (próba %d/%d): %s", attempt, attempts, path)
            time.sleep(poll_interval_s)
            continue

        try:
            size = os.path.getsize(path)
        except OSError as exc:
            logger.debug("Błąd odczytu rozmiaru pliku: %s", exc)
            time.sleep(poll_interval_s)
            continue

        if size == last_size and size > 0:
            stable_count += 1
        else:
            stable_count = 0
        last_size = size

        if stable_count >= required_stable_polls:
            result = check_zip_openable(path)
            if result.ok:
                return result
            logger.debug("Plik ma stabilny rozmiar, ale ZIP nieprawidłowy (próba %d/%d): %s",
                         attempt, attempts, result.reason)
            stable_count = 0  # jeszcze trwa zapis - resetujemy i próbujemy dalej

        time.sleep(poll_interval_s)

    return StabilityResult(False, "Plik nie osiągnął stabilnego, poprawnego stanu w wyznaczonym czasie.")


class _TargetFileEventHandler(FileSystemEventHandler):
    """Nasłuchuje zdarzeń w katalogu nadrzędnym, filtrując po nazwie pliku docelowego."""

    def __init__(self, target_filename: str, on_change: Callable[[], None]):
        super().__init__()
        self._target_filename = target_filename
        self._on_change = on_change

    def _maybe_trigger(self, event) -> None:
        try:
            src_name = os.path.basename(getattr(event, "src_path", "") or "")
            dest_name = os.path.basename(getattr(event, "dest_path", "") or "")
        except Exception:
            return
        if src_name == self._target_filename or dest_name == self._target_filename:
            self._on_change()

    def on_modified(self, event):
        self._maybe_trigger(event)

    def on_created(self, event):
        self._maybe_trigger(event)

    def on_moved(self, event):
        self._maybe_trigger(event)


class EpubFileWatcher(QObject):
    """
    Obserwuje katalog zawierający plik EPUB i emituje sygnał `changed`
    przy każdym zdarzeniu dotyczącym tego pliku.

    WAŻNE - wątkowość: watchdog dostarcza zdarzenia z WŁASNEGO wątku
    (nie wątku GUI), który nie ma uruchomionej pętli zdarzeń Qt. Dlatego
    powiadamianie NIE MOŻE polegać na `QTimer.singleShot()` wywoływanym z
    tamtego wątku (taki timer nigdy by się nie uruchomił - nie ma go kto
    "obsłużyć"). Zamiast tego korzystamy z mechanizmu sygnałów/slotów Qt:
    ten obiekt jest tworzony w wątku GUI (ma tam swoje "powinowactwo
    wątkowe" - thread affinity), więc emisja sygnału z wątku watchdog jest
    przez Qt automatycznie i bezpiecznie kolejkowana (queued connection) do
    wykonania w wątku GUI. Sama logika debounce (QTimer) pozostaje po
    stronie odbiorcy sygnału w wątku GUI - patrz core.reload_worker.
    """

    changed = Signal()

    def __init__(self, file_path: str, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._file_path = file_path
        self._dir_path = os.path.dirname(os.path.abspath(file_path)) or "."
        self._filename = os.path.basename(file_path)
        self._observer: Optional[Observer] = None

    def start(self) -> None:
        if self._observer is not None:
            return
        # Emitujemy sygnał wprost z wątku watchdog - Qt zadba o bezpieczne
        # dostarczenie go do slotów podłączonych w wątku GUI (patrz docstring wyżej).
        handler = _TargetFileEventHandler(self._filename, self.changed.emit)
        self._observer = Observer()
        self._observer.schedule(handler, self._dir_path, recursive=False)
        self._observer.start()
        logger.info("Rozpoczęto obserwację katalogu: %s (plik: %s)", self._dir_path, self._filename)

    def stop(self) -> None:
        if self._observer is None:
            return
        try:
            self._observer.stop()
            self._observer.join(timeout=2.0)
        except Exception as exc:
            logger.warning("Błąd podczas zatrzymywania obserwatora: %s", exc)
        finally:
            self._observer = None
            logger.info("Zatrzymano obserwację pliku.")

    @property
    def is_running(self) -> bool:
        return self._observer is not None and self._observer.is_alive()
