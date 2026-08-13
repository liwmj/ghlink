; Inno Setup 安装器脚本: ghlink Windows exe（v0.2.0）
; 构建: iscc packaging/windows/ghlink.iss
; 参考: v0.2 安装包技术方案草案（exe 线：PyInstaller + Inno 安装向导）
; 特性: 图形安装向导 + Program Files + PATH + 开始菜单 + 卸载项 + 默认不自启

#define MyAppName "ghlink"
#define MyAppVersion "0.2.0"
#define MyAppPublisher "Mason Lee"
#define MyAppExeName "ghlink.exe"
#define MyAppId "{B7E3C9A1-2F4D-4A6E-9C81-0D1F2B3C4D5E}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\ghlink
DefaultGroupName=ghlink
DisableProgramGroupPage=yes
OutputDir=..\..\dist\windows
OutputBaseFilename=ghlink-setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; 主程序 + 配置文件（PyInstaller 产物 dist/windows/ghlink.exe）
Source: "..\..\dist\windows\ghlink.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\config.example.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\ghlink"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\ghlink 使用说明"; Filename: "{app}\README.md"
Name: "{autodesktop}\ghlink"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"

[Registry]
; 注册 ghlink 到 PATH（用户级）
Root: HKCU; Subkey: "Environment"; ValueType: string; ValueName: "Path"; \
  ValueData: "{olddata};{app}"; Flags: preservestringtype uninsdeletevalue

[Run]
; 安装后可选：启动 ghlink status 查看状态（不自动值守，opt-in）
Filename: "{app}\{#MyAppExeName}"; Parameters: "status"; \
  Description: "查看 ghlink 状态"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; 卸载前停用值守（清理计划任务）
Filename: "{app}\{#MyAppExeName}"; Parameters: "disable"; \
  Flags: runhidden; RunOnceId: "ghlink-disable"

[Code]
// 卸载时确认提示
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    if MsgBox('确定卸载 ghlink 吗？配置文件和状态文件将保留在 %APPDATA%\ghlink。',
      mbConfirmation, MB_YESNO) = IDNO then
      Abort;
  end;
end;
