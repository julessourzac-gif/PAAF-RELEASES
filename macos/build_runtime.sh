#!/usr/bin/env bash
# Prépare l'environnement embarqué dans PAAF.app (macOS) :
#   - Python universel (Apple Silicon + Intel) avec tkinter / Tcl-Tk, d'après python-build-standalone
#     (les deux architectures fusionnées avec lipo) ;
#   - bibliothèques Python (py-ptsl, grpcio, protobuf, pyaaf2, tkinterdnd2) en wheels universal2 ;
#   - ffmpeg / ffprobe compilés depuis les sources officielles, en LGPL (pas de --enable-gpl),
#     pour arm64 et x86_64 puis fusionnés.
#
#   ./macos/build_runtime.sh    -> build/runtime/{python,bin,licences}
#
# Le résultat est réutilisé tant que les versions ci-dessous ne changent pas (build/ est ignoré
# par git). Téléchargements dans build/downloads, compilation dans build/work.
set -euo pipefail

PBS_RELEASE="20261110"
PY_VERSION="3.14.10"
PY_MINOR="3.14"
FFMPEG_VERSION="9.0.2"
FFMPEG_SHA256="8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e"  # ffmpeg-9.0.2.tar.xz
# Liste complète, installée sans résolution de dépendances : py-ptsl déclare flake8 et pytest
# comme dépendances d'exécution, inutiles ici.
PIP_DEPS=(py-ptsl==602.0.0 grpcio==1.76.0 protobuf==7.36.2 typing_extensions==4.16.0
          pyaaf2==1.7.1 tkinterdnd2==0.7.0)
MACOS_MIN="11.0"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
DL="$BUILD/downloads"
WORK="$BUILD/work"
OUT="$BUILD/runtime"
KEY="pbs-$PBS_RELEASE-py$PY_VERSION-ffmpeg$FFMPEG_VERSION-$(printf '%s ' "${PIP_DEPS[@]}" | shasum | cut -c1-12)"

[ "$(uname)" = "Darwin" ] || { echo "macOS uniquement"; exit 1; }
if [ -f "$OUT/.$KEY" ]; then
  echo "Environnement embarqué à jour ($KEY)"
  exit 0
fi
mkdir -p "$DL" "$WORK"
NCPU="$(sysctl -n hw.ncpu)"

fetch() {  # $1 url, $2 fichier
  [ -f "$2" ] && return 0
  echo "Téléchargement : $1"
  curl -fL --retry 3 -o "$2.part" "$1"
  mv "$2.part" "$2"
}

is_macho() { file -b "$1" | grep -q '^Mach-O'; }

# ------------------------------------------------------------------ 1) Python universel
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/$PBS_RELEASE"
fetch "$PBS_URL/SHA256SUMS" "$DL/SHA256SUMS-$PBS_RELEASE"
for arch in aarch64 x86_64; do
  f="cpython-$PY_VERSION+$PBS_RELEASE-$arch-apple-darwin-install_only_stripped.tar.gz"
  fetch "$PBS_URL/$f" "$DL/$f"
  want="$(awk -v f="$f" '$2==f {print $1}' "$DL/SHA256SUMS-$PBS_RELEASE")"
  got="$(shasum -a 256 "$DL/$f" | cut -d' ' -f1)"
  [ -n "$want" ] && [ "$want" = "$got" ] || { echo "Empreinte SHA-256 incorrecte : $f"; exit 1; }
  rm -rf "$WORK/py-$arch"
  mkdir -p "$WORK/py-$arch"
  tar -xzf "$DL/$f" -C "$WORK/py-$arch"
  # .pyc livrés de façon inégale selon l'architecture : régénérés plus bas
  find "$WORK/py-$arch" -name __pycache__ -type d -prune -exec rm -rf {} +
done
A="$WORK/py-aarch64/python"
X="$WORK/py-x86_64/python"
if ! diff <(cd "$A" && find . -type f | sort) <(cd "$X" && find . -type f | sort) >/dev/null; then
  echo "Les deux Python n'ont pas les mêmes fichiers :"
  diff <(cd "$A" && find . -type f | sort) <(cd "$X" && find . -type f | sort) | head -20
  exit 1
fi

