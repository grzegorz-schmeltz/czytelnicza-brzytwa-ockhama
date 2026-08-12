from __future__ import annotations

import threading
import time
import zipfile
from pathlib import Path

from core.file_watcher import check_zip_openable, wait_until_stable

from .epub_builder import build_epub3


def test_check_zip_openable_valid(tmp_path: Path):
    epub_path = build_epub3(tmp_path / "ok.epub")
    result = check_zip_openable(str(epub_path))
    assert result.ok


def test_check_zip_openable_invalid(tmp_path: Path):
    bad_path = tmp_path / "bad.epub"
    bad_path.write_bytes(b"nie zip")
    result = check_zip_openable(str(bad_path))
    assert not result.ok


def test_wait_until_stable_returns_true_for_already_stable_file(tmp_path: Path):
    epub_path = build_epub3(tmp_path / "stable.epub")
    result = wait_until_stable(str(epub_path), attempts=5, poll_interval_s=0.05, required_stable_polls=2)
    assert result.ok


def test_wait_until_stable_returns_false_for_missing_file(tmp_path: Path):
    result = wait_until_stable(
        str(tmp_path / "missing.epub"), attempts=3, poll_interval_s=0.05, required_stable_polls=2
    )
    assert not result.ok


def test_wait_until_stable_waits_out_a_slow_write(tmp_path: Path):
    """
    Symuluje program zapisujący plik przez kilka chwil (rosnący rozmiar),
    a następnie kończący zapis poprawnym archiwum ZIP. `wait_until_stable`
    powinno poczekać, aż zapis się zakończy, zamiast zgłosić błąd od razu.
    """
    target = tmp_path / "slow_write.epub"
    # Zaczynamy od niekompletnego/pustego pliku.
    target.write_bytes(b"")

    def slow_writer():
        # Kilka "połówkowych" zapisów, symulujących trwający proces zapisu.
        for chunk in (b"PK", b"PK\x03\x04partial", b"PK\x03\x04partial-more-bytes"):
            target.write_bytes(chunk)
            time.sleep(0.1)
        # Ostateczny, poprawny plik EPUB.
        build_epub3(target)

    t = threading.Thread(target=slow_writer)
    t.start()
    result = wait_until_stable(str(target), attempts=40, poll_interval_s=0.05, required_stable_polls=2)
    t.join()

    assert result.ok


def test_wait_until_stable_gives_up_on_permanently_corrupt_file(tmp_path: Path):
    bad_path = tmp_path / "always_bad.epub"
    bad_path.write_bytes(b"na zawsze uszkodzony plik, nie jest archiwum zip")
    result = wait_until_stable(str(bad_path), attempts=4, poll_interval_s=0.05, required_stable_polls=2)
    assert not result.ok


def test_should_abort_stops_waiting_early(tmp_path: Path):
    bad_path = tmp_path / "never_ready.epub"
    bad_path.write_bytes(b"nigdy nie bedzie gotowy")

    start = time.time()
    result = wait_until_stable(
        str(bad_path),
        attempts=1000,
        poll_interval_s=0.05,
        required_stable_polls=2,
        should_abort=lambda: True,
    )
    elapsed = time.time() - start

    assert not result.ok
    assert elapsed < 1.0  # przerwane niemal natychmiast, nie po 1000 próbach
