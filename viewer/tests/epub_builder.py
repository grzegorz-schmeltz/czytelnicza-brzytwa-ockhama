"""
Pomocnicze funkcje do budowania minimalnych, poprawnych plików EPUB
(w wersji 2 i 3) na potrzeby testów, bez zależności od zewnętrznych bibliotek.
"""
from __future__ import annotations

import zipfile
from pathlib import Path

CONTAINER_XML = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

OPF_EPUB3 = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Książka testowa - żółć</dc:title>
    <dc:identifier id="bookid">urn:uuid:test-1234</dc:identifier>
    <dc:language>pl</dc:language>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="chap1" href="chap1.xhtml" media-type="application/xhtml+xml"/>
    <item id="chap2" href="chap2.xhtml" media-type="application/xhtml+xml"/>
    <item id="css" href="style.css" media-type="text/css"/>
  </manifest>
  <spine>
    <itemref idref="chap1"/>
    <itemref idref="chap2"/>
  </spine>
</package>
"""

NAV_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Spis treści</title></head>
<body>
  <nav epub:type="toc">
    <ol>
      <li><a href="chap1.xhtml">Rozdział pierwszy - żółw</a></li>
      <li><a href="chap2.xhtml">Rozdział drugi - ąęśćźż</a></li>
    </ol>
  </nav>
</body>
</html>
"""

CHAP1_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Rozdział 1</title><link rel="stylesheet" href="style.css"/></head>
<body>
  <h1>Rozdział pierwszy</h1>
  <p>To jest treść z polskimi znakami: ąćęłńóśźż ĄĆĘŁŃÓŚŹŻ.</p>
  <p><a href="chap2.xhtml">Przejdź do rozdziału drugiego</a></p>
  <p><a href="https://example.com">Odnośnik zewnętrzny</a></p>
</body>
</html>
"""

CHAP2_XHTML = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Rozdział 2</title></head>
<body>
  <h1>Rozdział drugi</h1>
  <p>Koniec książki testowej.</p>
</body>
</html>
"""

STYLE_CSS = "body { font-family: sans-serif; }\n"

# Wersja z osadzonym (niedozwolonym) skryptem - do testów sanityzacji.
CHAP1_XHTML_WITH_SCRIPT = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Rozdział 1</title>
<script>alert('zły skrypt');</script>
</head>
<body onload="alert('zdarzenie')">
  <h1>Rozdział pierwszy</h1>
  <p onclick="doZlegoRzeczy()">Tekst</p>
  <a href="javascript:doZlegoRzeczy()">Zły odnośnik</a>
</body>
</html>
"""

# --- NCX / EPUB2 ---

OPF_EPUB2 = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Stara książka EPUB2</dc:title>
    <dc:identifier id="bookid">urn:uuid:test-2222</dc:identifier>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="chap1" href="chap1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="chap1"/>
  </spine>
</package>
"""

NCX = """<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <navMap>
    <navPoint id="np1" playOrder="1">
      <navLabel><text>Rozdział pierwszy</text></navLabel>
      <content src="chap1.xhtml"/>
    </navPoint>
  </navMap>
</ncx>
"""


def build_epub3(dest_path: Path, with_script: bool = False) -> Path:
    """Tworzy minimalny, poprawny plik EPUB 3 pod wskazaną ścieżką."""
    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        zf.writestr("OEBPS/content.opf", OPF_EPUB3)
        zf.writestr("OEBPS/nav.xhtml", NAV_XHTML)
        zf.writestr("OEBPS/chap1.xhtml", CHAP1_XHTML_WITH_SCRIPT if with_script else CHAP1_XHTML)
        zf.writestr("OEBPS/chap2.xhtml", CHAP2_XHTML)
        zf.writestr("OEBPS/style.css", STYLE_CSS)
    return dest_path


def build_epub2(dest_path: Path) -> Path:
    """Tworzy minimalny, poprawny plik EPUB 2 (NCX) pod wskazaną ścieżką."""
    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        zf.writestr("OEBPS/content.opf", OPF_EPUB2)
        zf.writestr("OEBPS/toc.ncx", NCX)
        zf.writestr("OEBPS/chap1.xhtml", CHAP1_XHTML)
    return dest_path


def build_zip_slip_epub(dest_path: Path) -> Path:
    """Tworzy archiwum ZIP z wpisem próbującym wyjść poza katalog docelowy."""
    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        zf.writestr("OEBPS/content.opf", OPF_EPUB3)
        zf.writestr("OEBPS/nav.xhtml", NAV_XHTML)
        zf.writestr("OEBPS/chap1.xhtml", CHAP1_XHTML)
        zf.writestr("OEBPS/chap2.xhtml", CHAP2_XHTML)
        # Złośliwy wpis - próba zapisu poza katalogiem docelowym.
        zf.writestr("../../evil.txt", "pwned")
    return dest_path


def build_zip_slip_absolute_epub(dest_path: Path) -> Path:
    """Wariant Zip Slip ze ścieżką bezwzględną wewnątrz archiwum."""
    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER_XML)
        zf.writestr("OEBPS/content.opf", OPF_EPUB3)
        zf.writestr("OEBPS/nav.xhtml", NAV_XHTML)
        zf.writestr("OEBPS/chap1.xhtml", CHAP1_XHTML)
        zf.writestr("OEBPS/chap2.xhtml", CHAP2_XHTML)
        info = zipfile.ZipInfo("/etc/evil.txt")
        zf.writestr(info, "pwned")
    return dest_path
