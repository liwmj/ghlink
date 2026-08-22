# ghlink v0.2.19 技术方案：八项修复 + 正常态接管设计变更

> 版本：v0.1 ｜ 日期：2026-08-21 ｜ 作者：赛博（技术方案）｜ 实现：顾笙 ｜ 复核：赛博
> 背景：李工 08-21 01:17 报 8 个测试问题（v0.2.18），01:25 我出根因分析；18:11 李工 8 条批注定方向：
> ①采用 brew trust 修复 ②注册完要立即跑一轮 ③正常态必须写 hosts（质疑原设计）④托盘角标要修 ⑤值守链路要优化
> ⑥GitHub520 初始化合入一次、后续只动态更新 ⑦全面优化成 8 域名 ⑧本机跑 ghlink.exe enable 抓真实报错（已执行）

## 一、本机实测证据（2026-08-21 18:12，李工批注 ⑧ 批准执行）

环境：Windows Server 2022，ghlink v0.2.18 安装于 `C:\Program Files\ghlink\`，当前 shell 为管理员/SYSTEM 上下文。

### 实测 1：`ghlink.exe version` 从非白名单 cwd 运行 → 直接崩溃
```
ValueError: lock path outside allowed dirs: C:\Users\Administrator\.openclaw\workspace\ghlink.lock
[PYI-7848:ERROR] Failed to execute script 'ghlink_entry' due to unhandled exception!
```
- **根因**：锁文件路径解析依赖 cwd（config 里 lock_file 为相对/异常路径时回退 cwd），`_safe_lock_path` 校验不通过即抛异常
- **影响**：任何命令（version/status/run）从任意非白名单目录运行都会崩，属 P0 健壮性缺陷

### 实测 2：`ghlink.exe enable` 成功（EXIT=0），但状态文件路径是 Unix 路径
```
[ghlink] 已生成默认配置: C:\Windows\system32\config\systemprofile\.ghlink\config.json
[ghlink] 已启用值守（schtasks，1 小时粒度，最高权限）
```
生成的 config.json 关键字段：
```json
"state_file": "/var/lib/ghlink/ghlink_status.json",
"lock_file": "/var/lib/ghlink/ghlink.lock",
"hosts_backup_dir": "/var/lib/ghlink/backup"
```
- **根因**：Windows 构建的默认配置模板硬编码 Linux 路径（模板共用导致），Windows 上这些路径无效
- **影响**：值守进程（ghlink-watch.exe）无法写状态文件 → 心跳恒判不新鲜 → 即使注册成功 status 也恒报「值守: 异常（疑似僵尸）」——**这就是李工 Windows 上 ⑤ 的根因**（比之前推断的路径漂移更直接）

### 实测 3：`ghlink.exe status` 复现僵尸症状
```
状态: normal ｜ 当前IP: 20.205.243.166 ｜ 失败计数: 0 ｜ 上次切换: -
值守: 异常（值守已注册但心跳已停，疑似僵尸）｜ 托盘: 未运行
```
任务已注册且启用、系统当前网络正常，仍报僵尸 → 与 ② 叠加：OnCalendar 1 小时粒度，注册后首轮要等 1 小时（下次运行 19:12），期间必然「心跳停」

### 实测 4：hosts 仍为原始基线
- 确认正常态不写 hosts 的设计行为；GitHub520 段、8 域名 IP 均未落盘

### 实测 5：enable 注册的 schtasks 详情
- 任务名 `\ghlink`，SYSTEM 身份，1 小时粒度，/TR 带 config 参数（正确），下次运行 19:12
- 已禁用该任务（避免坏配置每小时空跑），hosts 未被动

## 二、修复清单（对应李工 8 条批注）

| # | 李工批注 | 修复项 | 优先级 |
|---|---------|--------|--------|
| 1 | 采用 | brew trust：README 安装步骤补 `brew trust liwmj/ghlink`（或 --formula 精确版），tap 仓库 README 同步 | P1 |
| 2 | 注册完不跑一轮 | enable 注册后立即触发第一轮：schtasks /Run / systemctl start / launchctl kickstart（macOS 已有 RunAtLoad，补齐 Win/Linux） | P0 |
| 3 | 正常态必须写 hosts | **设计变更**：初始化（enable/首次 run）即写入已验证 IP 全量接管 hosts；后续每轮动态更新。不再等故障才接管 | P0 |
| 4 | 托盘角标要修 | tray 初始图标复用 _refresh() 同款判定（值守启用→绿，未启用→蓝），消除「绿角标+菜单未运行」不匹配 | P0 |
| 5 | 值守链路优化 | 状态/锁/备份路径按平台解析（Windows→%ProgramData%\ghlink 或 config 目录；Linux/macOS→/var/lib/ghlink）；enable 失败输出真实原因；锁路径不依赖 cwd（P0 崩溃修复） | P0 |
| 6 | 初始化合一次，后续动态更新 | GitHub520 段：enable 时拉取+抽检+合入 hosts 一次；后续每轮仅刷新缓存、IP 变化才更新段落；拉取失败保留旧段 | P1 |
| 7 | 全面优化成 8 域名 | 8 域名（github/api/codeload/fastly/raw/objects/gist/githubassets）全链路一致：config 模板（三平台）、brew formula 初始模板、探测+自愈+verify 全覆盖 | P1 |
| 8 | 本机跑 enable 抓报错 | 已完成（见第一节实测） | 完成 |

## 三、③ 正常态接管设计（核心变更，李工 3/6 条定调）

**旧设计（v0.1-v0.2.18）**：正常态不写 hosts，仅探测失败达阈值才切换写入；恢复后回滚删除。
**问题**：全局访问生效依赖「先失败再自愈」——故障窗口期内访问一直坏；且 hosts 常态无任何 GitHub 条目，DNS 污染/劫持时工具形同虚设。

**新设计（v0.2.19）**：
1. **初始化接管**：enable 或首次 run 时，DoH 解析 8 域名取 IP → TCP 443 预检 → 写入 `# ghlink Start/End` 主段；同时拉取 GitHub520 → 抽检 → 写入 `# ghlink520 Start/End` 独立段。一次合入，正常态 hosts 即有完整 GitHub 通路
2. **动态更新**：后续每轮仅做增量——探测各域名，IP 变化才更新主段；GitHub520 缓存刷新（1h），段内容变化才重写；写前备份（hosts_backup_dir），自检失败回滚上一版
3. **故障切换**：核心域名失效 → 切候选 IP（同域名换 IP），非核心失效 → 健康度降级剔除，不整体回滚（沿用 v0.2.15 verify 逻辑）
4. **回滚语义调整**：不再「恢复即删除段落」，而是「保持最优可用配置」——hosts 始终由 ghlink 托管，禁用（disable）才移除段落并恢复备份
5. **锁文件**：固定放状态目录（config 同目录或平台状态目录），禁止 cwd 派生（修复实测 1 崩溃）

