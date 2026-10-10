; Wallpaper Changer —— Inno Setup 安装脚本
; 用法: ISCC /DMyAppVersion=1.1.3 installer.iss
;       （build.bat / build.ps1 会自动传版本号）
#ifndef MyAppVersion
  #define MyAppVersion "1.1.3"
#endif

#define MyAppName "Wallpaper Changer"
#define MyAppPublisher "Wallpaper Changer"
#define MyAppExeName "Wallpaper Changer.exe"
#define MyBuildDir "dist\Wallpaper Changer"

[Setup]
AppId={{8F2C1A45-6D3B-4E7A-9C21-5B8D4F1E7A30}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
; 装到当前用户目录，无需管理员权限（壁纸应用属个人软件，够用且不会弹 UAC）
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
LicenseFile=
OutputDir=dist
OutputBaseFilename=WallpaperChanger-{#MyAppVersion}-Setup
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
; 不需要管理员：装到 %LOCALAPPDATA%\Programs 下
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ChangesAssociations=no
; 单实例：避免重复安装时启动多个安装器
SetupMutex=WallpaperChangerSetupMutex
; 覆盖旧版本文件
Uninstallable=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"; Flags: unchecked
Name: "startup"; Description: "开机自动启动（最小化到托盘）"; GroupDescription: "启动选项:"; Flags: checkedonce

[Files]
Source: "{#MyBuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
; 开机启动走「启动」文件夹（HKCU Run 也可，但文件夹方式用户可见、易管理）
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
      Parameters: "--minimized"; Tasks: startup
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; \
          Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{userstartup}\{#MyAppName}.lnk"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

{ 卸载前结束正在运行的进程，避免 exe / dll 被占用导致删不掉 }
function IsAppRunning(): Boolean;
var
  ResultCode: Integer;
begin
  Result := False;
  if ShellExec('open', 'taskkill.exe', '/F /IM "{#MyAppExeName}" /T',
               '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Result := (ResultCode = 0);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    { 先结束进程，否则 exe / dll 被占用删不掉 }
    IsAppRunning();

    { 数据在 %APPDATA%\WallpaperChanger（缓存、历史、配置、日志）。
      重装时往往希望保留，所以这里询问，而不是默默删掉。 }
    DataDir := ExpandConstant('{userappdata}\WallpaperChanger');
    if DirExists(DataDir) then
    begin
      if MsgBox('是否删除本机的配置、壁纸缓存和历史记录？' + #13#10 + #13#10 +
                DataDir + #13#10 + #13#10 +
                '选择「否」将保留这些数据（方便以后重新安装时继续使用）。',
                mbConfirmation, MB_YESNO) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
      end;
    end;
  end;
end;
