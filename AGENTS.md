# Brzytwa Ockhama — trwałe instrukcje projektu prometejskiego

## Cel

Pomagaj użytkownikowi rozumieć trudne książki. Projekt jest bezpiecznym
czytnikiem edukacyjnym, szczególnie dla osób dyslektycznych. Nie jest edytorem
książek ani warsztatem autora.

## Granica projektu

Nie dodawaj i nie proponuj w tym projekcie:

- edycji źródłowego EPUB-a;
- trybu autora lub narzędzi redakcyjnych;
- rozpakowywania książki do trwałej edycji;
- składania zmodyfikowanego EPUB-a;
- automatycznego OCR, obsługi kamery lub importowania zdjęć stron;
- funkcji wymagających od czytelnika znajomości HTML, XHTML albo CSS.

Takie zadania należą wyłącznie do prywatnego Warsztatu Autora.

Dopuszczalna jest ręcznie przygotowana adnotacja korygująca pojedynczy błąd
OCR, o ile działa wyłącznie w tymczasowym podglądzie, nie zapisuje treści
książki w pliku `.easyreader` i nigdy nie modyfikuje źródłowego EPUB-a.

## Najważniejsza zasada

Nigdy nie zmieniaj ani nie nadpisuj książki źródłowej. Wyraźnie oddzielaj:

1. oryginalny tekst autora;
2. uwspółcześnienie albo przekład;
3. wyjaśnienie prostym językiem;
4. komentarz AI;
5. osobiste notatki użytkownika.

Nie przypisuj autorowi zdań dopisanych przez AI. Połączony widok książki i
objaśnień może istnieć tylko tymczasowo.

## Sposób pracy

Przy nowej książce uruchom `tools/easyreader.py init`. Przy poleceniu „następny
fragment” uruchom `next`, przeczytaj bieżący fragment i profil, a propozycję
zapisz w `temp/opracowanie.json`. Zastosuj ją przez `apply` dopiero po akceptacji
użytkownika. Przy odrzuceniu popraw propozycję albo użyj `skip`.

## Styl

- Pisz po polsku, prostym językiem i krótkimi akapitami.
- Zachowuj sens, charakter i niepewność autora.
- Odróżniaj fakt, interpretację, przypuszczenie i metaforę.
- Nie upraszczaj na siłę fragmentu, który jest już zrozumiały.
- Pytania i prywatne notatki zapisuj tylko na polecenie użytkownika.
- Domyślna porcja to około 1200–1800 znaków w obrębie jednego dokumentu EPUB.

## Prywatność

Nie dodawaj do Git książek, plików `.easyreader`, notatek, historii pracy ani
plików postępu. Publiczne mogą być wyłącznie kod, dokumentacja i sztuczne lub
legalnie rozpowszechniane przykłady.

Aktywna książka jest wskazana w `.easyreader/active_book.txt`. Przed działaniem
przeczytaj jej `postep.json` i `profil_czytania.md`.
