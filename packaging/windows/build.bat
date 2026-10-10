@echo off
REM ============================================================
REM  Wallpaper Changer —— Windows 构建脚本
REM
REM  用法（在项目根目录）：
REM      packaging\windows\build.bat              REM 用默认版本
REM      packaging\windows\build.bat 1.1.3        REM 指定版本
REM      packaging\windows\build.bat 1.1.3 one    REM 打成单文件 exe
REM
REM  前置：Python 3.9+ 已安装并加入 PATH
REM        pip install -r requirements.txt pyinstaller
REM        需要安装器时：安装 Inno Setup 6 https://jrsoftware.org/isdl.php
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0\..\.."

REM --- 参数 ---
set VERSION=1.1.3
if not "%~1"=="" set VERSION=%~1
if /i "%~2"=="one" (set WC_ONEFILE=1) else (set WC_ONEFILE=0)

echo.
echo === Wallpaper Changer 构建 v%VERSION% ===
echo.

REM --- [1/5] 环境检查 ---
echo === [1/5] 检查环境 ===
where python >nul 2>&1 || (
    echo [错误] 未找到 python，请先安装 Python 3.9+ 并勾选 "Add to PATH"
    exit /b 1
)
python -c "import sys; sys.exit(0 if sys.version_info>=(3,9) else 1)" || (
    echo [错误] Python 版本过低，需要 3.9+
    exit /b 1
)
for /f "tokens=*" %%v in ('python -c "import sys;print(sys.version.split()[0])"') do set PYVER=%%v
echo   Python %PYVER%

REM --- [2/5] 依赖 ---
echo === [2/5] 检查依赖 ===
python -c "import PySide6, requests, PIL" 2>nul || (
    echo   正在安装运行时依赖...
    python -m pip install -r requirements.txt || (
        echo [错误] 依赖安装失败
        exit /b 1
    )
)
python -c "import PyInstaller" 2>nul || (
    echo   正在安装 PyInstaller...
    python -m pip install --upgrade pyinstaller || (
        echo [错误] PyInstaller 安装失败
        exit /b 1
    )
)
echo   依赖就绪

REM --- [3/5] 清理 ---
echo === [3/5] 清理旧产物 ===
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM --- [4/5] 打包 ---
echo === [4/5] PyInstaller 打包 ===
set WC_VERSION=%VERSION%
pyinstaller packaging\windows\wallpaper-changer.spec --noconfirm || (
    echo [错误] 打包失败
    exit /b 1
)
if not exist "dist\Wallpaper Changer\Wallpaper Changer.exe" (
    echo [错误] 未找到生成的 exe
    exit /b 1
)
echo   已生成: dist\Wallpaper Changer\Wallpaper Changer.exe

REM --- [5/5] 安装器 ---
echo === [5/5] 生成安装器 ===
set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

if defined ISCC (
    "%ISCC%" /DMyAppVersion=%VERSION% "packaging\windows\installer.iss" || (
        echo [警告] 安装器生成失败，程序目录仍可分发
        goto :done
    )
    echo   安装器: dist\WallpaperChanger-%VERSION%-Setup.exe
) else (
    echo   未检测到 Inno Setup 6，跳过安装器。
    echo   可直接分发整个目录: dist\Wallpaper Changer\
    echo   需要安装器请装 Inno Setup 6 后重跑: https://jrsoftware.org/isdl.php
)

:done
echo.
echo === 构建完成 ===
echo   版本    : %VERSION%
echo   程序目录: dist\Wallpaper Changer\
if exist "dist\WallpaperChanger-%VERSION%-Setup.exe" echo   安装器  : dist\WallpaperChanger-%VERSION%-Setup.exe
echo.
exit /b 0
