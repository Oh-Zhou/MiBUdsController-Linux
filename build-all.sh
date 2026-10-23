#!/usr/bin/env bash
# 一键构建所有安装包（deb / rpm / AppImage / Flatpak）
set -e
cd "$(dirname "$0")"
mkdir -p dist

echo "[1/4] Building .deb …"
./deb/build.sh

echo "[2/4] Building .rpm …"
if command -v rpmbuild >/dev/null 2>&1; then
  TOP="$PWD/rpm/_build"
  mkdir -p "$TOP"/{BUILD,RPMS,SRPMS,SOURCES,SPECS,TMP,DB}
  cp rpm/mibuds-linux-1.0.0.tar.gz "$TOP/SOURCES/"
  rpmbuild \
    --define "_topdir $TOP" --define "_builddir $TOP/BUILD" \
    --define "_rpmdir $TOP/RPMS" --define "_srcrpmdir $TOP/SRPMS" \
    --define "_sourcedir $TOP/SOURCES" --define "_specdir $TOP/SPECS" \
    --define "_tmppath $TOP/TMP" --define "_dbpath $TOP/DB" \
    --define "_build_id_links none" --define "debug_package %{nil}" \
    -ba rpm/mibuds-linux.spec
  cp "$TOP"/RPMS/noarch/*.rpm dist/ 2>/dev/null || true
  cp "$TOP"/SRPMS/*.src.rpm dist/ 2>/dev/null || true
else
  echo "  rpmbuild not found — install: sudo apt install rpm" 
fi

echo "[3/4] Building AppImage …"
if command -v appimagetool >/dev/null 2>&1; then
  ./appimage/build.sh
else
  echo "  appimagetool not found — see https://github.com/AppImage/appimagetool/releases"
fi

echo "[4/4] Building Flatpak …"
if command -v flatpak-builder >/dev/null 2>&1; then
  flatpak-builder --force-clean --install-deps-from=flathub flatpak-build flatpak/io.github.mibuds.Controller.yml || \
    echo "  flatpak build failed (runtime/deps) — manifest provided, build manually"
else
  echo "  flatpak-builder not found — install: sudo apt install flatpak-builder"
fi

echo "=== checksums ==="
cd dist && sha256sum *.deb *.rpm *.AppImage 2>/dev/null | tee SHA256SUMS || true
echo "done → dist/"
