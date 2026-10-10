# ============================================================
#  Wallpaper Changer —— Windows 构建脚本（PowerShell 版）
#
#  用法（在项目根目录）：
#     .\packaging\windows\build.ps1
#     .\packaging\windows\build.ps1 -Version 1.1.3
#     .\packaging\windows\build.ps1 -Version 1.1.3 -OneFile
#     .\packaging\windows\build.ps1 -Console          # 带控制台窗口，排错用
#     .\packaging\windows\build.ps1 -SkipInstaller    # 只出程序目录
#
#  比 build.bat 的好处：UTF-8 中文不乱码、可传参、出错即停。
# ============================================================
param(
    [string]$Version = "1.1.3",
    [switch]$OneFile,
    [switch]$Console,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $RepoRoot

function Say($msg, $color = "Gray") { Write-Host $msg -ForegroundColor $color }
function Step($msg) { Write-Host ""; Write-Host "=== $msg ===" -ForegroundColor Cyan }

Step "Wallpaper Changer 构建 v$Version"

# ---------- [1/5] 环境 ----------
Step "[1/5] 检查环境"
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { Say "未找到 python，请先安装 Python 3.9+ 并勾选 Add to PATH" "Red"; exit 1 }
$pyver = & python -c "import sys;print('%d.%d.%d'%sys.version_info[:3])"
if ([version]$pyver -lt [version]"3.9") { Say "Python 版本过低：$pyver（需要 3.9+）" "Red"; exit 1 }
Say "  Python $pyver" "Green"

# ---------- [2/5] 依赖 ----------
Step "[2/5] 检查依赖"
$need = & python -c "import PySide6, requests, PIL; print('ok')" 2>$null
if ($need -ne "ok") {
    Say "  安装运行时依赖..." "Yellow"
    & python -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Say "依赖安装失败" "Red"; exit 1 }
}
$hasPyi = & python -c "import PyInstaller; print('ok')" 2>$null
if ($hasPyi -ne "ok") {
    Say "  安装 PyInstaller..." "Yellow"
    & python -m pip install --upgrade pyinstaller
    if ($LASTEXITCODE -ne 0) { Say "PyInstaller 安装失败" "Red"; exit 1 }
}
Say "  依赖就绪" "Green"

# ---------- [3/5] 清理 ----------
Step "[3/5] 清理旧产物"
if (Test-Path build) { Remove-Item -Recurse -Force build }
if (Test-Path dist)  { Remove-Item -Recurse -Force dist }

# ---------- [4/5] 打包 ----------
Step "[4/5] PyInstaller 打包"
$env:WC_VERSION = $Version
$env:WC_ONEFILE = if ($OneFile) { "1" } else { "0" }
$env:WC_CONSOLE = if ($Console) { "1" } else { "0" }

& python -m PyInstaller packaging\windows\wallpaper-changer.spec --noconfirm
if ($LASTEXITCODE -ne 0) { Say "打包失败" "Red"; exit 1 }

$exe = "dist\Wallpaper Changer\Wallpaper Changer.exe"
if (-not (Test-Path $exe)) { Say "未找到生成的 exe：$exe" "Red"; exit 1 }
$size = [math]::Round((Get-ChildItem -Recurse "dist\Wallpaper Changer" |
          Measure-Object -Property Length -Sum).Sum / 1MB, 1)
Say "  已生成 $exe（目录共 $size MB）" "Green"

# ---------- [5/5] 安装器 ----------
if ($SkipInstaller) {
    Step "[5/5] 跳过安装器（-SkipInstaller）"
} else {
    Step "[5/5] 生成安装器"
    $iscc = @(
        "$env:ProgramFiles(x86)\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

    if ($iscc) {
        & $iscc "/DMyAppVersion=$Version" "packaging\windows\installer.iss"
        if ($LASTEXITCODE -eq 0) {
            Say "  安装器: dist\WallpaperChanger-$Version-Setup.exe" "Green"
        } else {
            Say "  安装器生成失败，程序目录仍可分发" "Yellow"
        }
    } else {
        Say "  未检测到 Inno Setup 6，跳过安装器" "Yellow"
        Say "  可直接分发目录: dist\Wallpaper Changer\" "Yellow"
        Say "  需要安装器请装 Inno Setup 6: https://jrsoftware.org/isdl.php" "Yellow"
    }
}

# ---------- 完成 ----------
Write-Host ""
Write-Host "=== 构建完成 ===" -ForegroundColor Cyan
Write-Host "  版本    : $Version"
Write-Host "  程序目录: dist\Wallpaper Changer\"
if (Test-Path "dist\WallpaperChanger-$Version-Setup.exe") {
    Write-Host "  安装器  : dist\WallpaperChanger-$Version-Setup.exe"
}
Write-Host ""
Write-Host "排错提示：程序闪退时加 -Console 重新打包，可看到报错信息。" -ForegroundColor DarkGray
