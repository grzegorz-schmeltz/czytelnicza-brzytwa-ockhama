@echo off
setlocal
set "ROOT=%~dp0.."
set "PYTHON_EXE=%ROOT%\.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
  echo [BLAD] Najpierw uruchom scripts\setup_windows.bat
  pause
  exit /b 1
)

"%PYTHON_EXE%" "%ROOT%\tools\easyreader.py" %*
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" pause
exit /b %RESULT%
