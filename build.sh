#!/usr/bin/env bash
# Construit « Import Pro Tools.app » et l'archive zip de distribution.
#   ./build.sh            -> dist/Import Pro Tools.app + dist/Import_Pro_Tools_v<VERSION>.zip
# Fonctionne sous macOS (ditto) et Linux (zip, pour la CI).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
BUILD="${BUILD_NUMBER:-$(git -C "$ROOT" rev-list --count HEAD 2>/dev/null || echo 1)}"
NAME="Import Pro Tools"
DIST="$ROOT/dist"
APP="$DIST/$NAME.app"
ZIP="$DIST/Import_Pro_Tools_v$VERSION.zip"

echo "Build $NAME v$VERSION ($BUILD)"

# Vérifications
python3 -m py_compile "$ROOT/src/ptsl_import_sequentiel.py" "$ROOT/src/ptsl_import_gui.py"
bash -n "$ROOT/macos/launcher"

rm -rf "$DIST"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

sed -e "s/@VERSION@/$VERSION/g" -e "s/@BUILD@/$BUILD/g" "$ROOT/macos/Info.plist" > "$APP/Contents/Info.plist"
sed -e "s/@VERSION@/$VERSION/g" "$ROOT/macos/launcher" > "$APP/Contents/MacOS/launcher"
chmod 755 "$APP/Contents/MacOS/launcher"
printf 'APPL????' > "$APP/Contents/PkgInfo"
cp "$ROOT/macos/AppIcon.icns" "$ROOT/src/ptsl_import_sequentiel.py" "$ROOT/src/ptsl_import_gui.py" \
   "$APP/Contents/Resources/"
cp "$ROOT/LISEZMOI.txt" "$DIST/"

if command -v plutil >/dev/null; then plutil -lint "$APP/Contents/Info.plist"; fi

# Archive : conserve les droits d'exécution du lanceur
if command -v ditto >/dev/null; then
  STAGE="$(mktemp -d)"
  cp -R "$APP" "$DIST/LISEZMOI.txt" "$STAGE/"
  (cd "$STAGE" && ditto -c -k --sequesterRsrc . "$ZIP")
  rm -rf "$STAGE"
else
  (cd "$DIST" && zip -qry "$ZIP" "$NAME.app" LISEZMOI.txt)
fi

echo "✓ $APP"
echo "✓ $ZIP"
