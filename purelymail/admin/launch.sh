#!/bin/bash
# Starts BGYHub Mailbox Admin (if not already running) and opens it in the browser.
# Used by "Start Mailbox Admin.command" and by the Desktop app it creates.
# Pass --install-app to (re)create "BGYHub Mailbox Admin.app" on the Desktop.
set -uo pipefail

# Apps started from Finder get a minimal PATH; add the usual Python locations.
export PATH="/opt/homebrew/bin:/usr/local/bin:/Library/Frameworks/Python.framework/Versions/Current/bin:$PATH"

DIR="$(cd "$(dirname "$0")/.." && pwd)"   # the purelymail/ folder
PORT="${ADMIN_PORT:-8787}"
URL="http://127.0.0.1:$PORT"
LOG="$DIR/output/admin-server.log"
APP="$HOME/Desktop/BGYHub Mailbox Admin.app"
VENV="$DIR/.venv"

mkdir -p "$DIR/output" && chmod 700 "$DIR/output"

notify() { osascript -e "display notification \"$1\" with title \"BGYHub Mailbox Admin\"" >/dev/null 2>&1 || true; }
fail() {
  echo "ERROR: $1" >&2
  osascript -e "display alert \"BGYHub Mailbox Admin\" message \"$1\" as critical" >/dev/null 2>&1 || true
  exit 1
}
running() { curl -fsS --max-time 2 "$URL/healthz" 2>/dev/null | grep -q "bgyhub-mailbox-admin"; }

if [ "${1:-}" = "--install-app" ] && [ ! -d "$APP" ]; then
  # A tiny AppleScript app that runs this script without opening Terminal.
  osacompile -o "$APP" -e "do shell script quoted form of \"$DIR/admin/launch.sh\"" >/dev/null 2>&1 \
    && echo "Created $APP" || echo "Could not create the Desktop app (you can keep using this .command file)."
fi

if running; then
  open "$URL"
  exit 0
fi
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  fail "Port $PORT is already used by another program. Quit it, or set ADMIN_PORT in your environment."
fi

command -v python3 >/dev/null 2>&1 || fail "Python 3 is not installed. Install it from python.org, then try again."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || fail "Python 3.9 or newer is required. Install the latest Python from python.org."

if [ ! -x "$VENV/bin/python" ] || ! "$VENV/bin/python" -c 'import flask, openpyxl, cryptography' >/dev/null 2>&1; then
  notify "First start: installing components, this takes about a minute…"
  echo "Installing components (first start only)…"
  python3 -m venv "$VENV" >>"$LOG" 2>&1 || fail "Could not create the Python environment. See purelymail/output/admin-server.log"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip >>"$LOG" 2>&1 || true
  "$VENV/bin/python" -m pip install --quiet -r "$DIR/requirements.txt" >>"$LOG" 2>&1 \
    || fail "Could not install components (check your internet connection). See purelymail/output/admin-server.log"
fi

cd "$DIR" || fail "Cannot open $DIR"
nohup "$VENV/bin/python" -m admin.app >>"$LOG" 2>&1 &

for _ in $(seq 1 60); do
  if running; then
    open "$URL"
    exit 0
  fi
  sleep 0.5
done
fail "The app did not start. See purelymail/output/admin-server.log"
