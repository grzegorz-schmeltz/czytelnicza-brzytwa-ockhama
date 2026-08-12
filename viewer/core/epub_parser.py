"""
core.epub_parser
=================

Parsowanie struktury książki EPUB (2 i 3) na podstawie już bezpiecznie
rozpakowanego katalogu.

Kolejność działania zgodna ze specyfikacją EPUB:

  1. META-INF/container.xml -> ścieżka do pliku OPF (rootfile).
  2. Plik OPF -> metadata (tytuł), manifest (id -> href/media-type),
     spine (kolejność czytania wg idref).
  3. Spis treści:
       - EPUB 3: dokument nawigacyjny (manifest item z properties="nav"),
         sekcja <nav epub:type="toc">.
       - EPUB 2 / zapasowo: plik NCX (media-type
         application/x-dtbncx+xml lub atrybut toc w <spine>), <navMap>.

Moduł nie zależy od Qt - może być testowany w izolacji.
"""

from __future__ import annotations

import dataclasses
import os
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional


class EpubParseError(Exception):
    """Zgłaszany, gdy struktura EPUB jest niepoprawna lub niekompletna."""


NS = {
    "container": "urn:oasis:names:tc:opendocument:xmlns:container",
    "opf": "http://www.idpf.org/2007/opf",
    "dc": "http://purl.org/dc/elements/1.1/",
    "xhtml": "http://www.w3.org/1999/xhtml",
    "epub": "http://www.idpf.org/2007/ops",
    "ncx": "http://www.daisy.org/z3986/2005/ncx/",
}


@dataclasses.dataclass
class ManifestItem:
    item_id: str
    href: str            # ścieżka względem katalogu OPF
    media_type: str
    properties: str = ""


@dataclasses.dataclass
class TocEntry:
    title: str
    href: str             # ścieżka względem katalogu OPF, może zawierać fragment (#id)
    children: List["TocEntry"] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class EpubBook:
    root_dir: str                       # katalog, w którym rozpakowano EPUB
    opf_dir: str                        # katalog zawierający plik OPF (bezwzględny)
    opf_path: str                       # pełna ścieżka do pliku OPF
    title: str
    manifest: Dict[str, ManifestItem]   # item_id -> ManifestItem
    spine: List[str]                    # lista href (względem opf_dir), w kolejności czytania
    toc: List[TocEntry]

    def spine_paths(self) -> List[str]:
        return list(self.spine)

    def nearest_spine_index(self, href: Optional[str]) -> int:
        """Zwraca indeks w spine najbliższy podanemu href (dla zachowania rozdziału po reloadzie)."""
        if not href:
            return 0
        target = href.split("#")[0]
        if target in self.spine:
            return self.spine.index(target)
        # Brak identycznego rozdziału - szukamy najbardziej zbliżonej nazwy pliku.
        target_name = os.path.basename(target)
        for i, sp in enumerate(self.spine):
            if os.path.basename(sp) == target_name:
                return i
        return 0


def _local(tag: str) -> str:
    """Zwraca lokalną nazwę znacznika XML bez przestrzeni nazw."""
    return tag.split("}", 1)[-1] if "}" in tag else tag


def _find_opf_path(root_dir: str) -> str:
    container_path = os.path.join(root_dir, "META-INF", "container.xml")
    if not os.path.isfile(container_path):
        raise EpubParseError("Brak pliku META-INF/container.xml - to nie jest poprawny EPUB.")

    try:
        tree = ET.parse(container_path)
    except ET.ParseError as exc:
        raise EpubParseError(f"Nie można sparsować container.xml: {exc}") from exc

    root = tree.getroot()
    rootfile = None
    for el in root.iter():
        if _local(el.tag) == "rootfile":
            rootfile = el
            break
    if rootfile is None or "full-path" not in rootfile.attrib:
        raise EpubParseError("container.xml nie zawiera poprawnego wpisu <rootfile>.")

    full_path = rootfile.attrib["full-path"]
    opf_path = os.path.normpath(os.path.join(root_dir, full_path))
    if not os.path.isfile(opf_path):
        raise EpubParseError(f"Plik OPF wskazany w container.xml nie istnieje: {full_path}")
    return opf_path


