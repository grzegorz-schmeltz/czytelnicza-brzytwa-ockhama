from __future__ import annotations

from core.sanitizer import sanitize_html_text


def test_removes_script_tag_with_content():
    html = "<html><head><script>alert('zly')</script></head><body>Tekst</body></html>"
    result = sanitize_html_text(html)
    assert "<script" not in result.lower()
    assert "alert" not in result


def test_removes_self_closing_script_tag():
    html = '<html><head><script src="evil.js"/></head><body>Tekst</body></html>'
    result = sanitize_html_text(html)
    assert "<script" not in result.lower()


def test_removes_on_event_attributes():
    html = '<body onload="doZlego()"><p onclick="tezZle()">Tekst</p></body>'
    result = sanitize_html_text(html)
    assert "onload" not in result
    assert "onclick" not in result
    assert "Tekst" in result


def test_neutralizes_javascript_uri():
    html = '<a href="javascript:doZlego()">Kliknij</a>'
    result = sanitize_html_text(html)
    assert "javascript:" not in result


def test_preserves_normal_content_and_polish_characters():
    html = "<p>Zwykły tekst z polskimi znakami: ąćęłńóśźż ĄĆĘŁŃÓŚŹŻ.</p>"
    result = sanitize_html_text(html)
    assert result == html
