; Wallpaper Changer —— Inno Setup 安装脚本
; 用法: ISCC /DMyAppVersion=1.0.0 installer.iss
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

#define MyAppName "Wallpaper Changer"
#define MyAppPublisher "Wallpaper Changer"
#define MyAppURL "https://github.com/example/wallpaper-changer"
#define MyAppExeName "Wallpaper Changer.exe"
#define MyBuildDir "dist\Wallpaper Changer"

[Setup]
AppId={{8F2C1A45-6D3B-4E7A-9C21-5B8D4F1E7A30}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
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
PrivilegesRequiredOverridesAllowed=dialog
; 允许无需管理员安装到用户目录
PrivilegesRequired=lowest
ChangesAssociations=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"; Flags: unchecked
Name: "startup"; Description: "开机自动启动（最小化到托盘）"; GroupDescription: "启动选项:"; Flags: checkedonce

[Files]
Source: "{#MyBuildDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; \
      Parameters: "--minimized"; Tasks: startup
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即启动 {#MyAppName}"; \
          Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{userstartup}\{#MyAppName}.lnk"
Type: filesandordirs; Name: "{userappdata}\WallpaperChanger"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  { 卸载前结束正在运行的进程，避免文件被占用 }
  if CurUninstallStep = usUninstall then
  begin
    ShellExec('open', 'taskkill.exe', '/F /IM "Wallpaper Changer.exe" /T',
              '', SW_HIDE, ewNoWait, ResultCode);
  end;
end;
