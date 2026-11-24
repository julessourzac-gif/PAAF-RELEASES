#!/usr/bin/env bash
# Construit « PAAF.app » et l'archive zip de distribution.
#   ./build.sh            -> dist/PAAF.app + dist/PAAF_v<VERSION>.zip
# Fonctionne sous macOS (ditto) et Linux (zip, pour la CI).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
BUILD="${BUILD_NUMBER:-$(git -C "$ROOT" rev-list --count HEAD 2>/dev/null || echo 1)}"
NAME="PAAF"
DIST="$ROOT/dist"
APP="$DIST/$NAME.app"
ZIP="$DIST/PAAF_v$VERSION.zip"

echo "Build $NAME v$VERSION ($BUILD)"

# Vérifications
python3 -m py_compile "$ROOT/src/ptsl_import_sequentiel.py" "$ROOT/src/ptsl_import_gui.py" "$ROOT/src/aaf_inspect.py" \
  "$ROOT/src/paaf_theme.py" "$ROOT/src/paaf_strings.py" "$ROOT/src/conform.py"
bash -n "$ROOT/macos/launcher"

rm -rf "$DIST"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

sed -e "s/@VERSION@/$VERSION/g" -e "s/@BUILD@/$BUILD/g" "$ROOT/macos/Info.plist" > "$APP/Contents/Info.plist"
# Lanceur bash dans Resources ; exécutable compilé (signable / notarisable) dans MacOS.
sed -e "s/@VERSION@/$VERSION/g" "$ROOT/macos/launcher" > "$APP/Contents/Resources/launcher"
chmod 755 "$APP/Contents/Resources/launcher"
if [ "$(uname)" = "Darwin" ] && command -v clang >/dev/null; then
  clang -O2 -Wall -arch arm64 -arch x86_64 -mmacosx-version-min=11.0 \
        -o "$APP/Contents/MacOS/PAAF" "$ROOT/macos/stub.c"
else
  # hors macOS (CI Linux) : le script sert directement d'exécutable
  cp "$APP/Contents/Resources/launcher" "$APP/Contents/MacOS/PAAF"
fi
chmod 755 "$APP/Contents/MacOS/PAAF"
printf 'APPL????' > "$APP/Contents/PkgInfo"
cp "$ROOT/macos/AppIcon.icns" "$ROOT/src/ptsl_import_sequentiel.py" "$ROOT/src/ptsl_import_gui.py" \
   "$ROOT/src/aaf_inspect.py" "$ROOT/src/paaf_theme.py" "$ROOT/src/paaf_strings.py" "$ROOT/src/conform.py" "$APP/Contents/Resources/"
mkdir -p "$APP/Contents/Resources/assets"
cp "$ROOT"/src/assets/logo_64.png "$ROOT"/src/assets/logo_128.png "$ROOT"/src/assets/logo_256.png \
   "$ROOT"/src/assets/paaf-logo-clair-2x.png "$ROOT"/src/assets/paaf-logo-sombre-2x.png \
   "$APP/Contents/Resources/assets/"
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
