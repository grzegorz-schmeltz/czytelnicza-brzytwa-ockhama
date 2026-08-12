"""
core.sanitizer
===============

Usuwanie skryptów JavaScript osadzonych w treści EPUB-a.

WAŻNE - uzasadnienie bezpieczeństwa:
Widok WebEngine (ui/webview.py) pozostawia silnik JavaScript włączony
WYŁĄCZNIE po to, aby sama aplikacja mogła wykonać niewielki, w pełni
zaufany skrypt służący do zapamiętania i przywrócenia pozycji przewinięcia
(wymaganie: zachowanie pozycji po przeładowaniu). Żaden skrypt POCHODZĄCY
Z SAMEGO EPUB-a nie może się wykonać, ponieważ - zanim jakikolwiek rozdział
trafi na ekran - ten moduł usuwa z jego kodu HTML/XHTML:

  * wszystkie znaczniki <script>...</script> oraz samozamykające <script/>,
  * wszystkie atrybuty zdarzeń "on..." (onclick, onload, onerror, ...),
  * odnośniki i atrybuty src/href zaczynające się od "javascript:".

Sanityzacja wykonywana jest na kopii plików w katalogu tymczasowym
(nigdy na oryginalnym pliku EPUB) i to na tej oczyszczonej kopii bazuje
cała reszta aplikacji.
"""

from __future__ import annotations

import re

_SCRIPT_TAG_RE = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
_SCRIPT_SELFCLOSE_RE = re.compile(r"<script\b[^>]*/\s*>", re.IGNORECASE)
_ON_EVENT_ATTR_RE = re.compile(
    r"""\s+on[a-zA-Z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""",
    re.IGNORECASE,
)
_JS_URI_RE = re.compile(
    r"""(\bhref|\bsrc)(\s*=\s*)(["'])\s*javascript:[^"']*\3""",
    re.IGNORECASE,
)


def sanitize_html_text(html_text: str) -> str:
    """Zwraca kopię `html_text` z usuniętymi skryptami i obsługą zdarzeń JS."""
    text = _SCRIPT_TAG_RE.sub("", html_text)
    text = _SCRIPT_SELFCLOSE_RE.sub("", text)
    text = _ON_EVENT_ATTR_RE.sub("", text)
    text = _JS_URI_RE.sub(lambda m: f'{m.group(1)}{m.group(2)}{m.group(3)}#{m.group(3)}', text)
    return text


def sanitize_file_in_place(path: str) -> None:
    """Wczytuje plik XHTML/HTML, usuwa skrypty i zapisuje z powrotem (UTF-8)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        return

    sanitized = sanitize_html_text(content)
    if sanitized != content:
        with open(path, "w", encoding="utf-8") as f:
            f.write(sanitized)


HTML_MEDIA_TYPES = {"application/xhtml+xml", "text/html"}


def sanitize_book_documents(book) -> None:
    """
    Sanityzuje wszystkie dokumenty HTML/XHTML wymienione w manifeście
    (rozdziały spine oraz dokument nawigacyjny EPUB 3).
    """
    import os

    for item in book.manifest.values():
        if item.media_type in HTML_MEDIA_TYPES:
            full_path = os.path.normpath(os.path.join(book.opf_dir, item.href))
            if os.path.isfile(full_path):
                sanitize_file_in_place(full_path)