def _parse_opf(opf_path: str):
    try:
        tree = ET.parse(opf_path)
    except ET.ParseError as exc:
        raise EpubParseError(f"Nie można sparsować pliku OPF: {exc}") from exc
    root = tree.getroot()

    # --- metadata: tytuł ---
    title = "(brak tytułu)"
    for el in root.iter():
        if _local(el.tag) == "title" and el.text and el.text.strip():
            title = el.text.strip()
            break

    # --- manifest ---
    manifest: Dict[str, ManifestItem] = {}
    manifest_el = None
    for el in root:
        if _local(el.tag) == "manifest":
            manifest_el = el
            break
    if manifest_el is None:
        raise EpubParseError("Plik OPF nie zawiera sekcji <manifest>.")

    for item in manifest_el:
        if _local(item.tag) != "item":
            continue
        item_id = item.attrib.get("id")
        href = item.attrib.get("href")
        media_type = item.attrib.get("media-type", "")
        properties = item.attrib.get("properties", "")
        if not item_id or not href:
            continue
        manifest[item_id] = ManifestItem(item_id=item_id, href=href, media_type=media_type, properties=properties)

    # --- spine ---
    spine_el = None
    for el in root:
        if _local(el.tag) == "spine":
            spine_el = el
            break
    if spine_el is None:
        raise EpubParseError("Plik OPF nie zawiera sekcji <spine>.")

    spine_hrefs: List[str] = []
    for itemref in spine_el:
        if _local(itemref.tag) != "itemref":
            continue
        idref = itemref.attrib.get("idref")
        linear = itemref.attrib.get("linear", "yes")
        if not idref or idref not in manifest:
            continue
        if linear == "no":
            # Strony nieliniowe pomijamy w głównej sekwencji czytania,
            # ale nadal będą dostępne poprzez odnośniki wewnętrzne.
            continue
        spine_hrefs.append(manifest[idref].href)

    ncx_toc_id = spine_el.attrib.get("toc")

    if not spine_hrefs:
        raise EpubParseError("Sekcja <spine> nie zawiera żadnych poprawnych pozycji.")

    return title, manifest, spine_hrefs, ncx_toc_id


def _find_nav_item(manifest: Dict[str, ManifestItem]) -> Optional[ManifestItem]:
    for item in manifest.values():
        if "nav" in item.properties.split():
            return item
    return None


def _find_ncx_item(manifest: Dict[str, ManifestItem], ncx_toc_id: Optional[str]) -> Optional[ManifestItem]:
    if ncx_toc_id and ncx_toc_id in manifest:
        return manifest[ncx_toc_id]
    for item in manifest.values():
        if item.media_type == "application/x-dtbncx+xml":
            return item
    return None


def _parse_epub3_nav(nav_path: str) -> List[TocEntry]:
    try:
        tree = ET.parse(nav_path)
    except ET.ParseError as exc:
        raise EpubParseError(f"Nie można sparsować dokumentu nawigacyjnego EPUB 3: {exc}") from exc
    root = tree.getroot()

    toc_nav = None
    for nav in root.iter():
        if _local(nav.tag) != "nav":
            continue
        nav_type = nav.attrib.get("{%s}type" % NS["epub"], nav.attrib.get("type", ""))
        if "toc" in nav_type.split():
            toc_nav = nav
            break
    if toc_nav is None:
        # Zapasowo: bierzemy pierwszy <nav>, jeśli istnieje.
        for nav in root.iter():
            if _local(nav.tag) == "nav":
                toc_nav = nav
                break
    if toc_nav is None:
        return []

    def parse_ol(ol_el) -> List[TocEntry]:
        entries: List[TocEntry] = []
        for li in ol_el:
            if _local(li.tag) != "li":
                continue
            a_el = None
            sub_ol = None
            for child in li:
                lname = _local(child.tag)
                if lname == "a" and a_el is None:
                    a_el = child
                elif lname == "ol":
                    sub_ol = child
            if a_el is None:
                continue
            href = a_el.attrib.get("href", "")
            text = "".join(a_el.itertext()).strip() or "(bez tytułu)"
            children = parse_ol(sub_ol) if sub_ol is not None else []
            entries.append(TocEntry(title=text, href=href, children=children))
        return entries

    for child in toc_nav:
        if _local(child.tag) == "ol":
            return parse_ol(child)
    return []


