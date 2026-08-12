# -*- mode: python ; coding: utf-8 -*-
#
# Konfiguracja PyInstaller dla "Podgląd EPUB na żywo".
#
# Budowanie (Windows, w aktywnym środowisku wirtualnym z zainstalowanym
# pyinstaller oraz wszystkimi zależnościami z requirements.txt):
#
#     pyinstaller epub_viewer.spec
#
# Wynikowy plik .exe pojawi się w katalogu dist\PodgladEpub\PodgladEpub.exe
# (tryb --onedir - zalecany dla aplikacji korzystających z Qt WebEngine,
# ponieważ silnik Chromium potrzebuje własnych plików pomocniczych, których
# upakowanie w pojedynczy plik .exe znacząco wydłuża czas startu).
#
# Jeżeli koniecznie potrzebny jest pojedynczy plik .exe, można dodać
# opcję `--onefile` przy wywołaniu pyinstaller, ale pierwsze uruchomienie
# będzie zauważalnie wolniejsze (rozpakowanie do katalogu tymczasowego).

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

project_root = Path(SPECPATH)

# PySide6 dostarcza własny hook, który poprawnie dołącza wszystkie pliki
# Qt WebEngine (QtWebEngineProcess.exe, silnik Chromium, lokalizacje, ICU,
# itp.), dlatego wystarczy wskazać hiddenimports oraz zebrać dane pakietu.
hidden_imports = [
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebChannel",
    "PySide6.QtPrintSupport",
    "watchdog.observers.winapi",
]

datas = []
datas += collect_data_files("PySide6", subdir="Qt6/resources")
datas += collect_data_files("PySide6", subdir="Qt6/translations")

a = Analysis(
    ["main.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PodgladEpub",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="PodgladEpub",
)
