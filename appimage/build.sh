#!/bin/bash
set -e
cd "$(dirname "$0")"
# Requires appimagetool (https://github.com/AppImage/appimagetool/releases)
TOOL="${APPIMAGETOOL:-appimagetool}"
VERSION=$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' ../tray.py)
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
Exec=AppRun
Icon=mibuds
Terminal=false
Categories=Utility;AudioVideo;
StartupNotify=false
DESK
# 128x128 icon placeholder — replace with real icon
convert -size 128x128 xc:white -fill black -draw "circle 64,64 64,10" mibuds.AppDir/mibuds.png 2>/dev/null || \
  cp /usr/share/icons/Adwaita/symbolic/legacy/battery-good-symbolic.svg mibuds.AppDir/mibuds.svg
# 离线/内网环境可指定本地 runtime：APPIMAGE_RUNTIME=/path/to/runtime-x86_64
RUNTIME_ARG=""
if [ -n "${APPIMAGE_RUNTIME:-}" ] && [ -f "$APPIMAGE_RUNTIME" ]; then
  RUNTIME_ARG="--runtime-file $APPIMAGE_RUNTIME"
fi
# shellcheck disable=SC2086
ARCH=x86_64 "$TOOL" --no-appstream $RUNTIME_ARG mibuds.AppDir ../release/MiBuds-${VERSION}-x86_64.AppImage
