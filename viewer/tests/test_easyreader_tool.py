"""
Testy prawdziwego CLI `tools/easyreader.py` (punkt 7/9 zgłoszenia).

UWAGA dot. rozbieżności układu projektu: ten plik historycznie importował
moduł `tools.easyreader` i sprawdzał strukturę stanu (`format: 2`,
`annotations_file`), której rzeczywisty CLI nigdy nie implementował -
`tools/easyreader.py` używa innego, wcześniejszego modelu (`postep.json`
+ `working_epub` z adnotacjami WSTAWIANYMI BEZPOŚREDNIO do kopii roboczej
EPUB-a, bez osobnego pliku `.easyreader`). To zupełnie inny, równoległy
mechanizm niż `core.easyreader_annotations` używany przez `viewer` - nie
scalono ich w ramach tego zgłoszenia (poza zakresem punktów 1-9). Ten plik
testuje więc RZECZYWISTE zachowanie CLI, a nie nieistniejący projekt formatu 2.

Katalog `tools` jest importowany jako pakiet Pythona;
`tests/conftest.py` dodaje katalog główny projektu (rodzic `viewer/`) do
`sys.path`, więc `from tools import easyreader` działa bez instalacji.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from tools import easyreader

from .epub_builder import build_epub3


def configure_private_roots(monkeypatch, tmp_path: Path) -> None:
    """Izoluje CLI od prawdziwego katalogu projektu na czas testu -
    własny BOOKS_ROOT, ACTIVE_FILE i DEFAULT_PROFILE w katalogu tymczasowym."""
    books_root = tmp_path / "ksiazki_robocze"
    active_file = tmp_path / "AKTYWNA_KSIAZKA.txt"
    profile = tmp_path / "profil_czytania.md"
    profile.write_text("# Profil testowy\n", encoding="utf-8")
    monkeypatch.setattr(easyreader, "BOOKS_ROOT", books_root)
    monkeypatch.setattr(easyreader, "ACTIVE_FILE", active_file)
    monkeypatch.setattr(easyreader, "DEFAULT_PROFILE", profile)


def build_large_epub(dest: Path, *, num_paragraphs: int) -> Path:
    """Buduje EPUB z JEDNYM dokumentem spine zawierającym bardzo dużo
    akapitów - do testowania zachowania `command_next` na dużych książkach
    (patrz oryginalne zgłoszenie: "w dokumencie pozostało ponad 3000 bloków")."""
    container = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>"""
    opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Duza ksiazka testowa</dc:title>
    <dc:identifier id="bookid">urn:uuid:big-test</dc:identifier>
  </metadata>
  <manifest>
    <item id="chap1" href="chap1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="chap1"/></spine>
