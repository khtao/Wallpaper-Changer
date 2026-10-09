#!/usr/bin/env bash
# 生成 Wallpaper-Changer-x86_64.AppImage
#
# 用法:  bash packaging/linux/build-appimage.sh [版本] [--dir-only]
# 依赖:  python3 + pip（构建机），appimagetool（脚本会自动下载）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION="${1:-1.0.0}"
APPDIR="${ROOT}/build/AppDir"
DIST="${ROOT}/dist"
PYVER="$(python3 -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
PY_PREFIX="$(python3 -c 'import sys;print(sys.prefix)')"
PY_BIN="$(command -v python3)"
DIR_ONLY=0
for a in "$@"; do [ "$a" = "--dir-only" ] && DIR_ONLY=1; done

echo "==> Python ${PYVER} (${PY_PREFIX})"
rm -rf "${APPDIR}" 2>/dev/null || true
mkdir -p "${APPDIR}/usr/bin" "${APPDIR}/usr/lib/python${PYVER}" "${APPDIR}/usr/share/wallpaper-changer"
mkdir -p "${DIST}"

# 1) 复制标准库与解释器（AppImage 自带运行时，不依赖目标机 Python 版本）
cp -a "${PY_PREFIX}/lib/python${PYVER}/." "${APPDIR}/usr/lib/python${PYVER}/"
cp -a "${PY_BIN}" "${APPDIR}/usr/bin/python${PYVER}"
ln -sf "python${PYVER}" "${APPDIR}/usr/bin/python3"

# 2) 安装依赖到 AppDir
#    使用 PySide6-Essentials（不含 QtWebEngine/3D 等），可让体积减少约一半
echo "==> 安装依赖到 AppDir"
REQ="${ROOT}/packaging/linux/requirements-appimage.txt"
[ -f "${REQ}" ] || REQ="${ROOT}/requirements.txt"
python3 -m pip install --disable-pip-version-check --no-cache-dir \
    --target "${APPDIR}/usr/lib/python${PYVER}/site-packages" \
    -r "${REQ}"

# 3) 应用代码
APP_SHARE="${APPDIR}/usr/share/wallpaper-changer"
cp -r "${ROOT}/wallpaper_changer" "${APP_SHARE}/"
cp "${ROOT}/main.py" "${APP_SHARE}/"
cp -r "${ROOT}/assets" "${APP_SHARE}/"
# 仅清理应用自身目录，避免遍历庞大的 site-packages 拖慢构建
find "${APP_SHARE}" -name "__pycache__" -type d -prune -exec rm -rf {} +
rm -rf "${APP_SHARE}/tests"

# 4) 桌面文件与图标
cp "${ROOT}/packaging/linux/wallpaper-changer.desktop" "${APPDIR}/wallpaper-changer.desktop"
cp "${ROOT}/assets/icon.png" "${APPDIR}/wallpaper-changer.png"
ln -sf "wallpaper-changer.png" "${APPDIR}/.DirIcon"

# 5) AppRun
cat > "${APPDIR}/AppRun" <<EOF
#!/bin/sh
HERE="\$(dirname "\$(readlink -f "\$0")")"
PY="\${HERE}/usr/bin/python${PYVER}"
SITE="\${HERE}/usr/lib/python${PYVER}/site-packages"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONNOUSERSITE=1
export QT_PLUGIN_PATH="\${SITE}/PySide6/Qt/plugins"
export QT_QPA_PLATFORM_PLUGIN_PATH="\${QT_PLUGIN_PATH}/platforms"
export LD_LIBRARY_PATH="\${SITE}/PySide6/Qt/lib:\${LD_LIBRARY_PATH:-}"

# 首选 PYTHONHOME 模式；若解释器无法初始化则退回 PYTHONPATH 模式
export PYTHONHOME="\${HERE}/usr"
if ! "\${PY}" -c "import sys" >/dev/null 2>&1; then
    unset PYTHONHOME
    export PYTHONPATH="\${HERE}/usr/lib/python${PYVER}:\${SITE}"
    if ! "\${PY}" -c "import sys" >/dev/null 2>&1; then
        unset PYTHONPATH
        PY="\$(command -v python3 || echo python3)"
    fi
fi

exec "\${PY}" "\${HERE}/usr/share/wallpaper-changer/main.py" "\$@"
EOF
chmod +x "${APPDIR}/AppRun"
chmod +x "${APPDIR}/usr/bin/python${PYVER}" 2>/dev/null || true

echo "==> AppDir 就绪: ${APPDIR}"
if [ "${DIR_ONLY}" = "1" ]; then
    echo "仅生成 AppDir（--dir-only），跳过 AppImage 打包"
    exit 0
fi

# 6) 下载 appimagetool 并打包
TOOL="${ROOT}/build/appimagetool"
if [ ! -f "${TOOL}" ]; then
    echo "==> 下载 appimagetool"
    mkdir -p "${ROOT}/build"
    curl -fsSL -o "${TOOL}" \
        "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage" \
        || { echo "下载失败，请手动下载 appimagetool 放到 ${TOOL}" ; exit 1; }
    chmod +x "${TOOL}"
fi

ARCH="$(uname -m)"
cd "${ROOT}/build"
APPIMAGE="${DIST}/Wallpaper-Changer-${VERSION}-${ARCH}.AppImage"
"${TOOL}" --appimage-extract-and-run "${APPDIR}" "${APPIMAGE}" 2>/dev/null \
    || ARCH="${ARCH}" "${TOOL}" "${APPDIR}" "${APPIMAGE}"
chmod +x "${APPIMAGE}"
echo "已生成: ${APPIMAGE}"
echo "运行:   ${APPIMAGE} --show"
