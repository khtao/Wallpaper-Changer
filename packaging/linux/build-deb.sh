#!/usr/bin/env bash
# 生成 wallpaper-changer_<version>_all.deb（纯 Python，架构无关）
#
# 用法:
#   bash packaging/linux/build-deb.sh                 # 轻量包，依赖系统 Python 环境
#   bash packaging/linux/build-deb.sh 1.0.0 --bundle  # 自包含包，把依赖打进 /opt
#
# 依赖:  dpkg-deb (dpkg 自带)；--bundle 模式额外需要 pip
#
# 设计要点：
#   PySide6 在很多机器上是通过 pip 安装的，而 deb 若硬依赖 apt 包名
#   （如 python3-pyside6.qtwidgets，位于 universe 仓库），
#   安装器会直接报「依赖关系不满足」而拒绝安装。
#   因此这里：
#     1) Depends 只保留真正必须的 python3，保证 deb 一定能装；
#     2) 其余依赖经探测后写入 Recommends（apt 默认会自动装，缺失不阻断）；
#     3) 运行期由启动器逐项检查并给出精确的安装命令；
#     4) --bundle 模式把依赖打进包内，彻底摆脱系统包依赖。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VERSION=""
BUNDLE=0
for arg in "$@"; do
    case "${arg}" in
        --bundle|-b) BUNDLE=1 ;;
        -*) echo "未知参数：${arg}" >&2; exit 2 ;;
        *)  VERSION="${arg}" ;;
    esac
done
VERSION="${VERSION:-1.0.0}"
PKG_NAME="wallpaper-changer"
DIST="${ROOT}/dist"
STAGE="${PKG_NAME}_${VERSION}_all"

echo "== 构建 ${STAGE}.deb =="
echo "模式：$([ "${BUNDLE}" -eq 1 ] && echo '自包含（bundle）' || echo '轻量（依赖系统环境）')"

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

# 3) 自包含依赖（可选）
if [ "${BUNDLE}" -eq 1 ]; then
    echo "-- 打包依赖到 /opt/${PKG_NAME}/vendor ..."
    VENDOR="${WORK}/opt/${PKG_NAME}/vendor"
    PIP=""
    for cand in pip3 pip python3; do
        if command -v "${cand}" >/dev/null 2>&1; then
            if [ "${cand}" = "python3" ]; then
                PIP="python3 -m pip"
            else
                PIP="${cand}"
            fi
            break
        fi
    done
    if [ -z "${PIP}" ]; then
        echo "错误：未找到 pip，无法构建自包含包" >&2
        exit 1
    fi
    # --break-system-packages：Ubuntu 23.04+ 的 PEP 668 环境下必须带，
    # 这里是装到独立的 vendor 目录，不会影响系统 Python。
    # 旧版 pip（<23.0）没有这个选项，需自动跳过，否则直接报「no such option」。
    BXSP=""
    if [ -n "${PIP_FLAGS:-}" ]; then
        BXSP="${PIP_FLAGS}"
    elif ${PIP} install --help 2>/dev/null | grep -q -- "--break-system-packages"; then
        BXSP="--break-system-packages"
    fi
    echo "   pip 选项：${BXSP:-（无）}"
    ${PIP} install --quiet --target "${VENDOR}" \
        --upgrade ${BXSP} \
        PySide6-Essentials requests Pillow 2>&1 | tail -3 || {
        echo "错误：依赖打包失败（可尝试设置 PIP_FLAGS=--break-system-packages）" >&2
        exit 1
    }
    # 清掉体积大头：Qt 的 qml / 翻译 / 文档，本应用不需要
    rm -rf "${VENDOR}"/PySide6/Qt/qml 2>/dev/null || true
    rm -rf "${VENDOR}"/PySide6/Qt/translations 2>/dev/null || true
    find "${VENDOR}" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
    echo "   依赖已打包，体积：$(du -sh "${VENDOR}" 2>/dev/null | cut -f1)"
