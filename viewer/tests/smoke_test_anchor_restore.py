"""
Test dymny kotwicy czytania: sprawdza WPROST (bez pełnego cyklu
watchdog/debounce), że `capture_reading_anchor` / `restore_reading_anchor`
poprawnie odnajdują ten sam akapit nawet wtedy, gdy przed bieżącym miejscem
czytania zostały wstawione nowe akapity (np. tłumaczenia) - a więc gdy sam
procent przewinięcia wskazywałby już zupełnie inne miejsce.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PySide6.QtCore import QEventLoop, QTimer, QUrl  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


def build_doc(paragraph_texts: list[str]) -> str:
    body = "\n".join(f"<p>{t}</p>" for t in paragraph_texts)
    # Dużo pustej przestrzeni na końcu, żeby dokument był wystarczająco
    # wysoki i faktycznie przewijalny niezależnie od rozmiaru okna.
    filler = "<p style='height:400px'>&nbsp;</p>" * 3
    return f"<html><head><meta charset='utf-8'></head><body>{body}{filler}</body></html>"


def run_js_sync(view, script: str, timeout_ms: int = 3000):
    import json as _json

    loop = QEventLoop()
    result_holder = {"value": None, "done": False}

    def _on_result(value):
        if isinstance(value, str) and value:
            try:
                value = _json.loads(value)
            except (TypeError, ValueError):
                pass
        result_holder["value"] = value
        result_holder["done"] = True
        loop.quit()

    view.page().runJavaScript(script, 0, _on_result)

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(timeout_ms)
    if not result_holder["done"]:
        loop.exec()
    return result_holder["value"]


def load_sync(view, html_path: str, timeout_ms: int = 5000) -> bool:
    loop = QEventLoop()
    ok_holder = {"ok": False}

    def _on_loaded(ok):
        ok_holder["ok"] = ok
        loop.quit()

    view.loadFinished.connect(_on_loaded)
    view.setUrl(QUrl.fromLocalFile(html_path))

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(timeout_ms)
    loop.exec()
    try:
        view.loadFinished.disconnect(_on_loaded)
    except (RuntimeError, TypeError):
        pass
    return ok_holder["ok"]


def main() -> int:
    import tempfile
    from pathlib import Path

    tmp_dir = Path(tempfile.mkdtemp(prefix="epubviewer_smoke_anchor_"))

    # Wersja "przed": 20 akapitów o unikalnej treści.
    before_paragraphs = [f"Akapit numer {i} z unikalna trescia XYZ{i}." for i in range(20)]
    doc_before = tmp_dir / "before.html"
    doc_before.write_text(build_doc(before_paragraphs), encoding="utf-8")

    # Wersja "po": wstawiamy 5 NOWYCH akapitów PRZED akapitem 10 (symulacja
    # tłumaczeń dopisanych wcześniej w dokumencie) - ten sam akapit-kotwica
    # ("Akapit numer 10 ...") teraz ma zupełnie inny indeks i inną pozycję
    # względem góry dokumentu.
    inserted = [f"NOWY DOPISANY AKAPIT {i} (tlumaczenie)." for i in range(5)]
    after_paragraphs = before_paragraphs[:10] + inserted + before_paragraphs[10:]
    doc_after = tmp_dir / "after.html"
    doc_after.write_text(build_doc(after_paragraphs), encoding="utf-8")

    app = QApplication(sys.argv)

    from ui.webview import BookWebView

    view = BookWebView(opf_dir_provider=lambda: None)
    view.resize(900, 500)
    view.show()

    assert load_sync(view, str(doc_before)), "Nie udalo sie zaladowac dokumentu 'przed'"
    print("[1/5] Zaladowano dokument bazowy (20 akapitow).")

    # Przewijamy tak, by akapit nr 10 znalazl sie na samej gorze okna.
    run_js_sync(
        view,
        "(function(){"
        "var ps = document.querySelectorAll('p');"
        "for (var i=0;i<ps.length;i++){ if (ps[i].textContent.indexOf('Akapit numer 10 ') === 0){"
        "  ps[i].scrollIntoView({block:'start'}); return true; } }"
        "return false;"
        "})();",
    )
    print("[2/5] Przewinieto tak, by 'Akapit numer 10' byl na gorze ekranu.")

    anchor = run_js_sync(
        view,
        (
            "(function(){"
            "var selector = 'p, h1, h2, h3, h4, h5, h6, li, blockquote, dt, dd, pre, td, th, figcaption';"
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
            "return JSON.stringify({found: true, id: found.id || null, index: foundIndex, total: elements.length,"
            "        textSnippet: text, offsetTop: rect.top, ratio: ratio});"
            "})();"
        ),
    )
    assert anchor and anchor.get("found"), f"Nie udalo sie przechwycic kotwicy: {anchor}"
    assert anchor["textSnippet"].startswith("Akapit numer 10 "), anchor
    captured_index = anchor["index"]
    print(f"[3/5] Przechwycono kotwice: index={captured_index}, tekst='{anchor['textSnippet'][:40]}...'")

    # Ładujemy nową wersję (z 5 dopisanymi akapitami PRZED kotwicą) i przywracamy pozycję.
    assert load_sync(view, str(doc_after)), "Nie udalo sie zaladowac dokumentu 'po'"
    view.restore_reading_anchor(anchor)

    # runJavaScript wywołane przez restore_reading_anchor jest asynchroniczne
    # (fire-and-forget) - odczekujemy chwilę w pętli zdarzeń, zanim sprawdzimy wynik.
    wait_loop = QEventLoop()
    QTimer.singleShot(300, wait_loop.quit)
    wait_loop.exec()

    check = run_js_sync(
        view,
        "(function(){"
        "var ps = document.querySelectorAll('p');"
        "var target = null, index=-1;"
        "for (var i=0;i<ps.length;i++){ if (ps[i].textContent.indexOf('Akapit numer 10 ') === 0){"
        "  target = ps[i]; index = i; break; } }"
        "if (!target) return JSON.stringify({ok:false});"
        "var rect = target.getBoundingClientRect();"
        "return JSON.stringify({ok:true, top: rect.top, newIndex: index});"
        "})();",
    )
    assert check and check.get("ok"), "Nie odnaleziono akapitu-kotwicy w nowej wersji dokumentu"
    print(f"[4/5] Nowy indeks akapitu-kotwicy w dokumencie 'po': {check['newIndex']} (poprzednio {captured_index}).")

    # Kluczowa asercja: mimo że indeks akapitu przesunął się o 5 (wstawione
    # tłumaczenia), po przywróceniu kotwicy akapit nadal jest tuż przy górze
    # okna (tam, gdzie był w momencie przechwycenia), z tolerancją na
    # zaokrąglenia/layout - a NIE tam, gdzie wskazywałby czysty procent.
    assert abs(check["top"]) < 5, f"Akapit-kotwica nie jest na gorze okna po przywroceniu (top={check['top']})"
    assert check["newIndex"] != captured_index, "Test nie ma sensu, jesli indeks sie nie zmienil"
    print("[5/5] Akapit-kotwica jest na tej samej pozycji na ekranie mimo przesuniecia indeksu o 5 - SUKCES.")

    view.close()
    app.quit()

    print("\nTEST KOTWICY CZYTANIA ZAKONCZONY SUKCESEM.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
