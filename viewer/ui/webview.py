"""
ui.webview
==========

Bezpieczny widok WebEngine do wyświetlania treści XHTML rozdziału EPUB.

Zasady bezpieczeństwa:
  * JavaScript jest CAŁKOWICIE wyłączony (treść EPUB może zawierać skrypty,
    których nie chcemy wykonywać).
  * Wszystkie żądania sieciowe o schemacie innym niż "file" są blokowane
    (żadnych żądań http/https do zasobów wewnątrz strony).
  * Kliknięcia w odnośniki są przechwytywane:
      - odnośnik do innego pliku XHTML wewnątrz książki -> przełącza rozdział
        w viewerze (nie ładuje bezpośrednio, aby MainWindow mógł zaktualizować
        TOC/pasek stanu),
      - odnośnik z samym fragmentem (#kotwica) w obrębie tego samego pliku
        -> obsługiwany natywnie przez silnik (przewinięcie do kotwicy),
      - odnośnik zewnętrzny (http/https/mailto/...) -> NIE otwiera się
        automatycznie, tylko emitowany jest sygnał proszący użytkownika
        o potwierdzenie.
"""

from __future__ import annotations

import json
import logging
import os

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineSettings,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

logger = logging.getLogger("epub_viewer.webview")


class LocalOnlyRequestInterceptor(QWebEngineUrlRequestInterceptor):
    """Blokuje wszystkie żądania sieciowe niepochodzące ze schematu 'file' lub 'data'."""

    ALLOWED_SCHEMES = {"file", "data"}

    def interceptRequest(self, info) -> None:  # noqa: N802 (nazwa wymagana przez Qt)
        scheme = bytes(info.requestUrl().scheme(), "utf-8") if False else info.requestUrl().scheme()
        if scheme not in self.ALLOWED_SCHEMES:
            logger.warning("Zablokowano żądanie sieciowe spoza treści lokalnej: %s", info.requestUrl().toString())
            info.block(True)


class EpubWebEnginePage(QWebEnginePage):
    """
    Strona WebEngine z wyłączonym JavaScriptem oraz przechwytywaniem nawigacji.

    Sygnały:
      internal_link_activated(str href_relative_to_opf_dir)
      external_link_requested(str url)
    """

    internal_link_activated = Signal(str)
    external_link_requested = Signal(str)

    def __init__(self, profile: QWebEngineProfile, opf_dir_provider, parent=None):
        super().__init__(profile, parent)
        self._opf_dir_provider = opf_dir_provider  # callable -> aktualny opf_dir (str|None)

        settings = self.settings()
        # UWAGA - patrz core/sanitizer.py: silnik JS pozostaje włączony wyłącznie
        # po to, aby SAMA APLIKACJA mogła wykonać zaufany, wewnętrzny skrypt
        # zapamiętujący/przywracający pozycję przewinięcia. Żaden skrypt
        # POCHODZĄCY Z TREŚCI EPUB nie zostanie wykonany, ponieważ każdy
        # rozdział jest sanityzowany (usuwanie <script>, atrybutów on*=,
        # odnośników javascript:) zanim trafi na dysk katalogu tymczasowego -
        # patrz core.sanitizer.sanitize_book_documents(), wywoływane w
        # core.preview_state przed pierwszym wyświetleniem i po każdym reloadzie.
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanAccessClipboard, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)

    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame: bool) -> bool:  # noqa: N802
        # Pierwsze/programowe wczytanie rozdziału (ustawiane przez MainWindow.setUrl) - zawsze dozwolone.
        if nav_type != QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            return True

        if url.scheme() not in ("file",):
            # Odnośnik zewnętrzny (http/https/mailto/itp.) - nie otwieramy automatycznie.
            self.external_link_requested.emit(url.toString())
            return False

        opf_dir = self._opf_dir_provider()
        if not opf_dir:
            return True

        target_path = os.path.normpath(url.toLocalFile())
        current_path = os.path.normpath(self.url().toLocalFile()) if self.url().isLocalFile() else None

        if current_path and target_path == current_path:
            # Odnośnik do kotwicy w tym samym dokumencie - pozwól silnikowi obsłużyć natywnie.
            return True

        # Sprawdzamy, że cel nadal znajduje się wewnątrz rozpakowanej książki.
        try:
            common = os.path.commonpath([opf_dir, target_path])
        except ValueError:
            common = None
        book_root = os.path.dirname(opf_dir) if os.path.basename(opf_dir) else opf_dir

        if common not in (opf_dir, book_root) and not target_path.startswith(book_root + os.sep):
            self.external_link_requested.emit(url.toString())
            return False

        try:
            relative = os.path.relpath(target_path, opf_dir)
        except ValueError:
            relative = target_path
        relative = relative.replace(os.sep, "/")
        if url.hasFragment():
            relative = f"{relative}#{url.fragment()}"

        self.internal_link_activated.emit(relative)
        return False


