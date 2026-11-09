#!/usr/bin/env bash
# Build a release tarball with the prebuilt COSMIC applet:
#   scripts/release.sh            -> dist/tokenbar-linux-<arch>.tar.gz (+ .sha256)
# The file name has no version, so .../releases/latest/download/tokenbar-linux-<arch>.tar.gz
# always points to the newest release. The version is in tokenbar/__init__.py and the git tag.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(python3 -c "import sys; sys.path.insert(0, '$ROOT'); import tokenbar; print(tokenbar.__version__)")"
ARCH="$(uname -m)"
NAME="tokenbar-linux-$ARCH"   # asset name
DIR="tokenbar-linux"           # folder inside the tarball
CARGO="$(command -v cargo || echo "$HOME/.cargo/bin/cargo")"

LIBDIR="$ROOT/applet/.deps/root/usr/lib/$ARCH-linux-gnu/pkgconfig"
[[ -d "$LIBDIR" ]] && export PKG_CONFIG_PATH="$LIBDIR${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
(cd "$ROOT/applet" && "$CARGO" build --release --locked --quiet)

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
OUT="$STAGE/$DIR"
mkdir -p "$OUT/bin" "$OUT/applet/data" "$OUT/data"
cp -r "$ROOT/tokenbar" "$OUT/"
find "$OUT/tokenbar" -name __pycache__ -prune -exec rm -rf {} +
cp "$ROOT/install.sh" "$ROOT/README.md" "$ROOT/LICENSE" "$OUT/"
cp "$ROOT"/applet/data/*.desktop "$OUT/applet/data/"
cp -r "$ROOT/data/icons" "$OUT/data/"
install -m755 "$ROOT/applet/target/release/cosmic-applet-tokenbar" "$OUT/bin/"

mkdir -p "$ROOT/dist"
rm -f "$ROOT"/dist/tokenbar-linux-*.tar.gz*
tar -C "$STAGE" -czf "$ROOT/dist/$NAME.tar.gz" "$DIR"
(cd "$ROOT/dist" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
echo "dist/$NAME.tar.gz (v$VERSION)"
GLIBC="$(objdump -T "$OUT/bin/cosmic-applet-tokenbar" | grep -oE 'GLIBC_[0-9.]+' | sort -Vu | tail -1)"
echo "applet needs: $GLIBC, libxkbcommon.so.0"