rm -rf "$OUT"
mkdir -p "$OUT/bin" "$OUT/licences"
cp -R "$A" "$OUT/python"
P="$OUT/python"
# Fichiers inutiles à l'exécution (tests, IDLE, en-têtes C)
rm -rf "$P/include" "$P/lib/pkgconfig" "$P/share" \
       "$P/lib/python$PY_MINOR/test" "$P/lib/python$PY_MINOR/idlelib" "$P/lib/python$PY_MINOR/turtledemo" \
       "$P/lib/python$PY_MINOR/tkinter/test" "$P/lib/python$PY_MINOR/lib2to3/tests"
echo "Fusion arm64 + x86_64…"
n=0
while IFS= read -r -d '' f; do
  rel="${f#"$P"/}"
  if is_macho "$f"; then
    lipo -create "$f" "$X/$rel" -output "$f.universal"
    mv "$f.universal" "$f"
    n=$((n + 1))
  fi
done < <(find "$P" -type f -print0)
echo "  $n fichiers Mach-O fusionnés"
PY="$P/bin/python3"
"$PY" -c "import sys, tkinter; tkinter.Tcl(); print('Python', sys.version.split()[0], 'Tk', tkinter.TkVersion)"
arch -x86_64 "$PY" -c "import platform, tkinter; tkinter.Tcl(); print('  x86_64 (Rosetta) :', platform.machine())" \
  || echo "  (Rosetta absent : partie x86_64 non testée)"

# ------------------------------------------------------------------ 2) bibliothèques
SITE="$P/lib/python$PY_MINOR/site-packages"
echo "Bibliothèques : ${PIP_DEPS[*]}"
"$PY" -m pip install --disable-pip-version-check --no-compile -q --upgrade --no-deps \
  --only-binary=:all: --platform "macosx_${MACOS_MIN/./_}_universal2" --platform macosx_10_9_universal2 \
  --python-version "$PY_MINOR" --implementation cp --target "$SITE" "${PIP_DEPS[@]}"
bad=0
while IFS= read -r -d '' f; do
  if is_macho "$f"; then
    archs="$(lipo -archs "$f")"
    case "$archs" in
      *arm64*x86_64*|*x86_64*arm64*) ;;
      *) # tkinterdnd2 fournit un dossier par architecture : c'est normal
         case "$f" in */tkdnd/osx-*) ;; *) echo "  pas universel ($archs) : ${f#"$P"/}"; bad=1 ;; esac ;;
    esac
  fi
done < <(find "$SITE" -type f \( -name '*.so' -o -name '*.dylib' \) -print0)
[ "$bad" = 0 ] || { echo "Bibliothèque non universelle"; exit 1; }
# pip ne sert qu'à l'installation ; libpython (37 Mo) n'est utilisé par rien, l'exécutable
# python3 étant lié statiquement
rm -rf "$SITE/pip" "$SITE"/pip-*.dist-info "$P/lib/python$PY_MINOR/ensurepip" "$P/lib/libpython$PY_MINOR.dylib" \
       "$P/bin/pip"* "$P/bin/idle"* "$P/bin/pydoc"* "$P/bin/python$PY_MINOR-config" "$P/bin/python3-config"
CHECK="import ptsl, ptsl.PTSL_pb2, grpc, google.protobuf, aaf2, tkinterdnd2, tkinter; tkinter.Tcl()"
"$PY" -c "$CHECK" && echo "  bibliothèques OK (arm64)"
if arch -x86_64 /usr/bin/true 2>/dev/null; then
  arch -x86_64 "$PY" -c "$CHECK" && echo "  bibliothèques OK (x86_64)"
else
  echo "  (Rosetta absent : partie x86_64 non testée)"
fi

# .pyc précompilés (l'app signée ne doit pas écrire dans son paquet : PYTHONDONTWRITEBYTECODE)
"$PY" -m compileall -q -j 0 --invalidation-mode unchecked-hash "$P/lib/python$PY_MINOR" >/dev/null || true

# ------------------------------------------------------------------ 3) ffmpeg / ffprobe LGPL
FF="ffmpeg-$FFMPEG_VERSION"
fetch "https://ffmpeg.org/releases/$FF.tar.xz" "$DL/$FF.tar.xz"
got="$(shasum -a 256 "$DL/$FF.tar.xz" | cut -d' ' -f1)"
if [ -n "$FFMPEG_SHA256" ] && [ "$got" != "$FFMPEG_SHA256" ]; then
  echo "Empreinte SHA-256 incorrecte : $FF.tar.xz ($got)"
  exit 1