</package>"""
    paragraphs = "".join(f"<p>Akapit numer {i} z jakąś treścią testową do przetworzenia.</p>" for i in range(num_paragraphs))
    chapter = f"<html><head><title>Duza</title></head><body>{paragraphs}</body></html>"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", container)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/chap1.xhtml", chapter)
    return dest


# ---------------------------------------------------------------------- #
# Podstawowy cykl: init -> next -> apply.
# ---------------------------------------------------------------------- #

def test_init_creates_expected_book_directory_structure(monkeypatch, tmp_path: Path):
    configure_private_roots(monkeypatch, tmp_path)
    source = build_epub3(tmp_path / "moja_ksiazka.epub")

    easyreader.command_init(SimpleNamespace(source=str(source), name="test", force=False))

    book_dir = easyreader.BOOKS_ROOT / "test"
    state = easyreader.load_state(book_dir)
    assert state["format"] == 1
    assert Path(state["working_epub"]).is_file()
    assert Path(state["source_epub"]).is_file()
    assert state["cursor"] == {"section": 0, "raw_block": 0}
    assert easyreader.ACTIVE_FILE.read_text(encoding="utf-8").strip() == str(book_dir)


def test_next_then_apply_embeds_annotation_in_working_copy(monkeypatch, tmp_path: Path):
    configure_private_roots(monkeypatch, tmp_path)
    source = build_epub3(tmp_path / "book.epub")
    easyreader.command_init(SimpleNamespace(source=str(source), name="test", force=False))
    book_dir = easyreader.BOOKS_ROOT / "test"

    easyreader.command_next(SimpleNamespace(book=str(book_dir), chars=250))
    state = easyreader.load_state(book_dir)
    assert state["pending"] is not None
    fragment_id = state["pending"]["id"]

    proposal_path = book_dir / "temp" / "opracowanie.json"
    proposal = json.loads(proposal_path.read_text(encoding="utf-8"))
    proposal["prosty_jezyk"] = "Prostym językiem: to jest testowe wyjaśnienie."
    easyreader.save_json(proposal_path, proposal)

    easyreader.command_apply(SimpleNamespace(book=str(book_dir), data=None))

    state = easyreader.load_state(book_dir)
    assert state["pending"] is None
    assert state["applied"] == 1
    assert state["history"][-1] == {"id": fragment_id, "status": "applied", "section": "OEBPS/chap1.xhtml"}

    working = Path(state["working_epub"])
    with zipfile.ZipFile(working) as archive:
        chapter = archive.read("OEBPS/chap1.xhtml").decode("utf-8")
    assert "Prostym językiem: to jest testowe wyjaśnienie." in chapter
    assert f'data-easyreader-id="{fragment_id}"' in chapter

    # Kopia bezpieczeństwa sprzed zmiany istnieje.
    backup = book_dir / "backups" / f"{fragment_id}_przed_zmiana.epub"
    assert backup.is_file()


# ---------------------------------------------------------------------- #
# Punkt 7 (pierwotne zgłoszenie): `next` na dużej książce z wysokim
# kursorem NIE powinien fałszywie zgłaszać końca książki.
# ---------------------------------------------------------------------- #

def test_next_finds_fragment_deep_into_a_large_document(monkeypatch, tmp_path: Path):
    """Regres dla zgłoszonego problemu: kursor `raw_block: 495` w dużym
    dokumencie (>3000 bloków) błędnie kończył się komunikatem "Koniec
    książki". Symulujemy dokładnie tę sytuację: duży dokument, kursor
    daleko w środku, wywołanie przez tę samą funkcję co prawdziwe CLI."""
    configure_private_roots(monkeypatch, tmp_path)
    source = build_large_epub(tmp_path / "duza.epub", num_paragraphs=3500)
    easyreader.command_init(SimpleNamespace(source=str(source), name="duza", force=False))
    book_dir = easyreader.BOOKS_ROOT / "duza"

    # Ustawiamy kursor tak, jak w zgłoszeniu: daleko w dokumencie, ale z
    # tysiącami bloków wciąż przed nim.
    state = easyreader.load_state(book_dir)
    state["cursor"] = {"section": 0, "raw_block": 495}
    easyreader.save_json(book_dir / "postep.json", state)

    easyreader.command_next(SimpleNamespace(book=str(book_dir), chars=1600))

    state = easyreader.load_state(book_dir)
    assert state["pending"] is not None, "Powinien zostac znaleziony kolejny fragment, nie koniec ksiazki"
    assert state["pending"]["blocks"][0]["raw_index"] >= 495


def test_next_reports_end_of_book_with_diagnostics(monkeypatch, tmp_path: Path, capsys):
    """Gdy kursor faktycznie wskazuje poza koniec książki, `next` powinien
    jasno zgłosić koniec KSIĄŻKI wraz z diagnostyką (ścieżka książki,
    źródłowy EPUB, liczba bloków, cursor, liczba kandydatów) - żeby dało się
    natychmiast odróżnić prawdziwy koniec od pomyłki z niewłaściwą książką."""
    configure_private_roots(monkeypatch, tmp_path)
    source = build_epub3(tmp_path / "book.epub")
    easyreader.command_init(SimpleNamespace(source=str(source), name="test", force=False))
    book_dir = easyreader.BOOKS_ROOT / "test"

    state = easyreader.load_state(book_dir)
    # Kursor daleko poza końcem jedynego dokumentu spine.
    state["cursor"] = {"section": len(state["spine"]), "raw_block": 0}
    easyreader.save_json(book_dir / "postep.json", state)

    easyreader.command_next(SimpleNamespace(book=str(book_dir), chars=1600))

    output = capsys.readouterr().out
    assert "Koniec książki" in output
    assert str(book_dir) in output
    assert "Źródłowy EPUB" in output
    assert "Liczba dokumentów w spine" in output
    assert "Kursor przed przeszukaniem" in output
    assert "Kandydatów znalezionych od kursora: 0" in output


def test_next_end_of_book_warns_when_using_stale_active_book(monkeypatch, tmp_path: Path, capsys):
    """Odtwarza rdzeń zgłoszonego problemu: użytkownik NIE podaje książki
    jawnie (jak przy zwykłym `easyreader.py next`), więc CLI korzysta z
    AKTYWNA_KSIAZKA.txt - jeśli to WYCZERPANA/inna książka, diagnostyka
    musi to jasno pokazać, zamiast pozostawić wrażenie "koniec mojej
    głównej książki"."""
    configure_private_roots(monkeypatch, tmp_path)

    # Książka A: mała, szybko się "kończy".
    source_a = build_epub3(tmp_path / "a.epub")
    easyreader.command_init(SimpleNamespace(source=str(source_a), name="a", force=False))
    book_dir_a = easyreader.BOOKS_ROOT / "a"
    state_a = easyreader.load_state(book_dir_a)
    state_a["cursor"] = {"section": len(state_a["spine"]), "raw_block": 0}
    easyreader.save_json(book_dir_a / "postep.json", state_a)

    # Książka B: duża, z mnóstwem bloków wciąż do przetworzenia.
    source_b = build_large_epub(tmp_path / "b.epub", num_paragraphs=3500)
    easyreader.command_init(SimpleNamespace(source=str(source_b), name="b", force=False))
    # `command_init` książki B automatycznie ustawia ją jako aktywną,
    # więc dla jasności testu jawnie przełączamy aktywną książkę na A.
    easyreader.command_activate(SimpleNamespace(book=str(book_dir_a)))

    # Wywołanie BEZ podania książki (`book=None`) - tak jak plain `next`.
    easyreader.command_next(SimpleNamespace(book=None, chars=1600))

    output = capsys.readouterr().out
    assert "Koniec książki" in output
    assert "AKTYWNA_KSIAZKA" in output or str(easyreader.ACTIVE_FILE) in output
    assert str(book_dir_a) in output


