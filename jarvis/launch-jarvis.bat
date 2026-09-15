@echo off
rem JARVIS launcher for Windows -- DOUBLE-CLICK THIS FILE.
rem
rem No terminal knowledge needed. It builds the demo graph on first run,
rem starts the server, and opens your browser to JARVIS.
rem To stop JARVIS: close this window, or press Ctrl-C then Y.

setlocal
cd /d "%~dp0"
if "%JARVIS_PORT%"=="" set JARVIS_PORT=8765

echo.
echo    J A R V I S
echo.

rem --- 1. Find Python 3 (prefer the "py" launcher, then "python"). ---
set "PYCMD="
py -3 --version >nul 2>&1 && set "PYCMD=py -3"
if not defined PYCMD (
  python --version >nul 2>&1 && set "PYCMD=python"
)
if not defined PYCMD (
  echo Python isn't installed on this PC.
  echo Opening the download page. Install Python 3.10 or newer, and IMPORTANT:
  echo tick "Add python.exe to PATH" in the installer. Then run this file again.
  start "" https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

rem --- 2. Build the demo graph once (skipped if it already exists). ---
if not exist "data\vault" (
  echo First run - building the demo graph...
  %PYCMD% data\generate.py
  if errorlevel 1 (
    echo.
    echo Couldn't build the demo data. See the message above.
    pause
    exit /b 1
  )
  echo.
)

rem --- 3. Open the browser ~2s after the server comes up. ---
start "" powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://localhost:%JARVIS_PORT%'"

echo Starting JARVIS at  http://localhost:%JARVIS_PORT%
echo Your browser will open in a moment.
echo.
echo Leave this window open while you use JARVIS.
echo To stop: close this window, or press Ctrl-C then Y.
echo.

rem --- 4. Run the server in the foreground so closing the window stops it. ---
%PYCMD% agent\main.py
set "CODE=%errorlevel%"

rem --- 5. If it fell over (usually: the port is in use), say so plainly. ---
if not "%CODE%"=="0" (
  echo.
  echo JARVIS stopped ^(exit %CODE%^).
  echo Most likely something else is already using port %JARVIS_PORT%.
  echo Fix: close the other app, or right-click this file ^> Edit and change
  echo 8765 near the top to another number ^(e.g. 8888^), then save and re-run.
  echo.
  pause
)
endlocal
