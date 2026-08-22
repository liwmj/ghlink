; Inno Setup 安装器脚本: ghlink Windows（v0.2.0）
; 构建: iscc packaging/windows/ghlink.iss
; 参考: v0.2 安装包技术方案草案（exe 线：PyInstaller + Inno 安装向导）
; 特性: 图形安装向导 + Program Files + PATH + 开始菜单 + 卸载项 + 开机自启选项（默认勾选）

#define MyAppName "ghlink"
#define MyAppVersion "0.4.3"
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
SetupIconFile=..\..\assets\ghlink-icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}

[Languages]
; 中文语言文件随仓库自带（packaging/windows/ChineseSimplified.isl），不依赖 runner 内置
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Files]
; 主程序 + 配置文件 + 说明文档（PyInstaller 产物 dist/windows/ghlink.exe）
Source: "..\..\dist\windows\ghlink.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\config.example.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\windows\ghlink-tray.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\dist\windows\ghlink-watch.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\assets\ghlink-icon.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\ghlink"; Filename: "{app}\ghlink-tray.exe"; IconFilename: "{app}\ghlink-icon.ico"
Name: "{group}\ghlink 使用说明"; Filename: "{app}\README.md"
Name: "{autodesktop}\ghlink"; Filename: "{app}\ghlink-tray.exe"; IconFilename: "{app}\ghlink-icon.ico"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加任务:"
; 李工 15:29 定规：默认不自启（不勾选），用户通过托盘右键开关或 ghlink enable 开启
Name: "autostart"; Description: "开机自动启动托盘并启用值守（ghlink enable，需勾选才自启）"; GroupDescription: "附加任务:"; Flags: unchecked

[Registry]
; 注册 ghlink 到 PATH（用户级）
; 注意：不能用 uninsdeletevalue —— 那会在卸载时清空整个用户 PATH（小爪 W3 watch 点②）
; 卸载时改由 [UninstallRun] powershell 精确摘除 {app} 段
Root: HKCU; Subkey: "Environment"; ValueType: string; ValueName: "Path"; \
  ValueData: "{olddata};{app}"; Flags: preservestringtype

; 托盘开机自启（用户级 Run key，随登录启动作 UI 载体）
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "ghlink-tray"; \
  ValueData: """{app}\ghlink-tray.exe"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
; 安装时预注册 SYSTEM 值守任务（默认 disabled，无窗口 ghlink-watch.exe）——
; v0.4.2（李工 8 条⑤）：与 enable 对齐 /SC HOURLY（原 /SC MINUTE /MO 1 与
; enable 的 HOURLY 不一致，且 /TR 未带 config 参数 → 托盘「值守未运行」无法定位）
; 方案 A（李工 13:44/13:50 定调）：托盘=值守总开关，安装预注册后托盘启动只需启停 UAC
Filename: "{cmd}"; Parameters: "/C schtasks /Create /TN ghlink /SC HOURLY /TR ""{app}\ghlink-watch.exe"" ""%USERPROFILE%\.ghlink\config.json"" /RL HIGHEST /RU SYSTEM /DISABLE /F";   Description: "预注册 ghlink 值守任务（默认停用 /DISABLE，托盘启动或勾选自启才启用）"; Flags: runhidden nowait
; 安装后：勾选自启则启用值守（schtasks）
Filename: "{app}\{#MyAppExeName}"; Parameters: "enable"; \
  Description: "启用 ghlink 值守（开机自启）"; Flags: nowait postinstall skipifsilent; Tasks: autostart
; 启动托盘
Filename: "{app}\ghlink-tray.exe"; \
  Description: "启动 ghlink 托盘"; Flags: nowait postinstall skipifsilent
; 可选：查看状态
Filename: "{app}\{#MyAppExeName}"; Parameters: "status"; \
  Description: "查看 ghlink 状态"; Flags: nowait postinstall skipifsilent

[InstallDelete]
; v0.4.2（李工 2026-08-22 定）：换版本删旧配置——升级安装时删除旧版本遗留配置/状态，
; 避免旧字段不兼容导致新版本行为异常（旧 v0.2.19.x 曾因平台无效路径字段出问题）
; 注意：{userprofile} 常量部分 Inno 版本不支持，改由 [Code] CurStepChanged 处理
Name: "{commonappdata}\ghlink"; Type: filesandordirs

[UninstallRun]
; 卸载前停用值守（清理计划任务）与托盘自启项——v0.4.2（李工 19:31 终裁）：
; uninstall = 停任务 + 还原 hosts + 删配置（disable 只保留，卸载必须删干净）
Filename: "{app}\{#MyAppExeName}"; Parameters: "uninstall"; \
  Flags: runhidden; RunOnceId: "ghlink-uninstall"
; 注意：用户 PATH 摘除在 [Code] CurUninstallStepChanged 中处理（Pascal 直接读写注册表，避免 shell 引号转义坑）

[UninstallDelete]
; v0.4.2（李工 2026-08-22 定）：卸载必须删除旧配置（config/状态/缓存/pid 全清），
; 不留残余——换版本/卸载都删配置，保证干净环境；{userprofile} 路径由 [Code] 处理
Name: "{commonappdata}\ghlink"; Type: filesandordirs

[Code]
// 安装/升级后删除旧版本用户级配置（换版本删旧配置，李工 2026-08-22 定；
// {userprofile} 常量部分 Inno 版本不支持 → GetEnv 拼接）
procedure CurStepChanged(CurStep: TSetupStep);
var
  UserProfile: string;
begin
  if CurStep = ssPostInstall then
  begin
    UserProfile := GetEnv('USERPROFILE');
    if UserProfile <> '' then
      DelTree(UserProfile + '\.ghlink', True, True, True);
  end;
end;

// 卸载时确认提示（静默卸载 UninstallSilent 时不弹，否则阻塞自动化/CI）
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  PathValue: string;
  AppPath: string;
  UserProfile: string;
begin
  if CurUninstallStep = usUninstall then
  begin
    if (not UninstallSilent) and
      (MsgBox('确定卸载 ghlink 吗？配置文件和状态文件将被一并删除。',
        mbConfirmation, MB_YESNO) = IDNO) then
      Abort;
    // v0.4.2（李工 2026-08-22 定）：卸载删除用户级配置目录 %USERPROFILE%\.ghlink
    UserProfile := GetEnv('USERPROFILE');
    if UserProfile <> '' then
      DelTree(UserProfile + '\.ghlink', True, True, True);
    // 精确摘除用户 PATH 中的 {app} 段（不整值清空，防 uninsdeletevalue 误删）
    AppPath := ExpandConstant('{app}');
    if RegQueryStringValue(HKCU, 'Environment', 'Path', PathValue) then
    begin
      // 先摘带分号的前缀/后缀形式，再摘裸路径，最后清理残留空段与首尾分号
      StringChangeEx(PathValue, AppPath + ';', '', True);
      StringChangeEx(PathValue, ';' + AppPath, '', True);
      StringChangeEx(PathValue, AppPath, '', True);
      while Pos(';;', PathValue) > 0 do
        StringChangeEx(PathValue, ';;', ';', True);
      if (Length(PathValue) > 0) and (PathValue[1] = ';') then
        Delete(PathValue, 1, 1);
      if (Length(PathValue) > 0) and (PathValue[Length(PathValue)] = ';') then
        Delete(PathValue, Length(PathValue), 1);
      RegWriteStringValue(HKCU, 'Environment', 'Path', PathValue);
    end;
  end;
end;
