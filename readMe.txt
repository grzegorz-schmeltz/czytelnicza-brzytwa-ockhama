CZYTELNICZA BRZYTWA OCKHAMA
CHATGPT / CODEX JAKO SKRYBA I KOMENTATOR
============================================================

POLSKI
------------------------------------------------------------

1. Na czym polega ten sposób pracy?

Program może współpracować z asystentem AI, takim jak ChatGPT lub Codex.
Asystent pełni rolę skryby i komentatora: czyta niewielki fragment, pomaga go
zrozumieć i przygotowuje propozycję opracowania. Nie jest autorem książki i nie
powinien przypisywać autorowi własnych zdań.

Najwygodniejszy jest lokalny Codex uruchomiony w katalogu projektu, ponieważ
może odczytywać i zapisywać pliki robocze. Zwykły ChatGPT działający w
przeglądarce może przygotować komentarz, ale bez lokalnego narzędzia nie zapisze
sam pliku na komputerze użytkownika.

Warstwa AI nie jest wbudowana w czytnik i projekt nie udostępnia wspólnego
klucza API. Użytkownik sam wybiera usługę AI.

2. Zalecany przebieg pracy

1) Dodaj książkę zgodnie z głównym README.md.
2) Poproś lokalnego Codexa: „Następny fragment”.
3) Narzędzie przygotuje:
   - temp/fragment_biezacy.txt — fragment przeznaczony do omówienia;
   - temp/opracowanie.json — szablon odpowiedzi.
4) Poproś np.:
   „Wyjaśnij fragment prostym językiem. Zachowaj styl autora i zapisz
   propozycję w temp/opracowanie.json. Nie stosuj jej jeszcze.”
5) Przeczytaj propozycję. Możesz poprosić o poprawienie, skrócenie albo
   usunięcie komentarza AI.
6) Dopiero gdy akceptujesz wynik, napisz: „Zastosuj”.
7) Jeśli nie chcesz opracowywać fragmentu, napisz: „Pomiń”.

Asystent powinien zawsze czekać na akceptację przed zastosowaniem opracowania.

3. Format temp/opracowanie.json

Pole fragment_id jest tworzone przez program i nie wolno go zmieniać.
Niewykorzystywane pola mogą pozostać puste.

Przykład:

{
  "fragment_id": "fragment-0001",
  "modernizacja": "Wierne uwspółcześnienie lub przekład.",
  "prosty_jezyk": "Krótkie wyjaśnienie prostymi słowami.",
  "objasnienia": [
    {
      "haslo": "trudne pojęcie",
      "tresc": "Zwięzłe objaśnienie pojęcia."
    }
  ],
  "komentarz_ai": "Oddzielony i wyraźnie oznaczony komentarz AI.",
  "notatka_czytelnika": "Prywatna notatka użytkownika."
}

4. Zasady skryby-komentatora

- Wyraźnie oddzielaj tekst autora, uwspółcześnienie, proste wyjaśnienie,
  objaśnienia pojęć, komentarz AI i notatkę czytelnika.
- Nie zmieniaj poglądów autora po cichu i nie przypisuj mu wypowiedzi AI.
- Nie udawaj pewności. Wątpliwe informacje oznacz jako wymagające sprawdzenia.
- Używaj prostego języka, krótkich zdań i małych porcji tekstu.
- Nie przetwarzaj całej książki naraz.
- Nie zapisuj do JSON-a pełnej treści książki. JSON ma zawierać opracowanie,
  identyfikator i dane potrzebne programowi.
- Nie rozpowszechniaj chronionej książki ani pełnej przerobionej wersji bez
  upewnienia się, że pozwalają na to prawa autorskie.

5. Bezpieczeństwo książki

Źródłowy EPUB nie powinien być modyfikowany. Obecne narzędzie CLI pracuje na
oddzielnej kopii roboczej EPUB-a, a viewer może niezależnie nakładać adnotacje
z pliku .easyreader w katalogu tymczasowym. Jest to wersja alpha; te dwa
przepływy nie są jeszcze w pełni połączone.

Więcej informacji:
- README.md
- docs/INSTRUKCJE_DLA_CHATGPT.md
- AGENTS.md
- PRIVACY_AND_COPYRIGHT.md


ENGLISH
------------------------------------------------------------

1. What is this workflow?

The reader can be used together with an AI assistant such as ChatGPT or Codex.
The assistant acts as a scribe and commentator: it reads a small passage,
helps the reader understand it, and prepares a proposed annotation. It is not
the author of the book and must not attribute its own words to the author.

Local Codex running in the project directory is the most convenient option,
because it can read and write working files. Regular ChatGPT in a web browser
can prepare an explanation, but without a local tool it cannot save a file on
the user's computer by itself.

AI is not embedded in the reader, and this project does not provide a shared
API key. The user chooses the AI service.

2. Recommended workflow

1) Add a book according to the main README.md.
2) Ask local Codex: "Next passage" (or in Polish: "Następny fragment").
3) The tool prepares:
   - temp/fragment_biezacy.txt — the passage to discuss;
   - temp/opracowanie.json — the response template.
4) Ask, for example:
   "Explain this passage in plain language. Preserve the author's style and
   save the proposal in temp/opracowanie.json. Do not apply it yet."
5) Review the proposal. Ask for corrections or a shorter explanation if needed.
6) Only after approval, say: "Apply" (or "Zastosuj").
7) To leave the passage unchanged, say: "Skip" (or "Pomiń").

The assistant should always wait for approval before applying an annotation.

3. temp/opracowanie.json format

The program creates fragment_id; it must not be changed. Unused fields may be
left empty. The JSON example in the Polish section uses these fields:

- fragment_id — identifier created by the program;
- modernizacja — faithful translation or modernization;
- prosty_jezyk — plain-language explanation;
- objasnienia — glossary entries with haslo (term) and tresc (explanation);
- komentarz_ai — clearly labelled AI commentary;
- notatka_czytelnika — the reader's private note.

4. Rules for the scribe-commentator

- Clearly separate the author's text, modernization, plain explanation,
  glossary, AI commentary, and reader notes.
- Never silently correct the author's views or attribute AI text to the author.
- Do not pretend to be certain. Mark claims that need verification.
- Use plain language, short sentences, and small passages.
- Do not process an entire book at once.
- Do not store the full book text in JSON. Store only the annotation,
  identifier, and data required by the program.
- Do not distribute copyrighted books or complete transformed editions unless
  copyright permissions allow it.

5. Book safety

The source EPUB should never be modified. The current CLI works on a separate
working EPUB copy, while the viewer can independently apply .easyreader
annotations in a temporary directory. This is alpha software, and these two
workflows are not fully integrated yet.

For more information, see:
- README.md
- docs/INSTRUKCJE_DLA_CHATGPT.md
- AGENTS.md
- PRIVACY_AND_COPYRIGHT.md
