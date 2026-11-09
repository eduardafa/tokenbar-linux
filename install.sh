#!/usr/bin/env bash
# Install TokenBar for the current user (no root needed).
#   ./install.sh              install / update
#   ./install.sh --autostart  also start it at login
#   ./install.sh --uninstall  remove everything
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/tokenbar"
BIN="$HOME/.local/bin/tokenbar"
DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/io.github.eduardafranca.TokenBar.desktop"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/tokenbar.desktop"
APPLET_ID="io.github.eduardafranca.CosmicAppletTokenBar"
APPLET_BIN="$HOME/.local/bin/cosmic-applet-tokenbar"
APPLET_DESKTOP="${XDG_DATA_HOME:-$HOME/.local/share}/applications/$APPLET_ID.desktop"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
ICON_NAME="io.github.eduardafranca.TokenBar"

if [[ "${1:-}" == "--uninstall" ]]; then
  pkill -f "python3 -m tokenbar" 2>/dev/null || true
  pkill -f "^$APPLET_BIN" 2>/dev/null || true
  rm -rf "$PREFIX" "$BIN" "$DESKTOP" "$AUTOSTART" "$APPLET_BIN" "$APPLET_DESKTOP" \
         "$ICONS/scalable/apps/$ICON_NAME.svg" "$ICONS/symbolic/apps/$ICON_NAME-symbolic.svg"
  echo "TokenBar removed. (Config kept in ~/.config/tokenbar)"
  exit 0
fi

# Remove the pre-rename install ("Token Monitor"). Each path is checked to be ours first.
legacy_cleanup() {
  local bin="$HOME/.local/bin/token-monitor" share="${XDG_DATA_HOME:-$HOME/.local/share}/token-monitor"
  local apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  grep -qs "python3 -m token_monitor" "$bin" && rm -f "$bin"
  [[ -f "$share/token_monitor/claude_cost.py" ]] && rm -rf "$share"
  rm -f "$apps/io.github.eduardafranca.TokenMonitor.desktop" \
        "$apps/io.github.eduardafranca.CosmicAppletTokenMonitor.desktop" \
        "$HOME/.local/bin/cosmic-applet-token-monitor"
  grep -qs "python3 -m token_monitor\|/token-monitor --hidden" "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/token-monitor.desktop" \
    && rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/token-monitor.desktop"
  [[ -f "${XDG_CACHE_HOME:-$HOME/.cache}/token-monitor/claude-cost-v2.json" ]] && rm -rf "${XDG_CACHE_HOME:-$HOME/.cache}/token-monitor"
  return 0
}
legacy_cleanup

if ! python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1"); import cairo; gi.require_foreign("cairo")' 2>/dev/null; then
  echo "Missing GTK 4 / libadwaita / PyGObject / pycairo. Install them with:"
  if command -v apt >/dev/null; then echo "  sudo apt install python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1"
  elif command -v dnf >/dev/null; then echo "  sudo dnf install python3-gobject python3-cairo gtk4 libadwaita"
  elif command -v pacman >/dev/null; then echo "  sudo pacman -S python-gobject python-cairo gtk4 libadwaita"
  elif command -v zypper >/dev/null; then echo "  sudo zypper install python3-gobject python3-gobject-cairo typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1"
  else echo "  PyGObject, pycairo, GTK 4 and libadwaita (1.5+) from your distribution"; fi
  echo "(The command-line part works without them: PYTHONPATH=$SRC python3 -m tokenbar usage)"
  exit 1
fi

mkdir -p "$PREFIX" "$(dirname "$BIN")" "$(dirname "$DESKTOP")"
rm -rf "$PREFIX/tokenbar"
cp -r "$SRC/tokenbar" "$PREFIX/"
find "$PREFIX" -name __pycache__ -prune -exec rm -rf {} +

cat > "$BIN" <<EOF
#!/usr/bin/env bash
PYTHONPATH="$PREFIX" exec python3 -m tokenbar "\$@"
EOF
chmod +x "$BIN"