# ---------------------------------------------------------------------- #
# Punkt 7: ponowne uruchomienie programu (nowy proces) nie powinno gubić
# ani duplikować oczekującego fragmentu.
# ---------------------------------------------------------------------- #

# ---------------------------------------------------------------------- #
# Nowy log z 2026-08-12: to samo wywołanie na TYM SAMYM, niezmienionym
# stanie i pliku bywało niedeterministyczne - raz fałszywy koniec książki,
# raz poprawny fragment. `command_next` teraz ponawia skanowanie i nie
# uznaje końca książki za potwierdzony po pojedynczej próbie z błędami.
# ---------------------------------------------------------------------- #

def test_next_recovers_from_a_transient_read_error_on_retry(monkeypatch, tmp_path: Path, capsys):
    """Symuluje dokładnie zgłoszony wzorzec: pierwsza próba odczytu kończy
    się błędem (typowe dla np. chwilowej blokady pliku albo chmurowej
    synchronizacji na żądanie), druga - na TYM SAMYM stanie i pliku - działa
    poprawnie. `next` powinno samo ponowić próbę i znaleźć fragment, zamiast
    fałszywie zgłosić koniec książki."""
    configure_private_roots(monkeypatch, tmp_path)
    source = build_epub3(tmp_path / "book.epub")
    easyreader.command_init(SimpleNamespace(source=str(source), name="test", force=False))
    book_dir = easyreader.BOOKS_ROOT / "test"

    real_scan = easyreader._scan_for_next_fragment
    call_count = {"n": 0}

    def flaky_scan(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            result = easyreader._ScanResult()
            result.errors.append(("OEBPS/chap1.xhtml", OSError("symulowany przejściowy błąd odczytu")))
            return result
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(easyreader, "_scan_for_next_fragment", flaky_scan)

    easyreader.command_next(SimpleNamespace(book=str(book_dir), chars=250))

    assert call_count["n"] >= 2, "Powinna zostac wykonana ponowna proba po bledzie"
    state = easyreader.load_state(book_dir)
    assert state["pending"] is not None, "Fragment powinien zostac znaleziony po ponowieniu"


def test_next_raises_uncertain_end_when_errors_persist_across_all_attempts(monkeypatch, tmp_path: Path):
    """Gdy błędy odczytu utrzymują się we WSZYSTKICH próbach, `next` nie
    wolno mylnie zgłosić potwierdzonego końca książki - musi zasygnalizować
    NIEPEWNOŚĆ (osobny wyjątek/kod wyjścia), żeby wywołujący mógł to
    odróżnić od prawdziwego, potwierdzonego końca."""
    configure_private_roots(monkeypatch, tmp_path)
    source = build_epub3(tmp_path / "book.epub")
    easyreader.command_init(SimpleNamespace(source=str(source), name="test", force=False))
    book_dir = easyreader.BOOKS_ROOT / "test"

    def always_erroring_scan(*args, **kwargs):
        result = easyreader._ScanResult()
        result.errors.append(("OEBPS/chap1.xhtml", OSError("trwały symulowany błąd odczytu")))
        return result

    monkeypatch.setattr(easyreader, "_scan_for_next_fragment", always_erroring_scan)

    with pytest.raises(easyreader.UncertainEndOfBook):
        easyreader.command_next(SimpleNamespace(book=str(book_dir), chars=250))

    # Stan NIE został zmieniony - żaden fałszywy "koniec" nie został utrwalony.
    state = easyreader.load_state(book_dir)
    assert state["pending"] is None
    assert state["cursor"] == {"section": 0, "raw_block": 0}


def test_cli_subprocess_exits_with_distinct_code_for_uncertain_end(tmp_path: Path):
    """Test na poziomie prawdziwego procesu: kod wyjścia 3 (nie 0, nie 1) dla
    niepewnego wyniku - patrz zgłoszenie: "Przydałby się osobny status
    maszynowy, żeby wywołujący mógł odróżnić prawdziwy koniec od awarii"."""
    project_root = Path(easyreader.__file__).resolve().parents[1]
    script = project_root / "tools" / "easyreader.py"

    isolated_root = tmp_path / "isolated_project"
    isolated_tools = isolated_root / "tools"
    isolated_tools.mkdir(parents=True)
    (isolated_tools / "easyreader.py").write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
    (isolated_root / "profil_czytania.md").write_text("# Profil\n", encoding="utf-8")

    source = build_epub3(tmp_path / "book.epub")

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(isolated_tools / "easyreader.py"), *args],
            capture_output=True, text=True, timeout=30,
        )

    result = run("init", str(source), "--name", "cli_test")
    assert result.returncode == 0, result.stderr
    book_dir = isolated_root / "ksiazki_robocze" / "cli_test"

    # Uszkadzamy ŹRÓDŁOWY EPUB (na który wskazuje postep.json), symulując
    # trwały problem z odczytem - CLI powinno zwrócić kod 3, nie 0 ani 1.
    state = json.loads((book_dir / "postep.json").read_text(encoding="utf-8"))
    corrupt_epub = Path(state["source_epub"])
    corrupt_epub.write_bytes(b"to nie jest poprawne archiwum zip")

    result = run("next", str(book_dir))
    assert result.returncode == 3, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "NIEPEWNY" in result.stderr


