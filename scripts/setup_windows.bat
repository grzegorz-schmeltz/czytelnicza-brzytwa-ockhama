@echo off
setlocal
cd /d "%~dp0.."

set "PYTHON_CMD="
where py >nul 2>nul && set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD where python >nul 2>nul && set "PYTHON_CMD=python"

if not defined PYTHON_CMD (
  echo [BLAD] Nie znaleziono Pythona 3. Zainstaluj go z https://www.python.org/
  pause
  exit /b 1
)

%PYTHON_CMD% -m venv .venv
if errorlevel 1 goto :error

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :error
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo Instalacja zakonczona.
echo Tryb czytelnika: scripts\open_last_book.bat
pause
exit /b 0

:error
echo.
echo [BLAD] Instalacja nie zostala ukonczona.
pause
exit /b 1
