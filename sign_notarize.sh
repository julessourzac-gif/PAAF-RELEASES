#!/usr/bin/env bash
# Construit, signe et notarise PAAF (macOS uniquement), puis produit le DMG de distribution.
#
#   ./sign_notarize.sh                 build + signature + notarisation + DMG
#   ./sign_notarize.sh --sign-only     build + signature + DMG signé, sans envoi à Apple
#
# Prérequis (une seule fois) :
#   - certificat « Developer ID Application » dans le trousseau
#   - profil de notarisation enregistré dans le trousseau :
#       xcrun notarytool store-credentials PAAF --apple-id <apple-id> --team-id <TEAM>
# Variables facultatives : SIGN_ID (identité), NOTARY_PROFILE (défaut : PAAF).
#
# Résultat : dist/PAAF_v<VERSION>.dmg (et dist/PAAF_v<VERSION>.zip), signés, notarisés, agrafés.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
DIST="$ROOT/dist"
APP="$DIST/PAAF.app"
DMG="$DIST/PAAF_v$VERSION.dmg"
ZIP="$DIST/PAAF_v$VERSION.zip"
PROFILE="${NOTARY_PROFILE:-PAAF}"
NOTARIZE=1
[ "${1:-}" = "--sign-only" ] && NOTARIZE=0

[ "$(uname)" = "Darwin" ] || { echo "macOS uniquement"; exit 1; }

SIGN_ID="${SIGN_ID:-$(security find-identity -v -p codesigning | sed -n 's/.*"\(Developer ID Application: [^"]*\)".*/\1/p' | head -1)}"
[ -n "$SIGN_ID" ] || { echo "Aucun certificat « Developer ID Application » dans le trousseau."; exit 1; }
echo "Identité : $SIGN_ID"

if [ "$NOTARIZE" = 1 ] && ! xcrun notarytool history --keychain-profile "$PROFILE" >/dev/null 2>&1; then
  echo "Profil de notarisation « $PROFILE » introuvable. À créer une fois :"
  echo "  xcrun notarytool store-credentials $PROFILE --apple-id <apple-id> --team-id <TEAM>"
  exit 1
fi

notarize() {  # $1 fichier (zip ou dmg)
  local out id status
  out="$(xcrun notarytool submit "$1" --keychain-profile "$PROFILE" --wait --output-format json)"
  id="$(printf '%s' "$out" | sed -n 's/.*"id" *: *"\([^"]*\)".*/\1/p' | head -1)"
  status="$(printf '%s' "$out" | sed -n 's/.*"status" *: *"\([^"]*\)".*/\1/p' | head -1)"
  echo "Notarisation $id : $status"
  if [ "$status" != "Accepted" ]; then
    xcrun notarytool log "$id" --keychain-profile "$PROFILE" || true
    exit 1
  fi
}

# 1) build
"$ROOT/build.sh"
rm -f "$ZIP"

# 2) signature de l'app (hardened runtime, horodatage) puis vérification
codesign --force --options runtime --timestamp --sign "$SIGN_ID" "$APP"
codesign --verify --strict --deep --verbose=2 "$APP"

# 3) notarisation de l'app, ticket agrafé dans l'app
if [ "$NOTARIZE" = 1 ]; then
  TMPZIP="$(mktemp -d)/PAAF.zip"
  ditto -c -k --keepParent "$APP" "$TMPZIP"
  notarize "$TMPZIP"
  xcrun stapler staple "$APP"
fi

# 4) DMG : l'app, un raccourci vers Applications et le LISEZMOI
STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp "$ROOT/LISEZMOI.txt" "$STAGE/"
rm -f "$DMG"
hdiutil create -volname "PAAF $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
rm -rf "$STAGE"
codesign --force --timestamp --sign "$SIGN_ID" "$DMG"
if [ "$NOTARIZE" = 1 ]; then
  notarize "$DMG"
  xcrun stapler staple "$DMG"
fi

# 5) zip de l'app signée (pour qui préfère le zip)
STAGE="$(mktemp -d)"
cp -R "$APP" "$DIST/LISEZMOI.txt" "$STAGE/"
(cd "$STAGE" && ditto -c -k --sequesterRsrc . "$ZIP")
rm -rf "$STAGE"

# 6) contrôle Gatekeeper
if [ "$NOTARIZE" = 1 ]; then
  spctl --assess --type execute --verbose=2 "$APP"
  spctl --assess --type open --context context:primary-signature --verbose=2 "$DMG"
  xcrun stapler validate "$DMG"
fi
echo "✓ $DMG"
echo "✓ $ZIP"
