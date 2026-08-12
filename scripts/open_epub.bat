@echo off
setlocal
set "ROOT=%~dp0.."
set "PYTHON_EXE=%ROOT%\.venv\Scripts\python.exe"

if "%~1"=="" (
  echo [BLAD] Nie podano pliku EPUB.
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

call "%ROOT%\scripts\open_reader.bat" "%~1" "%~2"
exit /b %ERRORLEVEL%
