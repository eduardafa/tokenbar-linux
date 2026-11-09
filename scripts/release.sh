#!/usr/bin/env bash
# Build a release tarball with the prebuilt COSMIC applet:
#   scripts/release.sh            -> dist/tokenbar-linux-v<version>-<arch>.tar.gz (+ .sha256)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(python3 -c "import sys; sys.path.insert(0, '$ROOT'); import tokenbar; print(tokenbar.__version__)")"
ARCH="$(uname -m)"
NAME="tokenbar-linux-v$VERSION-$ARCH"
CARGO="$(command -v cargo || echo "$HOME/.cargo/bin/cargo")"

LIBDIR="$ROOT/applet/.deps/root/usr/lib/$ARCH-linux-gnu/pkgconfig"
[[ -d "$LIBDIR" ]] && export PKG_CONFIG_PATH="$LIBDIR${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
(cd "$ROOT/applet" && "$CARGO" build --release --locked --quiet)

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
OUT="$STAGE/$NAME"
mkdir -p "$OUT/bin" "$OUT/applet/data"
cp -r "$ROOT/tokenbar" "$OUT/"
find "$OUT/tokenbar" -name __pycache__ -prune -exec rm -rf {} +
cp "$ROOT/install.sh" "$ROOT/README.md" "$ROOT/LICENSE" "$OUT/"
cp "$ROOT"/applet/data/*.desktop "$OUT/applet/data/"
install -m755 "$ROOT/applet/target/release/cosmic-applet-tokenbar" "$OUT/bin/"

mkdir -p "$ROOT/dist"
tar -C "$STAGE" -czf "$ROOT/dist/$NAME.tar.gz" "$NAME"
(cd "$ROOT/dist" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
echo "dist/$NAME.tar.gz"
GLIBC="$(objdump -T "$OUT/bin/cosmic-applet-tokenbar" | grep -oE 'GLIBC_[0-9.]+' | sort -Vu | tail -1)"
echo "applet needs: $GLIBC, libxkbcommon.so.0"
