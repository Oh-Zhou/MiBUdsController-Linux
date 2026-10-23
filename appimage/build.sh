#!/bin/bash
set -e
cd "$(dirname "$0")"
# Requires appimagetool (https://github.com/AppImage/appimagetool/releases)
TOOL="${APPIMAGETOOL:-appimagetool}"
rm -rf mibuds.AppDir
mkdir -p mibuds.AppDir/usr/share/mibuds-linux
cp ../tray.py ../rcsp.py ../saferplus.py ../probe.py ../mibuds.sh ../README.md mibuds.AppDir/usr/share/mibuds-linux/
cat > mibuds.AppDir/AppRun <<'INNER'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec python3 "$HERE/usr/share/mibuds-linux/tray.py" "$@"
INNER
chmod 755 mibuds.AppDir/AppRun
cat > mibuds.AppDir/mibuds-controller.desktop <<'DESK'
[Desktop Entry]
Type=Application
Name=MiBuds Controller
Comment=Xiaomi/Redmi earbuds battery and ANC controller
Exec=mibuds.sh
Icon=mibuds
Terminal=false
Categories=Utility;AudioVideo;
StartupNotify=false
DESK
# 128x128 icon placeholder — replace with real icon
convert -size 128x128 xc:white -fill black -draw "circle 64,64 64,10" mibuds.AppDir/mibuds.png 2>/dev/null || \
  cp /usr/share/icons/Adwaita/symbolic/legacy/battery-good-symbolic.svg mibuds.AppDir/mibuds.svg
ARCH=x86_64 "$TOOL" --no-appstream mibuds.AppDir ../release/MiBuds-1.0.0-x86_64.AppImage
