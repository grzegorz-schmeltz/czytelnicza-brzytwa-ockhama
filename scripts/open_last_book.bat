@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "ROOT=%~dp0.."
set "ACTIVE=%ROOT%\.easyreader\active_book.txt"

if not exist "%ACTIVE%" (
  echo [BLAD] Nie ma aktywnej ksiazki. Najpierw uzyj polecenia init.
  pause
  exit /b 1
)

set /p "BOOK_DIR="<"%ACTIVE%"
set "LATEST_EPUB="
set "NOTES_FILE="
for /f "usebackq tokens=1,* delims=|" %%F in (`powershell -NoProfile -Command "$s=Get-Content -LiteralPath '%BOOK_DIR%\postep.json' -Raw ^| ConvertFrom-Json; Write-Output ($s.source_epub + '|' + $s.annotations_file)"`) do (
  set "LATEST_EPUB=%%F"
  set "NOTES_FILE=%%G"
)

if not defined LATEST_EPUB (
  echo [BLAD] Nie znaleziono pliku roboczego aktywnej ksiazki.
  pause
  exit /b 1
)

call "%ROOT%\scripts\open_reader.bat" "%LATEST_EPUB%" "%NOTES_FILE%"
exit /b %ERRORLEVEL%
