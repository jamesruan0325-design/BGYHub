#!/bin/bash
# Starts BGYHub Mailbox Admin (if not already running) and opens it in the browser.
# Used by "BGYHub Mailbox Admin.app" and "Start Mailbox Admin.command".
#
# Nothing is installed: all Python packages the app needs are bundled in
# purelymail/vendor (pure Python), so any Python 3.9+ on the Mac works,
# including the one from Apple's Command Line Tools (/usr/bin/python3).
# Pass --install-app to (re)create a Desktop shortcut app.
set -uo pipefail

# Apps started from Finder get a minimal PATH; add the usual Python locations.
export PATH="/Library/Frameworks/Python.framework/Versions/Current/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

DIR="$(cd "$(dirname "$0")/.." && pwd)"   # the code: purelymail/ (inside the .app when bundled)
# The data (.env, output/, admin/data) can live elsewhere; the bundled app sets this to ~/BGYHub/purelymail.
DATA="${BGYHUB_DATA_ROOT:-$DIR}"
export BGYHUB_DATA_ROOT="$DATA"
PORT="${ADMIN_PORT:-8787}"
URL="http://127.0.0.1:$PORT"
LOG="$DATA/output/admin-server.log"
APP="$HOME/Desktop/BGYHub Mailbox Admin.app"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$DIR/admin/__init__.py")"
[ -f "$DIR/BUILD" ] && VERSION="$VERSION ($(tr '\n' ' ' < "$DIR/BUILD"| sed 's/ $//'))"

mkdir -p "$DATA/output" && chmod 700 "$DATA/output"

log() { echo "$(date '+%F %T') [launcher] $*" >> "$LOG"; }
notify() { osascript -e "display notification \"$1\" with title \"BGYHub Mailbox Admin\"" >/dev/null 2>&1 || true; }
fail() {
  log "ERROR: $1"
  echo "ERROR: $1" >&2
  osascript -e "display alert \"BGYHub Mailbox Admin\" message \"$1\" as critical" >/dev/null 2>&1 || true
  exit 1
}
running() { curl -fsS --max-time 2 "$URL/healthz" 2>/dev/null | grep -q "bgyhub-mailbox-admin"; }

if [ "${1:-}" = "--install-app" ] && [ ! -d "$APP" ]; then
  osacompile -o "$APP" -e "do shell script quoted form of \"$DIR/admin/launch.sh\"" >/dev/null 2>&1 \
    && echo "Created $APP" || echo "Could not create the Desktop app (you can keep using this .command file)."
fi

if running; then
  open "$URL"
  exit 0
fi
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  fail "Port $PORT is already used by another program. Restart the Mac, then try again."
fi

log "starting version $VERSION (bundled packages, no install step); code: $DIR; data: $DATA; macOS $(sw_vers -productVersion 2>/dev/null || uname -r), $(uname -m)"

# Pick the first Python 3.9+ that can load the app with the bundled packages.
# Probing the app's own imports (not just the version) catches broken installs.
# BGYHUB_PYTHON (optional) puts a specific interpreter first; used by tests.
PROBE='import sys; assert sys.version_info >= (3, 9), sys.version; import admin, flask, openpyxl, sqlite3, ssl, hashlib, mailbox_core, tracking_xlsx'
PYTHON=""
for candidate in \
    ${BGYHUB_PYTHON:-} \
    /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3 \
    /usr/bin/python3; do
  [ -x "$candidate" ] || continue
  # /usr/bin/python3 is a stub until the Command Line Tools are installed; don't trigger their installer here.
  if [ "$candidate" = "/usr/bin/python3" ] && ! xcode-select -p >/dev/null 2>&1 && [ "$(uname)" = "Darwin" ]; then
    log "skip $candidate (Command Line Tools not installed)"
    continue
  fi
  if out="$(cd "$DIR" && "$candidate" -B -s -c "$PROBE" 2>&1)"; then
    PYTHON="$candidate"
    break
  fi
  log "skip $candidate: $(echo "$out" | tail -1)"
done
[ -n "$PYTHON" ] || fail "No usable Python 3.9+ was found. Install Python from python.org (or the Command Line Tools), then try again. Details: purelymail/output/admin-server.log"
log "using $PYTHON ($("$PYTHON" -c 'import sys; print(sys.version.split()[0])'))"

cd "$DIR" || fail "Cannot open $DIR"
# -s: ignore per-user site-packages, so only the bundled packages are used.
# -B: don't write .pyc files (the .app may be on a read-only location).
nohup "$PYTHON" -B -s -m admin.app >>"$LOG" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 60); do
  if running; then
    log "running (pid $SERVER_PID)"
    open "$URL"
    exit 0
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    fail "The app stopped while starting: $(grep -v '^\s*$' "$LOG" | tail -1 | tr -d '\"')"
  fi
  sleep 0.5
done
fail "The app did not start within 30 seconds. See purelymail/output/admin-server.log"
