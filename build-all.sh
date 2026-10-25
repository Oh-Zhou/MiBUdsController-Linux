#!/usr/bin/env bash
# 一键构建所有安装包（deb / rpm / AppImage）
set -e
cd "$(dirname "$0")"
VERSION=$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' tray.py)
echo "version: $VERSION"
rm -rf release && mkdir -p release dist

echo "[1/3] Building .deb …"
./deb/build.sh

echo "[2/3] Building .rpm …"
if command -v rpmbuild >/dev/null 2>&1; then
  TOP="$PWD/rpm/_build"
  mkdir -p "$TOP"/{BUILD,RPMS,SRPMS,SOURCES,SPECS,TMP,DB}
  cp rpm/mibuds-linux-${VERSION}.tar.gz "$TOP/SOURCES/"
  rpmbuild \
    --define "_topdir $TOP" --define "_builddir $TOP/BUILD" \
    --define "_rpmdir $TOP/RPMS" --define "_srcrpmdir $TOP/SRPMS" \
    --define "_sourcedir $TOP/SOURCES" --define "_specdir $TOP/SPECS" \
    --define "_tmppath $TOP/TMP" --define "_dbpath $TOP/DB" \
    --define "_build_id_links none" --define "debug_package %{nil}" \
    -ba rpm/mibuds-linux.spec
  cp "$TOP"/RPMS/noarch/*.rpm "dist/MiBuds-${VERSION}-noarch.rpm" 2>/dev/null || true
  cp "$TOP"/SRPMS/*.src.rpm dist/ 2>/dev/null || true  # src.rpm 保留原名
else
  echo "  rpmbuild not found — install: sudo apt install rpm" 
fi

echo "[3/3] Building AppImage …"
if command -v appimagetool >/dev/null 2>&1; then
  ./appimage/build.sh
else
  echo "  appimagetool not found — see https://github.com/AppImage/appimagetool/releases"
fi


echo "=== checksums ==="
cd dist && sha256sum MiBuds-*.deb MiBuds-*.rpm MiBuds-*.AppImage 2>/dev/null | tee SHA256SUMS || true
echo "done → dist/"
