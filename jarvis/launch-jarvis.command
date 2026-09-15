#!/bin/bash
# JARVIS launcher for macOS — DOUBLE-CLICK THIS FILE in Finder.
#
# No terminal knowledge needed. It builds the demo graph on first run,
# starts the server, and opens your browser to JARVIS.
# To stop JARVIS: close this window, or press Ctrl-C.

cd "$(dirname "$0")" || exit 1

PORT="${JARVIS_PORT:-8765}"

clear
echo "─────────────────────────────────"
echo "   J A R V I S"
echo "─────────────────────────────────"
echo

# 1. Is Python 3 installed? (macOS may not ship it.)
if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 isn't installed on this Mac."
  echo "Opening the download page — install it, then double-click this file again."
  open "https://www.python.org/downloads/" 2>/dev/null
  echo
  read -n 1 -s -r -p "Press any key to close this window."
  exit 1
fi

# 2. Build the demo graph once (skipped if it already exists).
if [ ! -d "data/vault" ]; then
  echo "First run — building the demo graph..."
  if ! python3 data/generate.py; then
    echo
    echo "Couldn't build the demo data. See the message above."
    read -n 1 -s -r -p "Press any key to close this window."
    exit 1
  fi
  echo
fi

# 3. Open the browser a moment after the server comes up.
( sleep 2; open "http://localhost:${PORT}" 2>/dev/null ) &

echo "Starting JARVIS at  http://localhost:${PORT}"
echo "Your browser will open in a moment."
echo
echo "Leave this window open while you use JARVIS."
echo "To stop: close this window, or press Ctrl-C."
echo "─────────────────────────────────"
echo

# 4. Run the server in the foreground so closing the window stops it.
JARVIS_PORT="$PORT" python3 agent/main.py
code=$?

# 5. If it fell over (usually: the port is already in use), say so plainly
#    instead of the window vanishing.
if [ "$code" -ne 0 ] && [ "$code" -ne 130 ]; then
  echo
  echo "JARVIS stopped unexpectedly (exit $code)."
  echo "Most likely something else is already using port ${PORT}."
  echo "Fix: quit the other app, or right-click this file > Open With > TextEdit"
  echo "and change 8765 near the top to another number (e.g. 8888)."
  echo
  read -n 1 -s -r -p "Press any key to close this window."
fi