fi

# 4) 启动器
cat > "${WORK}/usr/bin/${PKG_NAME}" <<'LAUNCHER_EOF'
#!/usr/bin/env bash
# wallpaper-changer 启动器（deb 安装版）
#
# 设计目标：从菜单/双击启动时没有终端，任何失败都必须可见。
#   - 优先使用包内自带的依赖（vendor/，自包含版）
#   - 逐项检查依赖，给出精确的安装命令
#   - 失败信息写入 ~/.local/share/wallpaper-changer/launcher.log
#   - 有 zenity / notify-send 时弹出提示，否则至少打印到 stderr
set -uo pipefail

APP_DIR="/opt/wallpaper-changer"
APP_MAIN="${APP_DIR}/main.py"
VENDOR="${APP_DIR}/vendor"
DATA_DIR="${XDG_DATA_HOME:-${HOME:-/tmp}/.local/share}/wallpaper-changer"
LOG="${DATA_DIR}/launcher.log"

mkdir -p "${DATA_DIR}" 2>/dev/null || true

log_line() { printf '%s %s\n' "$(date '+%F %T')" "$*" >>"${LOG}" 2>/dev/null || true; }

report() {
    log_line "ERROR: $*"
    printf '%s\n' "$*" >&2
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title="Wallpaper Changer" --text="$*" --no-wrap >/dev/null 2>&1 &
    elif command -v notify-send >/dev/null 2>&1; then
        notify-send -i wallpaper-changer "Wallpaper Changer" "$*" >/dev/null 2>&1 &
    fi
}

if [ ! -f "${APP_MAIN}" ]; then
    report "程序文件缺失：${APP_MAIN}
请重新安装：sudo apt install --reinstall wallpaper-changer"
    exit 1
fi

# 0) 自包含模式：优先用包内依赖，完全不依赖系统包
if [ -d "${VENDOR}" ]; then
    export PYTHONPATH="${VENDOR}${PYTHONPATH:+:${PYTHONPATH}}"
    log_line "使用包内依赖：${VENDOR}"
fi

# 1) 找一个能 import PySide6 的解释器
#    注意：python3.x 的候选按从新到旧排列，覆盖多版本共存的系统
PY=""
for cand in "${WALLPAPER_CHANGER_PYTHON:-}" python3 python3.14 python3.13 \
            python3.12 python3.11 python3.10 python3.9; do
    [ -n "${cand}" ] || continue
    command -v "${cand}" >/dev/null 2>&1 || continue
    if "${cand}" -c 'import PySide6.QtWidgets, PySide6.QtGui, PySide6.QtCore, PySide6.QtNetwork' \
        >/dev/null 2>&1; then
        PY="${cand}"
        break
    fi
done

if [ -z "${PY}" ]; then
    report "未找到可用的 Python + PySide6。请任选一种方式安装依赖：

A. 用 apt（需启用 universe 仓库）：
   sudo add-apt-repository universe && sudo apt update
   sudo apt install python3-pyside6.qtwidgets python3-pyside6.qtnetwork python3-requests python3-pil

B. 用 pip（不依赖发行版仓库）：
   pip install --break-system-packages -r ${APP_DIR}/requirements.txt
   或：sudo apt install python3-pip 后
   python3 -m pip install --break-system-packages PySide6 requests Pillow

安装后可执行 wallpaper-changer --doctor 复核。"
    exit 1
fi

# 2) 其余依赖逐项检查，缺哪个说哪个
missing=""
for mod in requests PIL; do
    if ! "${PY}" -c "import ${mod}" >/dev/null 2>&1; then
        missing="${missing} ${mod}"
    fi
done
if [ -n "${missing}" ]; then
    report "缺少 Python 模块：${missing}
请执行：
sudo apt install python3-requests python3-pil
或：pip install --break-system-packages requests Pillow"
    exit 1
fi