def _parse_ncx(ncx_path: str) -> List[TocEntry]:
    try:
        tree = ET.parse(ncx_path)
    except ET.ParseError as exc:
        raise EpubParseError(f"Nie można sparsować pliku NCX: {exc}") from exc
    root = tree.getroot()

    nav_map = None
    for el in root:
        if _local(el.tag) == "navMap":
            nav_map = el
            break
    if nav_map is None:
        return []

    def parse_navpoints(parent) -> List[TocEntry]:
        entries: List[TocEntry] = []
        for navpoint in parent:
            if _local(navpoint.tag) != "navPoint":
                continue
            label_text = "(bez tytułu)"
            href = ""
            children: List[TocEntry] = []
            for child in navpoint:
                lname = _local(child.tag)
                if lname == "navLabel":
                    for text_el in child:
                        if _local(text_el.tag) == "text" and text_el.text:
                            label_text = text_el.text.strip()
                elif lname == "content":
                    href = child.attrib.get("src", "")
                elif lname == "navPoint":
                    pass  # obsłużone niżej rekurencyjnie
            children = parse_navpoints(navpoint)
            entries.append(TocEntry(title=label_text, href=href, children=children))
        return entries

    return parse_navpoints(nav_map)


def parse_epub_book(extracted_dir: str) -> EpubBook:
    """
    Buduje pełną strukturę EpubBook na podstawie już rozpakowanego katalogu.
    Zgłasza EpubParseError, jeśli struktura jest niepoprawna.
    """
    opf_path = _find_opf_path(extracted_dir)
    opf_dir = os.path.dirname(opf_path)

    title, manifest, spine_hrefs, ncx_toc_id = _parse_opf(opf_path)

    # Normalizacja hrefów manifestu/spine do ścieżek względem opf_dir,
    # ale przechowujemy je jako proste stringi (URL-decode dla bezpieczeństwa).
    from urllib.parse import unquote

    def norm_href(href: str) -> str:
        return unquote(href)

    for item in manifest.values():
        item.href = norm_href(item.href)
    spine_hrefs = [norm_href(h) for h in spine_hrefs]

    # Walidacja, że pliki spine faktycznie istnieją na dysku.
    valid_spine = []
    for href in spine_hrefs:
        full_path = os.path.normpath(os.path.join(opf_dir, href))
        if os.path.isfile(full_path):
            valid_spine.append(href)
    if not valid_spine:
        raise EpubParseError("Żaden z plików wskazanych w spine nie istnieje na dysku.")

    toc: List[TocEntry] = []
    nav_item = _find_nav_item(manifest)
    if nav_item is not None:
        nav_full_path = os.path.normpath(os.path.join(opf_dir, norm_href(nav_item.href)))
        if os.path.isfile(nav_full_path):
            try:
                toc = _parse_epub3_nav(nav_full_path)
            except EpubParseError:
                toc = []

    if not toc:
        ncx_item = _find_ncx_item(manifest, ncx_toc_id)
        if ncx_item is not None:
            ncx_full_path = os.path.normpath(os.path.join(opf_dir, norm_href(ncx_item.href)))
            if os.path.isfile(ncx_full_path):
                try:
                    toc = _parse_ncx(ncx_full_path)
                except EpubParseError:
                    toc = []

    if not toc:
        # Ostateczne zapasowe rozwiązanie: spis treści = kolejność spine.
        toc = [TocEntry(title=os.path.basename(href), href=href) for href in valid_spine]

    return EpubBook(
        root_dir=extracted_dir,
        opf_dir=opf_dir,
        opf_path=opf_path,
        title=title,
        manifest=manifest,
        spine=valid_spine,
        toc=toc,
    )
