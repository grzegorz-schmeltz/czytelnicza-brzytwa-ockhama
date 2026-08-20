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
trybu autora, silnika OCR, obsługi kamery ani narzędzi do składania książek.
Może wyświetlać ręcznie przygotowaną korektę drobnego błędu OCR jako osobną
adnotację, ale nie zmienia w tym celu książki źródłowej.

Oryginalny EPUB pozostaje nietknięty i jest otwierany tylko do odczytu.
Viewer potrafi nakładać opracowania z niewielkiego pliku `*.easyreader`, który
nie zawiera pełnej treści książki. Połączony podgląd powstaje wyłącznie w
katalogu tymczasowym i jest usuwany po zamknięciu programu.

## Stan projektu

To eksperymentalna wersja **alpha** do lokalnych testów na Windowsie. Interfejs
i format danych mogą się jeszcze zmieniać. Program obsługuje EPUB i TXT.
PDF może zostać przekształcony do prywatnego EPUB-a za pomocą Calibre.
Warstwa AI nie jest zaszyta w aplikacji i nie wymaga wspólnego klucza API.

### Znane ograniczenie wersji alpha

Viewer i format zewnętrznych notatek `.easyreader` są już obsługiwane, ale
narzędzie wiersza poleceń `tools/easyreader.py` nadal używa starszego modelu:
tworzy oddzielną kopię roboczą EPUB-a i pracuje na niej. Nigdy nie nadpisuje
źródłowej książki, jednak oba przepływy nie są jeszcze w pełni połączone.

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

Asystent zapisuje propozycję w `temp/opracowanie.json`. Obecny przepływ CLI
stosuje zaakceptowane opracowanie do oddzielnej kopii roboczej EPUB-a; viewer
może niezależnie korzystać z pliku `.easyreader`. Żaden z tych przepływów nie
zmienia źródłowego EPUB-a.

Pełny, dwujęzyczny opis używania ChatGPT/Codexa jako skryby i komentatora,
wraz z przykładem pliku JSON, znajduje się w [`readMe.txt`](readMe.txt).

## Funkcje czytnika

- spis treści i nawigacja między rozdziałami;
- powiększanie tekstu oraz jasny i ciemny motyw;
- zapamiętywanie rozdziału i dokładnego miejsca czytania;
- ręczny znacznik czytania oraz przycisk powrotu do ostatniego znacznika;
- automatyczne zachowanie znacznika przed przeładowaniem objaśnień;
- bezpieczne przeładowanie notatek i pomijanie niepewnych dopasowań;
- kontrolowane ponowne powiązanie notatek z kopią bezpieczeństwa;
- ręczne korekty drobnych błędów OCR nakładane wyłącznie w podglądzie;
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