def test_next_is_idempotent_across_simulated_restarts(monkeypatch, tmp_path: Path, capsys):
    configure_private_roots(monkeypatch, tmp_path)
    source = build_epub3(tmp_path / "book.epub")
    easyreader.command_init(SimpleNamespace(source=str(source), name="test", force=False))
    book_dir = easyreader.BOOKS_ROOT / "test"

    easyreader.command_next(SimpleNamespace(book=str(book_dir), chars=250))
    state_after_first = easyreader.load_state(book_dir)
    fragment_id_first = state_after_first["pending"]["id"]

    # Symulacja restartu: NOWE odczytanie stanu z dysku (nowy "proces" po
    # prostu wczytuje ten sam plik postep.json od nowa) i ponowne wywołanie
    # `next` - nie powinno nadpisać ani zduplikować oczekującego fragmentu.
    capsys.readouterr()
    easyreader.command_next(SimpleNamespace(book=str(book_dir), chars=250))
    output = capsys.readouterr().out
    assert "czeka już na opracowanie" in output

    state_after_second = easyreader.load_state(book_dir)
    assert state_after_second["pending"]["id"] == fragment_id_first
    assert state_after_second["applied"] == 0
    assert state_after_second["skipped"] == 0


# ---------------------------------------------------------------------- #
# `resolve_book` / aktywna książka.
# ---------------------------------------------------------------------- #

