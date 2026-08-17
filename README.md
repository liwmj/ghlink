<div align="center">
  <img src="assets/ghlink-icon-128.png" width="96" alt="ghlink"/>
  <h1>ghlink — GitHub 链路自愈工具</h1>
  <p>当 GitHub 网络不稳定时，自动获取可用 IP 并替换 hosts，实现无感自愈。</p>
</div>

<p align="center">
  <a href="https://github.com/liwmj/ghlink/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/liwmj/ghlink/ci.yml?label=CI&style=flat&logo=github" alt="CI"/></a>
  <a href="https://github.com/liwmj/ghlink/releases"><img src="https://img.shields.io/github/v/release/liwmj/ghlink?label=%E7%89%88%E6%9C%AC&style=flat&logo=github" alt="版本"/></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/liwmj/ghlink?label=License&style=flat" alt="License"/></a>
  <a href="docs/DESIGN.md"><img src="https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey?style=flat" alt="平台"/></a>
  <a href="docs/"><img src="https://img.shields.io/badge/docs-passing-brightgreen?style=flat" alt="文档"/></a>
  <a href="https://github.com/liwmj/ghlink/releases"><img src="https://img.shields.io/github/downloads/liwmj/ghlink/total?label=%E4%B8%8B%E8%BD%BD&style=flat" alt="下载"/></a>
</p>

---

## 目录

