#!/usr/bin/env bash
# 生成 wallpaper-changer_<version>_all.deb（纯 Python，架构无关）
#
# 用法:  bash packaging/linux/build-deb.sh [版本号]
# 依赖:  dpkg-deb (dpkg 包自带)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${1:-1.0.0}"
PKG_NAME="wallpaper-changer"
DIST="${ROOT}/dist"
STAGE="${PKG_NAME}_${VERSION}_all"

# dpkg-deb 要求打包目录权限 <=0775。
# 某些文件系统（overlay / 网络盘 / 部分容器）不支持 chmod，
# 这时自动改用临时目录作为构建根目录。
WORK="${ROOT}/build/deb/${STAGE}"
rm -rf "${WORK}"
mkdir -p "${WORK}/DEBIAN"
chmod 0755 "${WORK}/DEBIAN" 2>/dev/null || true
PERM="$(stat -c '%a' "${WORK}/DEBIAN" 2>/dev/null || echo 755)"
if [ "$((8#${PERM} & 8#022))" -ne 0 ]; then
    echo "提示：${ROOT} 不支持修改目录权限，改用临时目录构建"
    WORK="$(mktemp -d)/${STAGE}"
    rm -rf "${ROOT}/build/deb/${STAGE}"
fi

rm -rf "${WORK}"
mkdir -p "${WORK}/DEBIAN" "${WORK}/opt/${PKG_NAME}" "${DIST}"
mkdir -p "${WORK}/usr/share/applications"
mkdir -p "${WORK}/usr/share/icons/hicolor/256x256/apps"
mkdir -p "${WORK}/usr/bin"
chmod 0755 "${WORK}/DEBIAN"

# 1) 应用本体
cp -r "${ROOT}"/wallpaper_changer "${WORK}/opt/${PKG_NAME}/"
cp "${ROOT}/main.py" "${WORK}/opt/${PKG_NAME}/"
cp "${ROOT}/requirements.txt" "${WORK}/opt/${PKG_NAME}/"
cp -r "${ROOT}/assets" "${WORK}/opt/${PKG_NAME}/"
find "${WORK}" -name "__pycache__" -type d -prune -exec rm -rf {} +

# 2) 桌面文件与图标
cp "${ROOT}/packaging/linux/${PKG_NAME}.desktop" "${WORK}/usr/share/applications/"
cp "${ROOT}/assets/icon.png" "${WORK}/usr/share/icons/hicolor/256x256/apps/${PKG_NAME}.png"

# 3) 启动器
cat > "${WORK}/usr/bin/${PKG_NAME}" <<'EOF'
#!/usr/bin/env bash
# 优先使用系统 Python + PySide6；找不到时给出安装提示
for PY in python3 python3.13 python3.12 python3.11 python3.10; do
    if command -v "$PY" >/dev/null 2>&1 && "$PY" -c "import PySide6" >/dev/null 2>&1; then
        exec "$PY" /opt/wallpaper-changer/main.py "$@"
    fi
done
echo "未找到可用的 Python + PySide6，请先执行："
echo "  sudo apt install python3-pyside6.qtwidgets python3-requests python3-pil"
exit 1
EOF
chmod 0755 "${WORK}/usr/bin/${PKG_NAME}"

# 4) 控制文件
cat > "${WORK}/DEBIAN/control" <<EOF
Package: ${PKG_NAME}
Version: ${VERSION}
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.9), python3-pyside6.qtwidgets, python3-requests, python3-pil
Maintainer: Wallpaper Changer <noreply@example.com>
Description: 跨平台自动更换桌面壁纸工具
 支持 Bing 每日图、本地文件夹与自定义链接（直链 / JSON 接口）作为图片来源，
 可定时轮换、收藏/历史管理、系统托盘常驻与开机自启动。
Homepage: https://github.com/example/wallpaper-changer
EOF

cat > "${WORK}/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f /usr/share/icons/hicolor || true
fi
exit 0
EOF
chmod 0755 "${WORK}/DEBIAN/postinst"

cat > "${WORK}/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
exit 0
EOF
chmod 0755 "${WORK}/DEBIAN/prerm"

dpkg-deb --build --root-owner-group "${WORK}" "${DIST}/${STAGE}.deb"
echo "已生成: ${DIST}/${STAGE}.deb"
echo "安装:   sudo apt install ./${DIST}/${STAGE}.deb"