fi
echo "  $FF.tar.xz : sha256 $got"
SRC="$WORK/ffmpeg-src/$FF"
if [ ! -x "$SRC/configure" ]; then
  rm -rf "$WORK/ffmpeg-src"
  mkdir -p "$WORK/ffmpeg-src"
  tar -xJf "$DL/$FF.tar.xz" -C "$WORK/ffmpeg-src"
fi

HOST="$(uname -m)"
# Options communes aux deux architectures (LGPL : jamais --enable-gpl / --enable-nonfree)
FF_OPTS=(--target-os=darwin --disable-autodetect --enable-pthreads --enable-zlib
         --disable-doc --disable-debug --disable-ffplay --disable-network --disable-avdevice --disable-x86asm
         --disable-encoders --enable-encoder=pcm_s16le,pcm_s24le,pcm_s32le,pcm_f32le
         --disable-muxers --enable-muxer=wav,w64,null
         --disable-protocols --enable-protocol=file,pipe)
FF_STAMP=".done-$FF-$(printf '%s ' "$MACOS_MIN" "${FF_OPTS[@]}" | shasum | cut -c1-12)"
for arch in arm64 x86_64; do
  B="$WORK/ffmpeg-$arch"
  if [ -f "$B/$FF_STAMP" ]; then
    echo "ffmpeg $arch : déjà compilé"
    continue
  fi
  rm -rf "$B"
  mkdir -p "$B"
  cross=()
  [ "$arch" = "$HOST" ] || cross=(--enable-cross-compile)
  echo "ffmpeg $arch : configuration…"
  (cd "$B" && "$SRC/configure" --prefix="$B/inst" --arch="$arch" ${cross[@]+"${cross[@]}"} \
      --cc="clang -arch $arch" \
      --extra-cflags="-mmacosx-version-min=$MACOS_MIN" --extra-ldflags="-arch $arch -mmacosx-version-min=$MACOS_MIN" \
      "${FF_OPTS[@]}" > "$B/configure.log" 2>&1) || { tail -30 "$B/configure.log"; exit 1; }
  echo "ffmpeg $arch : compilation…"
  (cd "$B" && make -j"$NCPU" > "$B/make.log" 2>&1 && make install >> "$B/make.log" 2>&1) \
    || { tail -30 "$B/make.log"; exit 1; }
  touch "$B/$FF_STAMP"
done
for tool in ffmpeg ffprobe; do
  lipo -create "$WORK/ffmpeg-arm64/inst/bin/$tool" "$WORK/ffmpeg-x86_64/inst/bin/$tool" -output "$OUT/bin/$tool"
  chmod 755 "$OUT/bin/$tool"
done
"$OUT/bin/ffmpeg" -hide_banner -version | head -3
if "$OUT/bin/ffmpeg" -hide_banner -buildconf | grep -q -- '--enable-gpl'; then
  echo "ffmpeg compilé en GPL : refusé"
  exit 1
fi

# ------------------------------------------------------------------ 4) licences
cp "$SRC/COPYING.LGPLv2.1" "$OUT/licences/FFMPEG-COPYING.LGPLv2.1.txt"
cp "$SRC/LICENSE.md" "$OUT/licences/FFMPEG-LICENSE.md"
{
  echo "ffmpeg et ffprobe $FFMPEG_VERSION, compilés pour PAAF sous licence LGPL v2.1 ou ultérieure."
  echo "Sources : https://ffmpeg.org/releases/$FF.tar.xz (sha256 $got), non modifiées."
  echo
  echo "Configuration :"
  "$OUT/bin/ffmpeg" -hide_banner -buildconf
} > "$OUT/licences/FFMPEG.txt"
cp "$P/lib/python$PY_MINOR/LICENSE.txt" "$OUT/licences/PYTHON-LICENSE.txt" 2>/dev/null || true

touch "$OUT/.$KEY"
du -sh "$OUT/python" "$OUT/bin"
echo "✓ $OUT"
