from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import zipfile

from core.easyreader_annotations import load_document
from tools import easyreader

from .epub_builder import build_epub3


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def configure_private_roots(monkeypatch, tmp_path: Path) -> None:
    state_root = tmp_path / ".easyreader"
    monkeypatch.setattr(easyreader, "BOOKS_ROOT", tmp_path / "books")
    monkeypatch.setattr(easyreader, "STATE_ROOT", state_root)
    monkeypatch.setattr(easyreader, "ACTIVE_FILE", state_root / "active_book.txt")


def test_epub_workflow_keeps_original_and_stores_notes_separately(monkeypatch, tmp_path: Path):
    configure_private_roots(monkeypatch, tmp_path)
    source = build_epub3(tmp_path / "oryginal.epub")
    original_hash = digest(source)

    easyreader.command_init(SimpleNamespace(source=str(source), name="test", force=False))
    book_dir = easyreader.BOOKS_ROOT / "test"
    state = easyreader.load_state(book_dir)

    assert Path(state["source_epub"]) == source.resolve()
    assert "working_epub" not in state
    assert not list(book_dir.rglob("*.epub"))
    notes = Path(state["annotations_file"])
    assert notes.suffix == ".easyreader"

    easyreader.command_next(SimpleNamespace(book=str(book_dir), chars=250))
    proposal = json.loads((book_dir / "temp" / "opracowanie.json").read_text(encoding="utf-8"))
    proposal["prosty_jezyk"] = "Proste objaśnienie testowe."
    easyreader.save_json(book_dir / "temp" / "opracowanie.json", proposal)
    easyreader.command_apply(SimpleNamespace(book=str(book_dir), data=None))

    document = load_document(notes)
    assert len(document["annotations"]) == 1
    assert document["annotations"][0]["content"]["prosty_jezyk"] == "Proste objaśnienie testowe."
    assert digest(source) == original_hash


def test_migration_preserves_legacy_epub_and_recovers_annotations(monkeypatch, tmp_path: Path):
    configure_private_roots(monkeypatch, tmp_path)
    book_dir = tmp_path / "legacy"
    history_dir = book_dir / "temp" / "historia"
    history_dir.mkdir(parents=True)
    source = build_epub3(tmp_path / "source.epub")
    working = book_dir / "legacy_easyReader.epub"
    shutil.copy2(source, working)

    with zipfile.ZipFile(source) as archive:
        chapter = archive.read("OEBPS/chap1.xhtml").decode("utf-8")
    blocks = list(easyreader.BLOCK_RE.finditer(chapter))
    anchor = blocks[1].group(0)
    proposal = {
        "fragment_id": "fragment-0001",
        "modernizacja": "",
        "prosty_jezyk": "Odzyskane objaśnienie.",
        "objasnienia": [],
        "komentarz_ai": "",
        "notatka_czytelnika": "",
    }
    insertion = easyreader.build_annotation(proposal, "fragment-0001")
    position = chapter.find(anchor) + len(anchor)
    changed = chapter[:position] + insertion + chapter[position:]
    easyreader.zip_replace(working, {"OEBPS/chap1.xhtml": changed.encode("utf-8")})
    easyreader.save_json(history_dir / "fragment-0001_opracowanie.json", proposal)
    easyreader.save_json(
        book_dir / "postep.json",
        {
            "format": 1,
            "name": "legacy",
            "source_epub": str(source),
            "working_epub": str(working),
            "history": [
                {"id": "fragment-0001", "status": "applied", "section": "OEBPS/chap1.xhtml"}
            ],
        },
    )
    old_working_hash = digest(working)

    easyreader.command_migrate(SimpleNamespace(book=str(book_dir)))

    state = easyreader.load_state(book_dir)
    notes = load_document(state["annotations_file"])
    assert state["format"] == 2
    assert len(notes["annotations"]) == 1
    assert notes["annotations"][0]["content"]["prosty_jezyk"] == "Odzyskane objaśnienie."
    assert digest(working) == old_working_hash
    assert (book_dir / "postep_format1_backup.json").is_file()
