#### W3: Windows 三路径安装验证（全新 / 覆盖升级 / 卸载清理 + enable-disable 往返）
#### 宿主：GitHub Actions windows-latest（隔离环境，不动生产服务器）
#### 断言：全新安装产物齐全 / enable-disable 往返 / 覆盖升级 config 保留 / 卸载清理干净
$ErrorActionPreference = "Stop"
$script:pass = 0; $script:fail = 0
function Check($name, $cond) {
  if ($cond) { Write-Host "PASS: $name"; $script:pass++ }
  else { Write-Host "FAIL: $name"; $script:fail++ }
}

#### artifact 可能在仓库根，也可能在 actions/download-artifact 生成的 dist-windows/ 下
$searchDirs = @(".", "dist-windows")
$installer = $null; $bareExe = $null
foreach ($d in $searchDirs) {
  if (-not $installer) { $installer = Get-ChildItem -Path $d -Filter "ghlink-installer-*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1 }
  if (-not $bareExe) { $bareExe = Get-ChildItem -Path $d -Filter "ghlink.exe" -ErrorAction SilentlyContinue | Select-Object -First 1 }
}
Check "artifact: installer present" ($null -ne $installer)
Check "artifact: bare exe present" ($null -ne $bareExe)
if ($null -eq $installer) { Write-Host "ABORT: installer missing"; exit 1 }

$appDir = "$env:ProgramFiles\ghlink"
$exe = "$appDir\ghlink.exe"
$unins = "$appDir\unins000.exe"
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$silent = @("/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART","/LOG=install.log")
$taskArgs = $silent + @("/TASKS=autostart,desktopicon")
$userPathBefore = [Environment]::GetEnvironmentVariable("Path", "User")

#### --- 裸 exe 冒烟 ---
& $bareExe.FullName --version | Out-Host
Check "bare exe --version exit 0" ($LASTEXITCODE -eq 0)

#### ========== Phase 1: 全新安装 ==========
$p = Start-Process -FilePath $installer.FullName -ArgumentList $taskArgs -Wait -PassThru
Check "fresh install: exit 0" ($p.ExitCode -eq 0)
Check "fresh install: exe deployed" (Test-Path $exe)
Check "fresh install: config.example.json" (Test-Path "$appDir\config.example.json")
Check "fresh install: uninstaller present" (Test-Path $unins)
$runVal = (Get-ItemProperty -Path $runKey -Name "ghlink-tray" -ErrorAction SilentlyContinue)
Check "fresh install: Run key ghlink-tray" ($null -ne $runVal)
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
Check "fresh install: PATH updated" ($userPath -like "*$appDir*")
& $exe status *> $null
Check "fresh install: status runs" ($LASTEXITCODE -eq 0)

#### ========== Phase 2: enable-disable 往返 ==========
& $exe enable | Out-Host
Check "enable: exit 0" ($LASTEXITCODE -eq 0)
schtasks /Query /TN ghlink *> $null
Check "enable: schtasks ghlink exists" ($LASTEXITCODE -eq 0)
& $exe disable | Out-Host
Check "disable: exit 0" ($LASTEXITCODE -eq 0)
schtasks /Query /TN ghlink *> $null
Check "disable: schtasks removed" ($LASTEXITCODE -ne 0)
& $exe enable | Out-Host
Check "re-enable: exit 0" ($LASTEXITCODE -eq 0)
schtasks /Query /TN ghlink *> $null
Check "re-enable: schtasks back" ($LASTEXITCODE -eq 0)

#### ========== Phase 3: 覆盖升级（config 保留）==========
# 用户 config 位置（非管理员）：%APPDATA%\ghlink\config.json
$userCfg = "$env:APPDATA\ghlink\config.json"
New-Item -ItemType Directory -Force -Path "$env:APPDATA\ghlink" | Out-Null
Set-Content -Path $userCfg -Value '{"user_marker":"keep-me"}' -Encoding UTF8
# 记录升级前 exe 时间戳，验证升级后 exe 被替换
$exeBefore = (Get-Item $exe).LastWriteTimeUtc
Start-Sleep -Seconds 2
$p2 = Start-Process -FilePath $installer.FullName -ArgumentList $taskArgs -Wait -PassThru
Check "upgrade: exit 0" ($p2.ExitCode -eq 0)
Check "upgrade: exe intact" (Test-Path $exe)
$exeAfter = (Get-Item $exe).LastWriteTimeUtc
Check "upgrade: exe replaced" ($exeAfter -gt $exeBefore)
Check "upgrade: user config kept" ((Test-Path $userCfg) -and ((Get-Content $userCfg -Raw) -like "*keep-me*"))

#### ========== Phase 4: 卸载清理 ==========
# 卸载前先 disable（清理 schtasks）
& $exe disable | Out-Host
schtasks /Query /TN ghlink *> $null
Check "pre-uninstall: schtasks removed" ($LASTEXITCODE -ne 0)
$p3 = Start-Process -FilePath $unins -ArgumentList @("/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART") -Wait -PassThru
Check "uninstall: exit 0" ($p3.ExitCode -eq 0)
Check "uninstall: app dir removed" (-not (Test-Path $appDir))
$runVal2 = Get-ItemProperty -Path $runKey -Name "ghlink-tray" -ErrorAction SilentlyContinue
Check "uninstall: Run key removed" ($null -eq $runVal2)
$userPathAfter = [Environment]::GetEnvironmentVariable("Path", "User")
Check "uninstall: PATH ghlink removed" ($userPathAfter -notlike "*$appDir*")
Check "uninstall: user PATH not whole-value wiped" (($null -ne $userPathAfter) -and ($userPathAfter.Length -gt 0) -and ($userPathAfter -eq $userPathBefore))
# 用户 config 保留（卸载不清用户数据）
Check "uninstall: user config preserved" (Test-Path $userCfg)

Write-Host ""
Write-Host "===== W3 结果: PASS=$($script:pass) FAIL=$($script:fail) ====="
if ($script:fail -eq 0) { Write-Host "ALL GREEN ✅" } else { Write-Host "HAS FAILURES ❌"; exit 1 }