# 3) 启动；非正常退出时把错误留档并提示
log_line "启动：${PY} ${APP_MAIN} $*"
err="$(mktemp 2>/dev/null || echo /tmp/wc-launcher-err.$$)"
"${PY}" "${APP_MAIN}" "$@" 2>"${err}"
code=$?
if [ "${code}" -ne 0 ]; then
    log_line "退出码 ${code}"
    {
        echo "---- 错误输出 ----"
        cat "${err}"
    } >>"${LOG}" 2>/dev/null || true
    cat "${err}" >&2
    report "启动失败（退出码 ${code}）。
详情见 ${LOG}
可执行 wallpaper-changer --doctor 做环境自检。"
fi
rm -f "${err}"
exit "${code}"
LAUNCHER_EOF
chmod 0755 "${WORK}/usr/bin/${PKG_NAME}"

# 5) 依赖探测：只把系统里真实存在的包名写进 Recommends
#    （缺失不会阻断安装，apt 默认仍会自动装上可用的）
probe_pkg() {
    # $1: 候选包名列表（空格分隔）；输出第一个真实存在的包名
    local p
    for p in $1; do
        if apt-cache show "${p}" >/dev/null 2>&1; then
            echo "${p}"
            return 0
        fi
    done
    return 1
}

RECOMMENDS=""
if [ "${BUNDLE}" -eq 0 ]; then
    QTWIDGETS="$(probe_pkg "python3-pyside6.qtwidgets python3-pyside6.qtbase python3-pyside6" || true)"
    QTNETWORK="$(probe_pkg "python3-pyside6.qtnetwork" || true)"
    REQ="$(probe_pkg "python3-requests" || true)"
    PIL="$(probe_pkg "python3-pil python3-pil.imagetk" || true)"
    for p in "${QTWIDGETS}" "${QTNETWORK}" "${REQ}" "${PIL}"; do
        [ -n "${p}" ] && RECOMMENDS="${RECOMMENDS}${RECOMMENDS:+, }${p}"
    done
    [ -n "${RECOMMENDS}" ] && echo "探测到的可选依赖：${RECOMMENDS}" \
        || echo "提示：系统 apt 缓存中未找到 PySide6 包，将以无推荐依赖方式打包"
fi

# 6) 控制文件
{
    echo "Package: ${PKG_NAME}"
    echo "Version: ${VERSION}"
    echo "Section: utils"
    echo "Priority: optional"
    echo "Architecture: all"
    # 关键：Depends 只放 python3。
    # 把 PySide6 写成硬依赖会让 pip 安装的用户无法安装本包。
    echo "Depends: python3 (>= 3.9)"
    if [ -n "${RECOMMENDS}" ]; then
        echo "Recommends: ${RECOMMENDS}"
    fi
    if [ "${BUNDLE}" -eq 1 ]; then
        echo "Provides: ${PKG_NAME}-bundle"
    fi
    echo "Maintainer: Wallpaper Changer <noreply@example.com>"
    echo "Description: 跨平台自动更换桌面壁纸工具"
    echo " 支持 Bing 每日图、本地文件夹与自定义链接（直链 / JSON 接口）作为图片来源，"
    echo " 可定时轮换、收藏/历史管理、系统托盘常驻与开机自启动。"
    if [ "${BUNDLE}" -eq 1 ]; then
        echo " 本包为自包含版本，已内置 PySide6 等依赖，不依赖系统 Qt 包。"
    fi
    echo "Homepage: https://github.com/example/wallpaper-changer"
} > "${WORK}/DEBIAN/control"

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
echo "体积:   $(du -h "${DIST}/${STAGE}.deb" | cut -f1)"
echo "安装:   sudo apt install ./${DIST}/${STAGE}.deb"
if [ "${BUNDLE}" -eq 0 ]; then
    echo "提示:   若安装后启动报缺依赖，可改用自包含包："
    echo "        bash packaging/linux/build-deb.sh ${VERSION} --bundle"
fi
