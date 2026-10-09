@echo off
REM ============================================================
REM  Wallpaper Changer Windows 构建脚本
REM  1) PyInstaller 打包 -> dist\Wallpaper Changer\
REM  2) 若检测到 Inno Setup (iscc.exe)，继续生成安装器
REM
REM  前置：pip install -r requirements.txt pyinstaller
REM ============================================================
setlocal enabledelayedexpansion
cd /d "%~dp0\..\.."

set VERSION=1.0.0

echo === [1/3] 检查依赖 ===
python -c "import PySide6" 2>nul || (
    echo 未找到 PySide6，正在安装依赖...
    python -m pip install -r requirements.txt || goto :error
)
python -m pip install --upgrade pyinstaller || goto :error

echo === [2/3] PyInstaller 打包 ===
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
pyinstaller packaging\windows\wallpaper-changer.spec --noconfirm || goto :error

echo === [3/3] 生成安装器 ===
set ISCC=
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

if defined ISCC (
    "%ISCC%" /DMyAppVersion=%VERSION% "packaging\windows\installer.iss" || goto :error
    echo 安装器已生成: dist\WallpaperChanger-%VERSION%-Setup.exe
) else (
    echo.
    echo 未检测到 Inno Setup，已跳过安装器。
    echo 可直接分发目录: "dist\Wallpaper Changer"
    echo 需要安装器时请安装 Inno Setup 6 后重跑本脚本：
    echo     https://jrsoftware.org/isdl.php
)
echo.
echo 构建完成。
exit /b 0

:error
echo 构建失败，错误码 %errorlevel%
exit /b %errorlevel%
