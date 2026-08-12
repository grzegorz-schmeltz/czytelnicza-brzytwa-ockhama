# Brzytwa Ockhama — Czytnik Prometejski

Bezpłatne, otwarte narzędzie pomagające czytać książki napisane językiem
archaicznym, specjalistycznym, obcojęzycznym albo niepotrzebnie trudnym.
Projekt powstał szczególnie z myślą o osobach dyslektycznych i czytelnikach,
którym potrzebne są krótsze porcje tekstu oraz proste objaśnienia.

Program nie zastępuje książki streszczeniem. Zachowuje tekst autora, a pod
wybranymi fragmentami może wyświetlać wyraźnie oznaczone:

- przekłady lub uwspółcześnienia;
- objaśnienia prostym językiem;
- wyjaśnienia pojęć;
- komentarze AI;
- prywatne notatki czytelnika.

## Bezpieczny zakres

To jest wyłącznie projekt czytelniczy i edukacyjny. Nie zawiera edytora EPUB,
trybu autora, OCR, obsługi kamery ani narzędzi do składania książek.

Oryginalny EPUB pozostaje nietknięty i jest otwierany tylko do odczytu.
Opracowania trafiają do niewielkiego pliku `*.easyreader`, który nie zawiera
pełnej treści książki. Połączony podgląd powstaje wyłącznie w katalogu
tymczasowym i jest usuwany po zamknięciu programu.

## Stan projektu

To wczesna wersja do lokalnych testów na Windowsie. Obsługuje EPUB i TXT.
PDF może zostać przekształcony do prywatnego EPUB-a za pomocą Calibre.
Warstwa AI nie jest zaszyta w aplikacji i nie wymaga wspólnego klucza API.

## Instalacja

Wymagany jest Python 3.11 lub nowszy. Po pobraniu projektu uruchom:

```bat
scripts\setup_windows.bat
```

Dodanie książki:

```bat
scripts\easyreader.bat init "C:\sciezka\do\ksiazki.epub"
```

Otwarcie ostatniej książki:

```bat
scripts\open_last_book.bat
```

## Praca z asystentem AI

Można używać zwykłych poleceń:

- „Następny fragment”.
- „Wyjaśnij ten fragment prościej”.
- „Uwspółcześnij język, ale zachowaj styl”.
- „Zastosuj”.
- „Pomiń”.

Asystent zapisuje propozycję w `temp/opracowanie.json`, a zaakceptowane
opracowanie w osobnym pliku `.easyreader`. Nigdy nie zmienia źródłowego EPUB-a.

## Funkcje czytnika

- spis treści i nawigacja między rozdziałami;
- powiększanie tekstu oraz jasny i ciemny motyw;
- zapamiętywanie rozdziału i dokładnego miejsca czytania;
- ręczny znacznik czytania oraz przycisk powrotu do ostatniego znacznika;
- automatyczne zachowanie znacznika przed przeładowaniem objaśnień;
- bezpieczne przeładowanie notatek;
- eksport samych notatek do oddzielnego EPUB-a, bez tekstu książki.

## Prywatność

Repozytorium nie zawiera książek. Katalog `books/` oraz pliki książek, notatek
i postępu są ignorowane przez Git. Użytkownik odpowiada za prawo do
przetwarzania używanego tekstu. Szczegóły zawiera
[`PRIVACY_AND_COPYRIGHT.md`](PRIVACY_AND_COPYRIGHT.md).

## Testy

```bat
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest viewer\tests -q
```

## Licencja

Kod jest dostępny na warunkach GNU General Public License v3.0. Społeczny i
edukacyjny cel projektu opisuje [`PUBLIC_BENEFIT.md`](PUBLIC_BENEFIT.md).

Copyright © 2026 Grzegorz Schmeltz.
