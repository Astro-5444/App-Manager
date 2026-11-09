#!/usr/bin/env bash
#
# Build a Debian installer (.deb) for App Manager.
#
#   ./build_deb.sh
#
# The result is written to build/app-manager_<version>_<arch>.deb and can be
# installed with:  sudo apt install ./build/app-manager_<version>_<arch>.deb
#
set -euo pipefail

APP_ID="app-manager"
APP_NAME="App Manager"
VERSION="${VERSION:-1.0.0}"
ARCH="$(dpkg --print-architecture)"
MAINTAINER="${MAINTAINER:-App Manager <app-manager@localhost>}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$ROOT/build"
STAGE="$BUILD/${APP_ID}_${VERSION}_${ARCH}"

echo "==> Building $APP_NAME $VERSION ($ARCH)"

rm -rf "$STAGE"
mkdir -p \
    "$STAGE/DEBIAN" \
    "$STAGE/usr/bin" \
    "$STAGE/usr/lib/$APP_ID" \
    "$STAGE/usr/share/applications" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps" \
    "$STAGE/usr/share/doc/$APP_ID"

# ── Application payload ─────────────────────────────────────────────────────
cp "$ROOT/app.py" "$ROOT/launcher.py" "$STAGE/usr/lib/$APP_ID/"
cp -r "$ROOT/static" "$STAGE/usr/lib/$APP_ID/static"
chmod -R a+rX "$STAGE/usr/lib/$APP_ID"

# ── Command-line launcher ───────────────────────────────────────────────────
cat > "$STAGE/usr/bin/$APP_ID" <<EOF
#!/bin/sh
exec python3 /usr/lib/$APP_ID/launcher.py "\$@"
EOF
chmod 0755 "$STAGE/usr/bin/$APP_ID"

# ── Desktop entry + icon ────────────────────────────────────────────────────
install -m 0644 "$ROOT/assets/$APP_ID.desktop" \
    "$STAGE/usr/share/applications/$APP_ID.desktop"
install -m 0644 "$ROOT/assets/$APP_ID.svg" \
    "$STAGE/usr/share/icons/hicolor/scalable/apps/$APP_ID.svg"

# Optional raster icon for menus that don't render SVG well.
if command -v rsvg-convert >/dev/null 2>&1; then
    mkdir -p "$STAGE/usr/share/icons/hicolor/256x256/apps"
    rsvg-convert -w 256 -h 256 "$ROOT/assets/$APP_ID.svg" \
        -o "$STAGE/usr/share/icons/hicolor/256x256/apps/$APP_ID.png"
fi

# ── Documentation ───────────────────────────────────────────────────────────
cat > "$STAGE/usr/share/doc/$APP_ID/copyright" <<EOF
$APP_NAME $VERSION
Built from the local source tree.
EOF

# ── Package metadata ────────────────────────────────────────────────────────
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $APP_ID
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Depends: python3 (>= 3.8), python3-flask, python3-gi, gir1.2-gtk-3.0, gir1.2-webkit2-4.1
Recommends: xdg-utils
Maintainer: $MAINTAINER
Description: $APP_NAME
 A native desktop application for launching, renaming, hiding and
 uninstalling applications. The UI is rendered in an embedded WebKit
 window and the backend listens on a random loopback port each launch.
EOF

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
fi
exit 0
EOF

cat > "$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true
fi
exit 0
EOF

chmod 0755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

# ── Build ───────────────────────────────────────────────────────────────────
OUT="$BUILD/${APP_ID}_${VERSION}_${ARCH}.deb"
dpkg-deb --build --root-owner-group "$STAGE" "$OUT"

echo
echo "==> Installer ready: $OUT"
echo "    Install with: sudo apt install \"$OUT\""
