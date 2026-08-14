; Inno Setup 安装器脚本: ghlink Windows（v0.2.0）
; 构建: iscc packaging/windows/ghlink.iss
; 参考: v0.2 安装包技术方案草案（exe 线：PyInstaller + Inno 安装向导）
; 特性: 图形安装向导 + Program Files + PATH + 开始菜单 + 卸载项 + 开机自启选项（默认勾选）

#define MyAppName "ghlink"
#define MyAppVersion "0.2.0"
#define MyAppPublisher "Mason Lee"
#define MyAppExeName "ghlink.exe"
#define MyAppId "{{B7E3C9A1-2F4D-4A6E-9C81-0D1F2B3C4D5E}"

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
; 中文语言文件随仓库自带（packaging/windows/ChineseSimplified.isl），不依赖 runner 内置
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

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
Name: "autostart"; Description: "开机自动启用值守（ghlink enable，推荐）"; GroupDescription: "附加任务:"; Flags: checkedonce

[Registry]
; 注册 ghlink 到 PATH（用户级）
; 注意：不能用 uninsdeletevalue —— 那会在卸载时清空整个用户 PATH（小爪 W3 watch 点②）
; 卸载时改由 [UninstallRun] powershell 精确摘除 {app} 段
Root: HKCU; Subkey: "Environment"; ValueType: string; ValueName: "Path"; \
  ValueData: "{olddata};{app}"; Flags: preservestringtype

; 托盘开机自启（用户级 Run key，随登录启动作 UI 载体）
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ghlink-tray"; \
  ValueData: """{app}\ghlink.exe"" tray"; Flags: uninsdeletevalue; Tasks: autostart

[Run]
; 安装后：勾选自启则注册值守（schtasks）
Filename: "{app}\{#MyAppExeName}"; Parameters: "enable"; \
  Description: "启用 ghlink 值守（开机自启）"; Flags: nowait postinstall skipifsilent; Tasks: autostart
; 启动托盘
Filename: "{app}\{#MyAppExeName}"; Parameters: "tray"; \
  Description: "启动 ghlink 托盘"; Flags: nowait postinstall skipifsilent
; 可选：查看状态
Filename: "{app}\{#MyAppExeName}"; Parameters: "status"; \
  Description: "查看 ghlink 状态"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; 卸载前停用值守（清理计划任务）与托盘自启项
Filename: "{app}\{#MyAppExeName}"; Parameters: "disable"; \
  Flags: runhidden; RunOnceId: "ghlink-disable"
; 精确摘除用户 PATH 中的 {app} 段（不整值清空）
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command \"$p=[Environment]::GetEnvironmentVariable('Path','User'); if($p){$p=($p -split ';' | Where-Object {$_ -ne '{app}'}) -join ';'; [Environment]::SetEnvironmentVariable('Path',$p,'User')}\""; \
  Flags: runhidden; RunOnceId: "ghlink-unpath"

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
