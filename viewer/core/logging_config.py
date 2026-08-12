"""
core.logging_config
====================

Konfiguracja logowania do lokalnego pliku dziennika (nie wysyła niczego
przez sieć). Katalog logów tworzony jest obok programu (lub w katalogu
danych użytkownika, jeśli katalog programu jest tylko do odczytu, np. po
instalacji w "Program Files").
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler


def _default_log_dir() -> str:
    if getattr(sys, "frozen", False):
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    log_dir = os.path.join(base_dir, "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        test_path = os.path.join(log_dir, ".write_test")
        with open(test_path, "w") as f:
            f.write("ok")
        os.remove(test_path)
        return log_dir
    except OSError:
        # Katalog programu jest tylko do odczytu - używamy katalogu danych użytkownika.
        appdata = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        log_dir = os.path.join(appdata, "EpubViewer", "logs")
        os.makedirs(log_dir, exist_ok=True)
        return log_dir


def setup_logging(level: int = logging.INFO) -> str:
    """Konfiguruje logowanie do pliku i konsoli. Zwraca ścieżkę do pliku dziennika."""
    log_dir = _default_log_dir()
    log_path = os.path.join(log_dir, "epub_viewer.log")

    root_logger = logging.getLogger("epub_viewer")
    root_logger.setLevel(level)
    root_logger.propagate = False

    if root_logger.handlers:
        return log_path  # już skonfigurowane (np. w testach)

    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    return log_path
