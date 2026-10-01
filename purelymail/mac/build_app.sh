#!/bin/bash
# Builds the self-contained "BGYHub Mailbox Admin v2.app" and a zip of it.
#   purelymail/mac/build_app.sh [output-dir]      (default: purelymail/mac/dist)
# The app bundles the admin code + vendor/ packages; it never contains .env,
# output/, admin/data or any other user data.
set -euo pipefail
MAC="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$MAC/.." && pwd)"
OUT="${1:-$MAC/dist}"
NAME="BGYHub Mailbox Admin v2"
APP="$OUT/$NAME.app"
ZIP="$OUT/BGYHub-Mailbox-Admin-v2.zip"

rm -rf "$APP" "$ZIP"
mkdir -p "$OUT"
cp -R "$MAC/template" "$APP"
PAYLOAD="$APP/Contents/Resources/purelymail"
mkdir -p "$PAYLOAD"
cp "$SRC/mailbox_core.py" "$SRC/tracking_xlsx.py" "$SRC/requirements.txt" "$PAYLOAD/"
cp -R "$SRC/admin" "$SRC/vendor" "$PAYLOAD/"
rm -rf "$PAYLOAD/admin/data"
find "$PAYLOAD" \( -name "__pycache__" -o -name "*.pyc" -o -name ".DS_Store" \) -prune -exec rm -rf {} +
chmod 755 "$APP/Contents/MacOS/$(basename "$(ls "$APP/Contents/MacOS")")" "$PAYLOAD/admin/launch.sh"

COMMIT="$(git -C "$SRC" rev-parse --short HEAD 2>/dev/null || echo unknown)"
printf 'version=%s\ncommit=%s\nbuilt=%s\n' "$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$SRC/admin/__init__.py")" \
  "$COMMIT" "$(date -u +%FT%TZ)" > "$PAYLOAD/BUILD"

# Safety: no user data or secrets in the bundle.
if find "$APP" \( -name ".env" -o -name "*.db" -o -name "credentials*.csv" -o -name "*.xlsx" -o -path "*/output/*" \) | grep -q .; then
  echo "refusing to build: user data found in bundle" >&2
  exit 1
fi
# Safety: no compiled code, so it runs on any Mac/Python 3.9+.
if find "$APP" \( -name "*.so" -o -name "*.dylib" \) | grep -q .; then
  echo "refusing to build: compiled extension found in bundle" >&2
  exit 1
fi

(cd "$OUT" && zip -q -r -X "$(basename "$ZIP")" "$NAME.app")
echo "$APP"
echo "$ZIP"
