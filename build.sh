#!/usr/bin/env bash
# Construit « PAAF.app » et l'archive zip de distribution.
#   ./build.sh            -> dist/PAAF.app + dist/PAAF_v<VERSION>.zip
# macOS uniquement : l'app embarque Python, ses bibliothèques et ffmpeg (macos/build_runtime.sh,
# résultat mis en cache dans build/runtime).
set -euo pipefail
[ "$(uname)" = "Darwin" ] || { echo "macOS uniquement"; exit 1; }

ROOT="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
BUILD="${BUILD_NUMBER:-$(git -C "$ROOT" rev-list --count HEAD 2>/dev/null || echo 1)}"
NAME="PAAF"
DIST="$ROOT/dist"
APP="$DIST/$NAME.app"
ZIP="$DIST/PAAF_v$VERSION.zip"

echo "Build $NAME v$VERSION ($BUILD)"

# Environnement embarqué (préparé une fois, puis réutilisé)
"$ROOT/macos/build_runtime.sh"
RUNTIME="$ROOT/build/runtime"
PY="$RUNTIME/python/bin/python3"

# Vérifications
"$PY" -m py_compile "$ROOT/src/ptsl_import_sequentiel.py" "$ROOT/src/ptsl_import_gui.py" "$ROOT/src/aaf_inspect.py" \
  "$ROOT/src/paaf_theme.py" "$ROOT/src/paaf_strings.py" "$ROOT/src/conform.py"
bash -n "$ROOT/macos/launcher"

rm -rf "$DIST"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

sed -e "s/@VERSION@/$VERSION/g" -e "s/@BUILD@/$BUILD/g" "$ROOT/macos/Info.plist" > "$APP/Contents/Info.plist"
# Lanceur bash dans Resources ; exécutable compilé (signable / notarisable) dans MacOS.
sed -e "s/@VERSION@/$VERSION/g" "$ROOT/macos/launcher" > "$APP/Contents/Resources/launcher"
chmod 755 "$APP/Contents/Resources/launcher"
clang -O2 -Wall -arch arm64 -arch x86_64 -mmacosx-version-min=11.0 \
      -o "$APP/Contents/MacOS/PAAF" "$ROOT/macos/stub.c"
chmod 755 "$APP/Contents/MacOS/PAAF"
printf 'APPL????' > "$APP/Contents/PkgInfo"
cp "$ROOT/macos/AppIcon.icns" "$ROOT/src/ptsl_import_sequentiel.py" "$ROOT/src/ptsl_import_gui.py" \
   "$ROOT/src/aaf_inspect.py" "$ROOT/src/paaf_theme.py" "$ROOT/src/paaf_strings.py" "$ROOT/src/conform.py" "$APP/Contents/Resources/"
mkdir -p "$APP/Contents/Resources/assets"
cp "$ROOT"/src/assets/logo_64.png "$ROOT"/src/assets/logo_128.png "$ROOT"/src/assets/logo_256.png \
   "$ROOT"/src/assets/paaf-logo-clair-2x.png "$ROOT"/src/assets/paaf-logo-sombre-2x.png \
   "$APP/Contents/Resources/assets/"
# Python, bibliothèques, ffmpeg / ffprobe et leurs licences
ditto "$RUNTIME/python" "$APP/Contents/Resources/python"
ditto "$RUNTIME/bin" "$APP/Contents/Resources/bin"
ditto "$RUNTIME/licences" "$APP/Contents/Resources/licences"
# .pyc des modules de PAAF précompilés : l'app signée ne les écrit pas elle-même
"$PY" -m compileall -q --invalidation-mode unchecked-hash \
  "$APP/Contents/Resources/"*.py >/dev/null
cp "$ROOT/LISEZMOI.txt" "$DIST/"

plutil -lint "$APP/Contents/Info.plist"

# Archive : conserve les droits d'exécution et les liens symboliques
STAGE="$(mktemp -d)"
ditto "$APP" "$STAGE/$NAME.app"
cp "$DIST/LISEZMOI.txt" "$STAGE/"
(cd "$STAGE" && ditto -c -k --sequesterRsrc . "$ZIP")
rm -rf "$STAGE"
du -sh "$APP"

echo "✓ $APP"
echo "✓ $ZIP"