- [为什么需要 ghlink](#为什么需要-ghlink)
- [核心特性](#核心特性)
- [工作原理](#工作原理)
- [架构设计](#架构设计)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [运行与退出码](#运行与退出码)
- [状态文件](#状态文件)
- [测试与验证](#测试与验证)
- [跨平台支持](#跨平台支持)
- [路线图](#路线图)
- [开发记录](#开发记录)
- [License](#license)

---

## 为什么需要 ghlink

在中国大陆等网络环境下，GitHub 的 DNS 解析经常被污染或返回不可达 IP，导致 `git clone`、`git push`、网页访问频繁超时（TCP 443 连接失败、TLS 握手超时）。

常见的解决方式：
- **手动改 hosts**：需要人工找 IP、改文件、刷 DNS，IP 失效后又要重来
- **代理工具**：需要额外部署、配置，且不是所有场景都适用

**ghlink 的思路**：监控 GitHub 连通性 → 不稳定时自动获取可用 IP → 写入 hosts → 自检确认 → 失败自动回滚，全程无人工干预。

---

## 核心特性

- 🔍 **三层探测**：TCP 443 建连 → TLS 握手（SNI）→ HTTP HEAD，真实反映 GitHub 可用性
- 🔄 **自动自愈**：连续 3 轮失败（默认）触发切换，写入新 IP 后自检，失败立即回滚
- 🌐 **多源 IP 获取**：阿里/腾讯/Cloudflare/Google 四个 DoH 源 + 系统 DNS + 本地缓存，多数票 + TCP 443 预检，单源故障自动剔除
- 🧠 **目标域名健康度管理**（v0.2）：非核心域名（如 codeload/fastly）长期不可达自动降级，从判定/自检集剔除，核心域名（github.com / api.github.com）永不降级优先保证切换成功；恢复后自动重新纳入
- 🛡️ **安全红线**：写入前备份 hosts、写入后自检、自检失败回滚——**宁可不变，不能改坏**
- ⏱️ **冷却防抖**：切换成功后 15 分钟冷却期，避免 IP 抖动导致频繁切换
- 🧵 **防重入锁**：跨平台（flock / msvcrt / PID 文件），避免定时任务并发执行
- 🔔 **多渠道告警**：切换、降级、回滚事件实时通知（飞书 / 钉钉 / 企业微信 / Telegram / 通用 Webhook 可配），冷却期去重，发送失败不阻断主流程
- 💻 **跨平台**：macOS / Windows / Linux 一套代码，平台差异收敛到单一适配层
- 📦 **零第三方依赖**：纯 Python 标准库实现，运行无需任何第三方包

---

## 工作原理

```mermaid
flowchart LR
    A[定时调度<br>cron / launchd / 任务计划] --> B[监控层 探测<br>TCP443 + TLS + HTTP HEAD]
    B -->|连续 3 轮失败| C[IP 通路自稳<br>DoH×4 + 系统DNS + 缓存 + 预检]
    C --> D[替换层 自愈<br>备份 → 写入 → 刷DNS → 自检]
    D -->|自检失败| E[回滚 restore_hosts<br>坏配置不留场]
    D -->|自检通过| F[verifying 确认<br>连续 2 轮成功 → normal]
    F --> B
    B -->|正常| A
```

**状态机**：`normal → switching → verifying → normal`，异常路径进入 `degraded`（只告警不破坏）。

---

## 架构设计

详细设计文档见 [docs/DESIGN.md](docs/DESIGN.md)，包含：

- 模块划分与职责（9 个核心模块）
- 平台差异适配策略（hosts 路径 / 提权 / DNS 刷新）
- 状态文件 Schema v1
- 失败场景与降级路径
- 代码审查记录：[docs/REVIEW-v0.1.md](docs/REVIEW-v0.1.md)

```
src/ghlink/
├── config.py            # 配置加载 / 深合并 / 缺省回退
├── platform_adapter.py  # 平台差异唯一出口（hosts / 权限 / 刷DNS / 备份回滚）
├── probe.py             # TCP443 + TLS + HTTP HEAD 三层探测
├── resolver.py          # 多 DoH + 系统 DNS 多数票 + 443 预检 + 缓存兜底
├── hosts_manager.py     # 段落式写入 / 备份 / 自检 / 回滚
├── state.py             # 状态文件原子写
├── notifier.py          # 多渠道通知（Webhook / 飞书 / 钉钉 / 企业微信等，冷却去重，失败不阻断）
├── lock.py              # 跨平台防重入锁（flock / msvcrt / PID 文件）
└── main.py              # 单轮执行闭环（探测→判定→自愈→确认）
```

---

## 默认行为（值守独立于托盘，2026-08-17 李工新口径）

ghlink 的语义模型（替代 2026-08-16 版「托盘=值守总开关」）：

- **值守独立于托盘**：`ghlink enable` 注册平台定时任务（1 分钟粒度后台探测 + 自愈）即开启值守，**不启动托盘也能值守**（命令行模式）。
- **托盘 = UI 载体**：托盘用于展示状态（图标颜色/菜单）与便捷操作（复制 IP/开关自启）。启动/退出托盘不改变值守状态，值守由 enable 独立管理。
- **Windows 便捷联动**（启动逻辑层面，非代码逻辑）：为方便 Windows 用户，启动托盘时同步启动值守；退出托盘时同步退出值守。Windows 托盘在 ⇒ 值守在（联动行为）；命令行 `ghlink enable` 仍可独立值守。
- **值守 enable**：底层是系统定时任务（Windows schtasks / macOS LaunchDaemon / Linux systemd timer，1 分钟粒度），`ghlink enable` 注册、`ghlink disable` 移除、`ghlink status` 查看。

关键语义：**值守在 = 平台任务注册 + 心跳正常**；托盘只是显示器，不是开关。安装后默认不自启（需手动 `ghlink enable` 或勾选「开机自动启动托盘」）。

## 快速开始

### 环境要求

- Python 3.8+（纯标准库，无第三方依赖）
- 写 hosts 需要管理员/root 权限

### 安装（各系统）

**macOS（Homebrew tap，推荐）**

```bash
brew tap liwmj/ghlink
brew install ghlink
```

**Windows（安装向导 / 裸 exe）**

```bash
# 方式一：安装向导（推荐，含托盘依赖与开机自启选项）
# 从 https://github.com/liwmj/ghlink/releases 下载 ghlink-installer-vX.Y.Z.exe 双击安装

# 方式二：裸 exe（绿色版，无需安装）
# 下载 ghlink.exe（CLI）+ ghlink-tray.exe（托盘）放同一目录直接运行
```

**Linux（apt / .deb）**

```bash
# 方式一：apt 仓库（Debian/Ubuntu，v0.2.18 起用 Pages 固定 URL，写一次永久生效）
echo "deb [trusted=yes] https://liwmj.github.io/ghlink/apt/ ./" | sudo tee /etc/apt/sources.list.d/ghlink.list
sudo apt update && sudo apt install ghlink
# 以后每版发完直接 sudo apt upgrade 拿最新，无需改 sources.list

# 方式二：.deb 直接安装
wget https://github.com/liwmj/ghlink/releases/download/v0.2.18/ghlink_0.2.18-1_all.deb
sudo dpkg -i ghlink_*.deb
```

**PyPI（任意系统，发布打通后生效）**

```bash
# PyPI 发布打通后可直接安装（当前版本请优先使用上方各平台安装包）
pip install ghlink
```

**源码（任意系统，零第三方依赖）**

```bash
git clone https://github.com/liwmj/ghlink.git && cd ghlink
cp config.example.json config.json
# 运行：python3 -m ghlink.main run [config.json]
# 托盘：pip install pystray Pillow 后 python3 -m ghlink.main tray
```

### 配置

```bash
cp config.example.json config.json
# 编辑 config.json：
#   - notify.channel：通知渠道（webhook / feishu / dingtalk / wecom / telegram，可选，默认关）
#   - probe.targets：探测域名清单（默认 github.com / api.github.com 等）
```

### 手动运行一次

```bash
# Linux / macOS（需 root 写 hosts）
sudo python3 -m ghlink.main config.json

# Windows（管理员命令行）
python -m ghlink.main config.json
```

退出码 `0` = 正常（探测通过或冷却期跳过），`1` = 降级/告警。

### 默认行为（v0.2.x）

- **安装后默认不自启**（2026-08-14 李工定规）：不注册值守任务、托盘不随登录启动
- **开启自启**：托盘右键菜单「启用值守」开关，或命令行 `ghlink enable`（注册 1 小时粒度定时任务，v0.2.18 起）
- **关闭自启**：托盘右键「停用值守」，或 `ghlink disable`
- 托盘（Windows/macOS）：状态图标/悬停摘要/右键开关/气泡通知；Linux 为纯 CLI

### 定时调度（1 小时粒度，v0.2.18 起）

**Linux（crontab）**：
```bash
0 * * * * cd /opt/ghlink && sudo python3 -m ghlink.main config.json >> /var/log/ghlink.log 2>&1
```

**macOS（launchd）**：
```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.ghlink.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/opt/ghlink/src/ghlink/main.py</string>
        <string>/opt/ghlink/config.json</string>
    </array>
    <key>StartInterval</key><integer>3600</integer>
    <key>RunAtLoad</key><true/>
</dict>
</plist>
```

**Windows（任务计划程序）**：创建基本任务 → 触发器设为「重复任务间隔 1 小时」→ 操作设为 `python C:\ghlink\src\ghlink\main.py C:\ghlink\config.json`，勾选「使用最高权限运行」。

### GitHub520 社区 IP 集成（v0.2.18）

ghlink 周期拉取 [GitHub520](https://github.com/521xueweihan/GitHub520) 社区 hosts（默认 1 小时刷新），为非核心 GitHub 域名（raw/objects/gist 等）补充社区 IP，覆盖自愈盲区。**核心域名（github.com/api.github.com）永远由 ghlink 自愈动态验证兜底**，社区 IP 只补非核心盲区、写入前做 TCP 可达性抽检防坏 IP 入场；拉取失败自动回退本地缓存。

---

## 配置说明

| 配置段 | 字段 | 默认值 | 说明 |
|--------|------|--------|------|
| `probe` | `targets` | github.com 等 8 域名 | 探测域名清单（v0.2.18 扩域：含 raw/objects/gist/githubassets） |
| `probe` | `timeout_sec` | 15 | 单域名探测超时 |
| `probe` | `core_targets` | github.com / api.github.com | 核心域名（永不降级，优先保证切换成功） |
| `probe` | `degrade_after_rounds` | 3 | 非核心域名连续失败 N 轮 → 降级（1h 粒度 ≈ 3h） |
| `probe` | `recover_rounds` | 2 | 降级域名连续成功 N 轮 → 恢复纳入 |
| `trigger` | `consecutive_failures` | 3 | 连续失败 N 轮触发切换（1h 粒度 ≈ 3h） |
| `trigger` | `cooldown_min` | 180 | 切换后冷却分钟数（1h 粒度核算） |
| `trigger` | `verify_success_rounds` | 2 | 自愈后连续成功轮数恢复 normal |
| `github520` | `enabled` | true | GitHub520 社区 IP 集成开关（v0.2.18） |
| `github520` | `url` | raw.hellogithub.com/hosts | 社区 hosts 拉取源 |
| `github520` | `refresh_min` | 60 | 社区 IP 刷新周期（1 小时） |
| `github520` | `core_first` | true | 核心域名 ghlink 自愈优先（社区 IP 只补非核心盲区） |
| `resolver` | `doh_sources` | 阿里/腾讯/CF/Google | DoH 源 URL 列表 |
| `resolver` | `cache_ttl_sec` | 3600 | 本地 IP 缓存有效期 |
| `resolver` | `max_candidates` | 5 | 候选 IP 上限 |
| `notify` | `channel` | 关 | 通知渠道（webhook / feishu / dingtalk / wecom / telegram） |
| `notify` | `webhook_url` | 空 | 通用 Webhook URL / 各渠道机器人地址（可选，配置后启用通知） |

---

## 运行与退出码

| 码 | 含义 |
|----|------|
| 0 | 正常（探测通过 / 锁占用跳过 / 冷却期跳过 / 切换成功） |
| 1 | 降级（提权失败 / 写入失败 / 自检失败回滚 / 无可用 IP / 告警触发） |
| 2 | 配置 / 参数错误 |

**降级语义**：任何失败都不破坏现有 hosts（要么不写，要么写了自检不过立即回滚），只记录状态并可选告警。

---

## 状态文件

默认 `ghlink_status.json`（schema v1）：

```json
{
  "schema_version": 1,
  "state": "normal",
  "probe": {
    "targets": { "github.com": {"ok": true} },
    "consecutive_failures": 0
  },
  "current_ip": "140.82.112.3",
  "verify_success": 0,
  "switched_at": null,
  "last_error": null,
  "history": []
}
```

| 状态 | 含义 |
|------|------|
| `normal` | 健康，无需干预 |
| `switching` | 已触发切换，正在写入 hosts |
| `verifying` | 已写入，等待连续成功确认 |
| `degraded` | 降级：保持原配置，已告警 |

---

## 测试与验证

- **单元/集成测试**：`tests/` 目录，60 个用例覆盖配置/探测/解析/hosts/状态/锁/通知/全链路/域名健康度
- **运行测试**：`python -m pytest tests/ -v`
- **真机冒烟**：已完成三平台验证（2026-08-14）：
  - Linux（Ubuntu）：注入故障 → 触发切换 → 写入 → 自检 → 回滚 → 锁接管 → 冷却防抖全链路通过
  - Windows（Server 2022）：正常路径无感跳过 / E-004 降级（全源不可达保持原配置）/ 注入 127.0.0.1 → 自动切换 20.205.243.168 / 2 轮确认恢复 / 残留锁接管 + 并发跳过
  - macOS：本机故障注入全链路冒烟（正常路径/切换/回滚/锁/冷却）通过
- **验证记录**：三平台（macOS / Windows / Linux）60 用例全绿 + 真机冒烟报告

---

## 跨平台支持

| 平台 | 单测 | 真机冒烟 | 备注 |
|------|------|----------|------|
| macOS 13+ | ✅ 60 passed | ✅ 已完成 (2026-08-14) | Intel / Apple Silicon |
| Windows 10/11 / Server | ✅ 60 passed | ✅ 已完成 (2026-08-13) | UAC 提权 / ipconfig flushdns |
| Linux (Ubuntu/Debian) | ✅ 60 passed | ✅ 已完成 | resolvectl / 备份恢复 |

---

## 路线图

- [x] **v0.1.0**（2026-08-13）：核心自愈闭环 + 三平台单测全绿 + 双平台真机冒烟
- [x] **v0.2.0**（2026-08-14）：目标域名健康度管理（长期不可达域名自动降级，核心域名优先切换）+ 三平台真机冒烟闭环 + 生产就绪（Production/Stable）
- [x] **v0.2.1**（2026-08-14）：默认不自启（李工定规）+ 平台安装包发布（Windows installer / macOS brew / Linux deb）
- [ ] **v0.3.x**：历史切换统计与报表

---

## 开发记录

- 2026-08-13：立项（PJ-002）→ 架构设计 → 核心 9 模块实现 → 代码审查（P0/P1/P2 全修复）→ 测试套件合入（51 用例全绿）→ 三平台验证（macOS/Linux/Windows 51 passed）→ Linux + Windows 真机冒烟完成 → Release v0.1.0
- 2026-08-13：Windows 真机冒烟完成，三平台矩阵全绿
- 2026-08-14：v0.2.0/v0.2.1 发布（目标域名健康度管理 + 默认不自启 + 多平台安装包）；三平台真机冒烟闭环
- 2026-08-15：按 P-006 v1.21 对齐（ruff 代码规范 + CI 门禁 + README 徽章参数化/mermaid 原理图 + 测试矩阵）
- 立项评估：A 级（优化 GitHub 网络链路，直接影响开发/同步效率与稳定性）

---

## Contributors

<!-- 早期手动头像墙，有外部贡献者后切换 all-contributors 自动化（P-006 v1.15） -->
<a href="https://github.com/liwmj"><img src="https://github.com/liwmj.png?size=40" width="40" height="40" alt="liwmj"></a>

---

## License

[MIT](LICENSE)
