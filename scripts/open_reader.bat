@echo off
setlocal
set "ROOT=%~dp0.."
set "PYTHON_EXE=%ROOT%\.venv\Scripts\python.exe"

if "%~1"=="" (
  echo [BLAD] Nie podano oryginalnego pliku EPUB.
  pause
  exit /b 1
)
if not exist "%~1" (
  echo [BLAD] Nie znaleziono pliku: %~1
  pause
  exit /b 1
)
if not exist "%PYTHON_EXE%" (
  echo [BLAD] Najpierw uruchom scripts\setup_windows.bat
  pause
  exit /b 1
)

cd /d "%ROOT%\viewer"
if "%~2"=="" (
  "%PYTHON_EXE%" main.py "%~1"
) else (
  "%PYTHON_EXE%" main.py --annotations "%~2" "%~1"
)
exit /b %ERRORLEVEL%
