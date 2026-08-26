# ghlink v0.4.24 验收前置实测记录（顾笙 2026-08-26）

> 用途：v0.4.24 验收附件——还原 10:19「李工看到托盘图标」的真实来源，确认验收基准；
> 同时提供系统状态、渲染链路、部署形态的实测证据，支撑 v0.4.25 部署修复两刀。
> 整理人：顾笙（R-STG-01）｜时间：2026-08-26 10:42｜状态：待赛博核对

---

## 一、验收基准结论（先看这个）

- **当前机器基准 = 0.4.23 安装版 + 无托盘进程在跑**（实测确认，见 §五）。
- **10:19 李工看到的托盘图标 = 顾笙手动启动的 0.4.23 测试进程**，非任何版本修复生效。
- 测试进程已于 10:19 前全部清理（pkill + pid 文件删除），验收环境干净。
- 机器上**未安装 0.4.24**（v0.4.24 于 10:39 发版，晚于 10:19 观察点，时间线对不上是正常的）。

## 二、时间线还原

| 时间 | 事件 | 谁 |
|---|---|---|
| 09:52 | 李工：不再回答菜单栏问题，让顾笙自己测 | 李工 |
| 09:56 | 赛博派单：0.4.18 vs 0.4.23 对比渲染 + 确认系统状态 | 赛博 |
| ~10:14 | 启动 0.4.23 托盘（ghlink-tray），System Events 确认菜单栏项「状态菜单」存在 | 顾笙 |
| ~10:15 | 启动 0.4.18（worktree v0.4.18 源码 + 同套 vendor），System Events 同样确认菜单栏项存在 | 顾笙 |
| ~10:19 | 李工看到托盘图标（= 0.4.23 测试进程，正在菜单栏上） | 李工 |
| ~10:19 | 测试完成，pkill 清理全部托盘进程 + 删 pid 文件 | 顾笙 |
| 10:19-10:24 | 复查：无 ghlink/python 托盘进程、无残留菜单栏项、pid 文件不存在 | 顾笙 |
| 10:39 | v0.4.24 发版（PR #107 / 7da1647） | 赛博 |

## 三、系统状态确认（李工答案 vs 实测交叉验证）

| 检查项 | 李工口头答案 | 顾笙实测证据 | 结论 |
|---|---|---|---|
| 其他第三方菜单栏图标 | 正常 | System Events 枚举：LuLu、Amphetamine、Tailscale、Alfred、PicGo、Clash Verge、iBar Pro、iShot Pro、Nums、Yoink、Tiles、Dash、iRightMouse Pro 菜单栏项全部在位 | 系统菜单栏没坏 |
| 系统是否重启 | 没有 | `kern.boottime` = 8/21 02:39:55，uptime 5 天 7 小时 | 21:38 后无重启 |
| 系统是否更新 | （未直接问） | `softwareupdate --history` 最后一次 macOS 更新 = 8/21 02:29（26.6.2） | 21:38 后无系统更新 |

## 四、渲染链路实测（核心证据）

### 4.1 纯 pystray 最小复现（不走 ghlink 代码）
- 脚本：`/tmp/pystray_min.py`（pystray 0.19.5 + PIL 纯色图标，run_detached）
- 结果：
  ```
  pystray version: 0.19.5
  backend: AppKit (darwin)
  icon.run_detached() OK, waiting 8s...
  icon.visible = True
  icon stopped
  ```
- **结论：用户会话环境渲染 pystray 图标无问题。**

### 4.2 ghlink 0.4.23 托盘（用户会话手动启动）
- 启动：`/Applications/ghlink.app/Contents/MacOS/ghlink-tray`
- System Events 证据：
  ```
  process "Python" (unix id 匹配 ghlink.main tray 进程)
  count of menu bar items of menu bar 1 = 1
  description of menu bar items = "状态菜单"
  ```
- **结论：0.4.23 用户会话渲染正常。**

### 4.3 ghlink 0.4.18 托盘（worktree 源码 + 同套 vendor）
- 启动：`PYTHONPATH=/tmp/ghlink-v0.4.18/src:<vendor> python3 -m ghlink.main tray`
- System Events 证据：同样 `count=1`、`description="状态菜单"`
- **结论：0.4.18 用户会话渲染正常，非代码回归。**

### 4.4 渲染链路总判定
- 环境（用户会话）✅、ghlink 代码 0.4.18/0.4.23 ✅、系统菜单栏 ✅
- **问题不在渲染层，在部署形态。**

## 五、部署形态证据（根因）

| 检查项 | 证据 | 含义 |
|---|---|---|
| LaunchDaemon 状态 | `launchctl print system/com.ghlink.daemon`：state=not running, runs=2, last exit code=1 | daemon 未在跑且最近两次启动失败 |
| daemon 日志 | `/var/log/ghlink.log`（8 行）：`ModuleNotFoundError: No module named 'ghlink'`（mtime 8/24 09:12） | LaunchDaemon 环境 PYTHONPATH 没吃到 libexec/vendor → run 模式根本没跑起来。注：ghlink_status.json 09:50 仍有更新，系手动 run/其他路径写入（daemon 本体以 launchctl state=not running 为准），两者不矛盾 |
| daemon 运行形态 | plist ProgramArguments = `ghlink /etc/ghlink/config.json`（run 模式，root 非 GUI 会话） | daemon 本就不渲染托盘；且 root 非 GUI 会话 NSStatusItem 不渲染 |
| 托盘自启 | `~/Library/LaunchAgents/com.ghlink.tray.plist` 不存在 | 托盘无用户会话自启机制 → 平时没图标 |
| 21:38 成功复盘 | 系统日志 21:30-21:45 无 ghlink 记录；属手动前台运行 | 21:38 成功 = 手动前台跑，符合「用户会话可渲染」结论 |

## 六、结论与对 v0.4.25 的支撑

1. **托盘显示问题** = 缺用户会话 LaunchAgent 自启（刀①），渲染层无需更换；
2. **值守 run 模式问题** = LaunchDaemon PYTHONPATH 未生效（刀②），与渲染独立；
3. 0.4.24 验收②（G4 重启自启）预计挂在自启环节，属 v0.4.25 必要性实证，不算翻车；
4. v0.4.24 的 PyObjC 直驱渲染作为兜底不白做，渲染链路更可控。

## 七、验收时可用命令（复现证据用）

```bash
# 确认版本与进程基准
defaults read /Applications/ghlink.app/Contents/Info.plist CFBundleShortVersionString
ps aux | grep -iE "ghlink.main tray|ghlink-tray" | grep -v grep

# 确认菜单栏渲染（托盘运行时）
osascript -e 'tell application "System Events" to tell (first process whose unix id is <PID>) to get {count of menu bar items of menu bar 1, description of every menu bar item of menu bar 1}'

# 确认 daemon 状态
launchctl print system/com.ghlink.daemon | grep -E "state|runs|last exit"
tail -5 /var/log/ghlink.log
```
