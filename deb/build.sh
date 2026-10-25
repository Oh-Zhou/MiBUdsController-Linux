#!/bin/bash
set -e
cd "$(dirname "$0")"
VERSION=$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' ../tray.py)
ROOT=build
rm -rf "$ROOT"
mkdir -p "$ROOT/opt/mibuds-linux" "$ROOT/usr/bin" "$ROOT/usr/share/applications" "$ROOT/usr/share/doc/mibuds-linux" "$ROOT/DEBIAN"
cp ../tray.py ../rcsp.py ../saferplus.py ../probe.py "$ROOT/opt/mibuds-linux/"
cp ../mibuds.sh "$ROOT/opt/mibuds-linux/"
chmod 755 "$ROOT/opt/mibuds-linux/mibuds.sh"
cat > "$ROOT/usr/bin/mibuds" <<'WRAP'
#!/usr/bin/env bash
exec /opt/mibuds-linux/mibuds.sh "$@"
WRAP
chmod 755 "$ROOT/usr/bin/mibuds"
cat > "$ROOT/usr/share/applications/mibuds-controller.desktop" <<'DESK'
[Desktop Entry]
Type=Application
Name=MiBuds 耳机控制
Comment=Xiaomi/Redmi earbuds battery and ANC controller
Exec=/usr/bin/mibuds
Icon=battery-good-symbolic
Terminal=false
Categories=Utility;AudioVideo;
Keywords=xiaomi;redmi;earbuds;bluetooth;anc;
DESK
cp ../README.md "$ROOT/usr/share/doc/mibuds-linux/"
cp -r DEBIAN "$ROOT/"
fakeroot dpkg-deb --build "$ROOT" ../release/mibuds-linux_${VERSION}_all.deb
echo "deb built: ../release/mibuds-linux_${VERSION}_all.deb"