def test_resolve_book_uses_explicit_argument_over_active_file(monkeypatch, tmp_path: Path):
    configure_private_roots(monkeypatch, tmp_path)
    source_a = build_epub3(tmp_path / "a.epub")
    easyreader.command_init(SimpleNamespace(source=str(source_a), name="a", force=False))
    book_dir_a = easyreader.BOOKS_ROOT / "a"

    source_b = build_epub3(tmp_path / "b.epub")
    easyreader.command_init(SimpleNamespace(source=str(source_b), name="b", force=False))
    book_dir_b = easyreader.BOOKS_ROOT / "b"  # ta jest teraz aktywna (ostatni init)

    resolved = easyreader.resolve_book(str(book_dir_a))
    assert resolved == book_dir_a.resolve()
    assert resolved != book_dir_b.resolve()


def test_resolve_book_fails_clearly_without_active_book(monkeypatch, tmp_path: Path):
    configure_private_roots(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="aktywnej książki"):
        easyreader.resolve_book(None)


# ---------------------------------------------------------------------- #
# Punkt 9: pełny test integracyjny CLI z rzeczywistym układem projektu -
# wywołanie `python tools/easyreader.py ...` jako osobny proces, żeby
# sprawdzić też samo parsowanie argumentów (argparse), a nie tylko funkcje.
# ---------------------------------------------------------------------- #

def test_cli_end_to_end_as_real_subprocess(tmp_path: Path):
    project_root = Path(easyreader.__file__).resolve().parents[1]
    script = project_root / "tools" / "easyreader.py"
    assert script.is_file()

    source = build_epub3(tmp_path / "book.epub")

    # Uruchamiamy prawdziwy interpreter na kopii prawdziwego pliku CLI, we
    # WŁASNYM, izolowanym katalogu projektu (PROJECT_ROOT liczony jest
    # względem __file__), żeby nie dotykać repozytorium ani realnych danych.
    isolated_root = tmp_path / "isolated_project"
    isolated_tools = isolated_root / "tools"
    isolated_tools.mkdir(parents=True)
    (isolated_tools / "easyreader.py").write_text(script.read_text(encoding="utf-8"), encoding="utf-8")
    (isolated_root / "profil_czytania.md").write_text("# Profil\n", encoding="utf-8")

    def run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(isolated_tools / "easyreader.py"), *args],
            capture_output=True, text=True, timeout=30,
        )

    result = run("init", str(source), "--name", "cli_test")
    assert result.returncode == 0, result.stderr
    assert "Katalog książki" in result.stdout

    book_dir = isolated_root / "ksiazki_robocze" / "cli_test"
    assert book_dir.is_dir()

    result = run("next", str(book_dir), "--chars", "250")
    assert result.returncode == 0, result.stderr

    result = run("status", str(book_dir))
    assert result.returncode == 0, result.stderr
    status = json.loads(result.stdout)
    assert status["pending"] is not None
    assert status["resolved_from"] == "argument"

    # Drugie wywołanie "next" (symulacja ponownego uruchomienia CLI jako
    # nowego procesu) na oczekującym fragmencie - nie powinno się wywrócić
    # ani utworzyć nowego fragmentu.
    result = run("next", str(book_dir))
    assert result.returncode == 0, result.stderr
    assert "czeka już na opracowanie" in result.stdout