class BookWebView(QWebEngineView):
    """QWebEngineView skonfigurowany do bezpiecznego, w pełni lokalnego wyświetlania rozdziałów EPUB."""

    internal_link_activated = Signal(str)
    external_link_requested = Signal(str)

    def __init__(self, opf_dir_provider, parent=None):
        super().__init__(parent)

        # Profil "poza rekordem" (bez trwałego zapisu cache/ciasteczek na dysku).
        self._profile = QWebEngineProfile(self)
        self._profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        self._profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)

        self._interceptor = LocalOnlyRequestInterceptor(self)
        self._profile.setUrlRequestInterceptor(self._interceptor)

        self._page = EpubWebEnginePage(self._profile, opf_dir_provider, self)
        self._page.internal_link_activated.connect(self.internal_link_activated)
        self._page.external_link_requested.connect(self.external_link_requested)
        self.setPage(self._page)

        self._zoom_factor = 1.0

    def load_chapter(self, absolute_path: str, fragment: str = "") -> None:
        url = QUrl.fromLocalFile(absolute_path)
        if fragment:
            url.setFragment(fragment)
        self.setUrl(url)

    def set_zoom_factor(self, factor: float) -> None:
        self._zoom_factor = max(0.25, min(factor, 5.0))
        self.setZoomFactor(self._zoom_factor)

    def zoom_in(self) -> None:
        self.set_zoom_factor(self._zoom_factor + 0.1)

    def zoom_out(self) -> None:
        self.set_zoom_factor(self._zoom_factor - 0.1)

    def zoom_factor(self) -> float:
        return self._zoom_factor

    # ------------------------------------------------------------------ #
    # Zapamiętywanie / przywracanie pozycji czytania ("kotwica czytania").
    #
    # Poniższe wywołania runJavaScript() to jedyny JavaScript, jaki
    # kiedykolwiek wykonuje ta aplikacja - to nasz własny, zaufany kod
    # (nie pochodzi z treści EPUB, która jest wcześniej sanityzowana).
    #
    # Zamiast samego procentu przewinięcia (który po zmianie wysokości
    # dokumentu - np. po dodaniu tłumaczeń przed bieżącym miejscem -
    # zaczyna wskazywać zupełnie inny fragment), zapamiętujemy "kotwicę":
    #   - identyfikator elementu (jeśli ma atrybut id) LUB krótki fragment
    #     jego tekstu,
    #   - jego indeks wśród elementów tekstowych dokumentu (pomaga wybrać
    #     właściwy element, gdy ten sam fragment tekstu występuje kilka
    #     razy w książce),
    #   - przesunięcie tego elementu względem górnej krawędzi okna
    #     (px), aby po przywróceniu akapit znalazł się dokładnie w tym
    #     samym miejscu na ekranie, a nie tylko "gdzieś na stronie".
    # Procent przewinięcia jest zapamiętywany równolegle i służy jako
    # rozwiązanie awaryjne, gdy elementu nie da się odnaleźć w nowej
    # wersji dokumentu (np. cały akapit został usunięty).
    # ------------------------------------------------------------------ #

    _ANCHOR_ELEMENT_SELECTOR = (
        "p, h1, h2, h3, h4, h5, h6, li, blockquote, dt, dd, pre, td, th, figcaption"
    )

    def capture_reading_anchor(self, callback) -> None:
        """
        Odczytuje kotwicę czytania: opis pierwszego widocznego elementu
        tekstowego. Wynik trafia do `callback` jako dict (patrz JS niżej)
        lub `None`, jeśli odczyt się nie powiódł.
        """
        script = (
            "(function(){"
            f"var selector = '{self._ANCHOR_ELEMENT_SELECTOR}';"
            "var elements = Array.prototype.slice.call(document.querySelectorAll(selector));"
            "var viewportH = window.innerHeight || document.documentElement.clientHeight || 0;"
            "var found = null, foundIndex = -1;"
            "for (var i = 0; i < elements.length; i++) {"
            "  var rect = elements[i].getBoundingClientRect();"
            "  if (rect.bottom > 0 && rect.top < viewportH) { found = elements[i]; foundIndex = i; break; }"
            "}"
            "var d = document.documentElement;"
            "var scrollable = Math.max(d.scrollHeight - d.clientHeight, 1);"
            "var ratio = (window.scrollY || d.scrollTop || 0) / scrollable;"
            "if (!found) { return JSON.stringify({found: false, ratio: ratio}); }"
            "var rect = found.getBoundingClientRect();"
            "var text = (found.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 80);"
            "return JSON.stringify({"
            "  found: true,"
            "  id: found.id || null,"
            "  index: foundIndex,"
            "  total: elements.length,"
            "  textSnippet: text,"
            "  offsetTop: rect.top,"
            "  ratio: ratio"
            "});"
            "})();"
        )

        def _on_raw_result(raw) -> None:
            # WAŻNE: ta wersja Qt WebEngine niewiarygodnie przenosi przez
            # runJavaScript() złożone wartości JS (obiekty/tablice) - w
            # testach potrafiły wracać jako pusty string zamiast danych.
            # Dlatego JS zwraca JSON.stringify(...), a tu parsujemy go z
            # powrotem na słownik Pythona - proste typy (string/liczba)
            # przechodzą przez tę warstwę poprawnie.
            anchor = None
            if isinstance(raw, str) and raw:
                try:
                    anchor = json.loads(raw)
                except (TypeError, ValueError):
                    logger.warning("Nie udało się zdekodować kotwicy czytania (JSON): %r", raw)
                    anchor = None
            callback(anchor)

        try:
            # Ta wersja PySide6/Qt WebEngine wymaga trzyargumentowej sygnatury
            # runJavaScript(script, world_id, callback) - wywołanie
            # dwuargumentowe zgłasza TypeError (dopasowywana jest inna
            # sygnatura), więc jawnie podajemy world_id=0 (główny świat JS).
            self.page().runJavaScript(script, 0, _on_raw_result)
        except Exception:
            logger.exception("Nie udało się odczytać kotwicy czytania.")
            callback(None)

    def restore_reading_anchor(self, anchor: dict | None) -> None:
        """
        Odnajduje element opisany przez `anchor` (najpierw po `id`, potem po
        fragmencie tekstu najbliższym oryginalnemu indeksowi, na końcu po
        samym indeksie) i przewija tak, by znalazł się w tym samym miejscu
        względem górnej krawędzi okna, co przy przechwytywaniu. Gdy nic się
        nie odnajdzie, używa zapasowego procentu przewinięcia z `anchor`.
        """
        if not anchor:
            return
        try:
            anchor_json = json.dumps(anchor)
        except (TypeError, ValueError):
            logger.exception("Nie udało się zserializować kotwicy czytania.")
            return

        script = (
            "(function(){"
            f"var anchor = {anchor_json};"
            f"var selector = '{self._ANCHOR_ELEMENT_SELECTOR}';"
            "var elements = Array.prototype.slice.call(document.querySelectorAll(selector));"
            "var target = null;"
            "if (anchor.id) { target = document.getElementById(anchor.id); }"
            "if (!target && anchor.textSnippet) {"
            "  var snippet = anchor.textSnippet;"
            "  var bestEl = null, bestDist = Infinity;"
            "  for (var i = 0; i < elements.length; i++) {"
            "    var t = (elements[i].textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 80);"
            "    if (t.length > 0 && (t === snippet || (snippet.length >= 10 && t.indexOf(snippet) === 0))) {"
            "      var dist = Math.abs(i - anchor.index);"
            "      if (dist < bestDist) { bestDist = dist; bestEl = elements[i]; }"
            "    }"
            "  }"
            "  target = bestEl;"
            "}"
            "if (!target && typeof anchor.index === 'number' && anchor.index >= 0 && anchor.index < elements.length) {"
            "  target = elements[anchor.index];"
            "}"
            "if (target) {"
            "  var rect = target.getBoundingClientRect();"
            "  var currentScroll = window.scrollY || document.documentElement.scrollTop || 0;"
            "  var absoluteTop = rect.top + currentScroll;"
            "  var desired = absoluteTop - (anchor.offsetTop || 0);"
            "  window.scrollTo(0, Math.max(desired, 0));"
            "  return true;"
            "}"
            "var d = document.documentElement;"
            "var scrollable = Math.max(d.scrollHeight - d.clientHeight, 1);"
            "window.scrollTo(0, scrollable * (anchor.ratio || 0));"
            "return false;"
            "})();"
        )
        self.page().runJavaScript(script, 0)