cat > "$DESKTOP" <<EOF
[Desktop Entry]
Type=Application
Name=TokenBar
Comment=AI coding plan usage limits and spend
Exec=$BIN
Icon=$ICON_NAME
Categories=Utility;Development;Monitor;
Keywords=claude;codex;tokens;usage;limits;
StartupNotify=false
EOF

install -Dm644 "$SRC/data/icons/$ICON_NAME.svg" "$ICONS/scalable/apps/$ICON_NAME.svg"
install -Dm644 "$SRC/data/icons/$ICON_NAME-symbolic.svg" "$ICONS/symbolic/apps/$ICON_NAME-symbolic.svg"
gtk-update-icon-cache -qtf "$ICONS" 2>/dev/null || true

# COSMIC panel applet (Rust): use the prebuilt binary from a release tarball if present,
# otherwise build it with cargo. Skipped when neither is available.
PREBUILT="$SRC/bin/cosmic-applet-tokenbar"
CARGO="$(command -v cargo || echo "$HOME/.cargo/bin/cargo")"

build_applet() {
  # libcosmic needs xkbcommon headers. If libxkbcommon-dev isn't installed, unpack it
  # locally (apt download needs no root); the runtime library is already on the system.
  if ! pkg-config --exists xkbcommon && command -v apt >/dev/null; then
    DEPS="$SRC/applet/.deps"
    MULTIARCH="$(dpkg-architecture -qDEB_HOST_MULTIARCH 2>/dev/null || echo "$(uname -m)-linux-gnu")"
    LIBDIR="$DEPS/root/usr/lib/$MULTIARCH"
    if [[ ! -f "$LIBDIR/pkgconfig/xkbcommon.pc" ]]; then
      mkdir -p "$DEPS/debs" "$DEPS/root"
      (cd "$DEPS/debs" && apt download libxkbcommon-dev >/dev/null)
      dpkg-deb -x "$DEPS"/debs/libxkbcommon-dev_*.deb "$DEPS/root"
      ln -sfn "$(readlink -f "/usr/lib/$MULTIARCH/libxkbcommon.so.0")" "$LIBDIR/libxkbcommon.so"
      sed -i "s#^prefix=.*#prefix=$DEPS/root/usr#" "$LIBDIR/pkgconfig/xkbcommon.pc"
    fi
    export PKG_CONFIG_PATH="$LIBDIR/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
  fi
  if ! pkg-config --exists xkbcommon && [[ -z "${PKG_CONFIG_PATH:-}" ]]; then
    echo "The applet needs xkbcommon headers: install libxkbcommon-dev / libxkbcommon-devel / libxkbcommon."
    exit 1
  fi
  echo "Building COSMIC applet (first build takes a few minutes)..."
  (cd "$SRC/applet" && "$CARGO" build --release --quiet)
  PREBUILT="$SRC/applet/target/release/cosmic-applet-tokenbar"
}

if [[ "${TOKENBAR_NO_APPLET:-}" == 1 ]]; then
  :
elif [[ -x "$PREBUILT" ]] || { [[ -x "$CARGO" && -d "$SRC/applet/src" ]] && build_applet; }; then
  install -m755 "$PREBUILT" "$APPLET_BIN.new"
  mv -f "$APPLET_BIN.new" "$APPLET_BIN"   # atomic swap, safe while the panel runs it
  sed "s#@BIN@#$APPLET_BIN#" "$SRC/applet/data/$APPLET_ID.desktop" > "$APPLET_DESKTOP"
  echo "Applet installed. Add it in Settings > Desktop > Panel > Configure panel applets ('TokenBar')."
else
  echo "No prebuilt applet and no cargo: skipping the COSMIC panel applet (tray icon mode still works)."
fi

if [[ "${1:-}" == "--autostart" ]]; then
  mkdir -p "$(dirname "$AUTOSTART")"
  cat > "$AUTOSTART" <<EOF
[Desktop Entry]
Type=Application
Name=TokenBar
Exec=$BIN --hidden
Icon=$ICON_NAME
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF
  echo "Autostart enabled."
fi

echo "Installed. Launch 'TokenBar' from the app library, or run: tokenbar"
echo "CLI: tokenbar usage [--json]"