## 四、⑤ 值守链路优化细项

1. `_config_path()` root 分支与模板候选路径补齐（含 /opt/homebrew 等 brew 实际布局）
2. 状态/锁/备份路径按平台解析：
   - Windows：`%ProgramData%\ghlink\`（SYSTEM 可写）或 config 同目录
   - Linux：/var/lib/ghlink/（root）/ ~/.ghlink/（用户）
   - macOS：/usr/local/var/ghlink/ 或 ~/.ghlink/
3. 心跳判据放宽：fresh 阈值 180s → 5400s（覆盖 1h 间隔 + 宽限）
4. enable 失败路径输出真实原因（schtasks 返回码/错误信息透传），不再静默
5. 阈值对齐 1h 粒度：degrade_after_rounds 3、cooldown_min 60、verify_success_rounds 2
6. 注册后立即首轮触发（对应 ②）：schtasks /Run / systemctl start / launchctl kickstart

## 五、⑦ 8 域名全链路清单

- 探测 targets：github.com / api.github.com / codeload.github.com / github.global.ssl.fastly.net / raw.githubusercontent.com / objects.githubusercontent.com / gist.github.com / github.githubassets.com
- core_targets（永不降级）：github.com / api.github.com
- config 模板（三平台打包一致）+ brew formula 初始模板同步 8 域名 + github520 配置
- README：8 域名说明 + github520 使用说明 + 值守语义（正常态托管 hosts）+ brew trust

## 六、执行链

1. 赛博方案（本文档）→ 李工终裁
2. 顾笙实现 ①-⑦ → CI 全绿 → 赛博复核 → bump v0.2.19 → 合 main → tag
3. 验证：
   - 本机 Windows（赛博）：enable 后 status 立即 normal + 心跳新鲜；hosts 含 8 域名 + GitHub520 段；version/status 任意 cwd 不崩
   - 拂晓 Linux：enable 后立即首轮；hosts 接管；timer 1h；apt/Pages 源
   - 李工：macOS LOGO/托盘、Windows 托盘角标、真实机器验收

## 七、风险与备注

- ③ 设计变更影响面：正常态写 hosts = 工具从「故障自愈」变「常驻接管」，disable 语义必须清晰（移除段落+恢复备份），README 需讲明
- ② 立即首轮：Windows schtasks /Run 需任务已注册；SYSTEM 上下文无交互，首轮探测+写入 hosts 由 SYSTEM 执行（需验证 hosts 写权限）
- ⑤ Windows 状态目录：%ProgramData%\ghlink 需确认 SYSTEM 与普通用户可读（托盘读状态），必要时 user 读 system 写的文件用 ACL
- 实测 1 崩溃（cwd 派生锁）修复后需补测试：任意 cwd 下 version/status/run 均不崩
