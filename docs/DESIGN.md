# ghlink 设计文档（DESIGN）

> 版本：v0.2.19 ｜ 最后更新：2026-08-21 ｜ 状态：✅ 已实现并通过三平台验证（生产就绪）
>
> ## 变更记录
>
> - **v0.2.19（2026-08-21，李工 8 条决策）**：
>   1. README 补 `brew trust`（Homebrew 4.x 第三方 tap 默认不可信）
>   2. `enable` 注册后立即执行第一轮（systemd start / launchctl kickstart / schtasks /Run），不等定时器；
>      status 区分「首轮执行中」与「疑似僵尸」（_heartbeat_never）
>   3. **hosts 常态维护**（设计修正）：正常态也写 hosts 段（内容无变化不落盘），保证全局访问生效，不再仅故障切换才写
>   4. 托盘初始角标与 _refresh 同款判定（值守未启用→蓝，修复 Windows 绿角标+菜单未运行不匹配）
>   5. Windows 值守链路：schtasks 失败输出真实 stderr（_run_cmd_output_error）；安装器预注册与 enable 对齐（/SC HOURLY + config 参数）
>   6. GitHub520 静态兜底：初始化合一次（state 标记 github520_initialized），后续从现有 hosts 保留子段（# ghlink520 Start/End），只做动态更新
>   7. brew formula config 模板直接同步 config.example.json（8 域名，不再手写旧模板）
>   8. Windows 实测：GitHub Actions windows-latest 跑 ghlink.exe enable，验证注册+首轮+心跳+hosts 落盘完整闭环


## 1. 背景与目标

### 1.1 问题

在中国大陆等网络环境下，GitHub 的 DNS 解析常被污染或返回不可达 IP，导致：
- `git clone` / `git push` 超时（TCP 443 建连失败）
- TLS 握手超时、HTTP 请求无响应
- 各域名（github.com / api.github.com / codeload.github.com / raw.githubusercontent.com）可用性不一致

### 1.2 目标

- **自动**：探测到不稳定后自动获取可用 IP 并替换 hosts，全程无人值守
- **无感**：恢复时限 ≤ 60s（目标 30s），替换瞬间最多打断一次请求
- **稳定**：自愈成功率 ≥ 95%、误触发率 ≤ 5%、15min 冷却防抖、7×24 不崩溃
- **安全**：写入前备份、写入后自检、失败回滚——**宁可不变，不能改坏**
- **跨平台**：macOS / Windows / Linux 一套代码无 BUG

## 2. 总体架构

```
┌─────────────────────────────────────────────────────┐
│                    定时调度层                        │
│    cron / launchd / 任务计划程序（1 分钟粒度）        │
└──────────────────────┬──────────────────────────────┘
                       │ 单轮执行
┌──────────────────────▼──────────────────────────────┐
│                    监控层（probe）                   │
│   TCP443 建连 → TLS 握手(SNI) → HTTP HEAD 三层校验    │
│   单轮并行探测全部目标，任一失败记本轮失败             │
└──────────────────────┬──────────────────────────────┘
                       │ 连续 N 轮失败（默认 3）
┌──────────────────────▼──────────────────────────────┐
│                    替换层（自愈）                    │
│   ① resolver 多源取 IP → ② 备份 hosts → ③ 写入      │
│   ④ 刷 DNS → ⑤ 自检 → 失败回滚                     │
└──────────────────────┬──────────────────────────────┘
                       │ 自检通过
┌──────────────────────▼──────────────────────────────┐
│                 verifying 确认态                     │
│   连续 verify_rounds 轮（默认 2）成功 → normal        │
└─────────────────────────────────────────────────────┘
```

## 3. 模块设计

### 3.1 config.py — 配置加载

- `load_config(path)`：JSON 加载 + 深合并 + 缺省回退
- 无配置文件 → 全默认；部分覆盖 → 其余回退默认；非法输入 → 明确报错
- 核心段：`probe` / `trigger` / `resolver` / `notify`

### 3.2 platform_adapter.py — 平台差异唯一出口

| 能力 | macOS | Windows | Linux |
|------|-------|---------|-------|
| hosts 路径 | /etc/hosts | C:\Windows\System32\drivers\etc\hosts | /etc/hosts |
| 提权 | sudo 提示（交互） | ShellExecuteW runas（自动） | sudo 提示（交互） |
| 刷 DNS | dscacheutil -flushcache | ipconfig /flushdns | resolvectl flush-caches |
| 备份 | copy2 | copy2 | copy2 |

- **提权语义**：Windows 非管理员时自动 runas 提权重跑，启动成功后旧进程立即退出（防双跑）；提权失败只告警不替换（degraded）
- **提权状态保存**：提权 exit 前先落盘 switching 状态，标记不丢

### 3.3 probe.py — 监控探测

- `probe_target(host, timeout)`：TCP 443 → TLS(SNI) → HTTP HEAD，任一层失败即失败
- `probe_all(targets, timeout)`：ThreadPoolExecutor 并行探测全部目标
- `round_ok(results)`：全部通过才 True；**空结果保守视为失败**
- 使用 `ProxyHandler({})` 构建独立 opener，**禁用环境代理**（探测必须走真实网络）

### 3.4 resolver.py — IP 多源获取

```
多 DoH 源（阿里/腾讯/CF/Google）──┐
系统 DNS 直查（getaddrinfo）──────┼→ 多数票统计 → TCP 443 预检 → 候选列表
本地缓存兜底（ghlink_cache_*.json）┘              （保留前 max_candidates 个）
```

- 单源失败自动剔除，不拖垮整体
- 预检：`_precheck(ips)` 对候选做 TCP 443 建连粗筛
- 全源失败 → 回退本地缓存；缓存也空 → 返回空候选（调用方保持原配置 + 告警）
- 成功后写缓存（原子写 + TTL 过期）

### 3.5 hosts_manager.py — hosts 写入/回滚

- `build_block(entries)`：生成多域名段落（含标记注释）
- `apply_block(block, backup_dir) -> (ok, backup_path)`：提权 → 备份 → 写入 → 刷 DNS
- `verify_after_apply(targets, timeout)`：写入后立即自检（复用 probe）
- `rollback(backup_path)`：自检失败立即恢复备份
- 备份文件：`hosts.<ts>.bak`（同秒加序号去重）

### 3.6 state.py — 状态文件

- 原子写（临时文件 + os.replace），损坏文件回退默认
- Schema v1 字段：`schema_version / state / probe / current_ip / verify_success / switched_at / last_error / history`

### 3.7 notifier.py — 多渠道通知

- 渠道：通用 Webhook（默认）/ 飞书 / 钉钉 / 企业微信 / Telegram（v0.2 多通道化，每个渠道一个 POST 适配器）
- 冷却期去重（基于 last_alert_at 时间差）
- 发送失败不阻断主流程（try/except 吞掉）

### 3.8 lock.py — 防重入锁

| 平台 | 机制 |
|------|------|
| Linux/macOS | fcntl.flock（LK_EX 阻塞） |
| Windows | msvcrt.locking（LK_NBLCK 非阻塞，OSError → yield False 跳过本轮） |
| 兜底 | PID 文件 + 时间戳（10min 过期接管） |

## 4. 状态机

```
                ┌────────────┐
   探测成功 ───▶ │   normal   │ ◀─── 连续 2 轮成功
                └─────┬──────┘
                      │ 连续 3 轮失败
                ┌─────▼──────┐
                │ switching  │  取 IP → 写 hosts → 自检
                └─────┬──────┘
                      │ 自检通过          自检失败
                ┌─────▼──────┐      ┌─────▼──────┐
                │ verifying  │      │  degraded  │ → 回滚 + 告警
                └────────────┘      └────────────┘
```

- **冷却**：切换成功记录 switched_at，15min 内不重复切换（与 state 解耦，切换后冷却依然生效）
- **失败计数**：成功轮清零；切换成功后重置为 0 重新累计

## 5. 失败场景与降级路径

| 场景 | 行为 | 退出码 |
|------|------|--------|
| 提权失败 | 不写入，degraded + 告警 | 1 |
| 无可用 IP（全源失败） | 不写入，degraded + 告警 | 1 |
| hosts 写入失败 | 不写入，degraded + 告警 | 1 |
| 自检失败 | **回滚备份**，degraded + 告警 | 1 |
| 告警发送失败 | 吞掉，主流程继续 | 0/1 |
| 锁被占用 | 本轮跳过，不排队不阻塞 | 0 |
| 冷却期内 | 跳过，不重复切换/告警 | 0 |

## 6. 验收口径（终裁版）

| 指标 | 断言 |
|------|------|
| 探测期（持续不稳定→触发切换） | ≤ 3min（含调度相位） |
| 自愈期（触发→恢复可用） | ≤ 60s（目标 30s） |
| 总恢复时间 | ≈ 4min（两段之和） |
| 自愈成功率 | ≥ 95% |
| 误触发率 | ≤ 5% |
| 切换频率 | ≤ 3 次/小时（15min 冷却） |
| 可靠性 | 7×24 不崩溃，自动拉起 |

## 7. 测试策略

- **单元测试**：`tests/` 11 文件 56 用例（config/probe/resolver/hosts/state/lock/notifier/e2e/domain-health）
- **mock 原则**：测试不依赖真实网络，网络调用全部 monkeypatch
- **真机冒烟**：注入故障（写坏 hosts/超时）→ 验证触发/切换/回滚全链路；已完成 Linux（Ubuntu）、Windows（Server 2022）、macOS 三平台冒烟
- **跨平台矩阵**：macOS / Windows / Linux 三平台全量套件 56 passed 全绿 + 三平台真机冒烟

## 8. 代码审查记录

见 [docs/REVIEW-v0.1.md](REVIEW-v0.1.md)（P0×3 / P1×3 / P2×3 全部修复闭环）

### 设计决策：托盘 = 值守总开关（2026-08-16 李工定调 + 方案 A 终裁）

语义模型：托盘运行即值守生效，托盘是值守的统一开关。

- **开机启动**（autostart）：Windows Run 键 → 登录时拉起托盘（ghlink-tray.exe）。
- **托盘 = 总开关**：托盘启动时若值守未启用则自动开启（幂等）；托盘退出（确认后）停用值守。
- **enable 值守**：底层 schtasks SYSTEM 任务（无窗口 ghlink-watch.exe），1 分钟探测+自愈。

权限模型（路线 A，李工 13:50 拍板）：
1. 安装时（向导本需 admin）预注册 SYSTEM 值守任务（默认 disabled）
2. 托盘普通权限常驻；启动/退出值守各触发一次 UAC（独立提权子进程，托盘不被误杀）
3. 提权失败不阻塞托盘，降级为菜单提示「值守未开启，点此启用」
4. 值守调度入口 windowed（ghlink-watch.exe）静默执行，不弹命令行窗口

实现约束：
1. 托盘自动 enable 用独立提权进程（ShellExecuteW runas / subprocess），不得直接调 service.enable（ensure_privilege 会 exit 托盘）
2. 托盘启动 enable 幂等（已启用则跳过）；退出 disable 幂等
3. 写 hosts（自愈核心动作）需管理员——值守跑在 SYSTEM 任务内，天然满足

## 9. 已知边界与 v0.2 规划

- ~~**目标域名健康度管理**~~（✅ v0.2 已实现 2026-08-14）：长期不可达域名（如 codeload/fastly 在某些网络）从自检集自动降级，核心域名（probe.core_targets）优先保证切换成功；非核心域名连续失败 degrade_after_rounds 轮降级、连续成功 recover_rounds 轮恢复，核心域名永不降级
- ~~**便捷安装包**~~（✅ v0.2 已实现 2026-08-14）：Windows installer.exe（Inno 向导，含自启选项）+ 裸 exe 绿色版并存；Linux .deb；macOS 仅 brew（dmg 砍掉）；PyPI 发布链就绪
- **系统托盘**（v0.2.x 规划，2026-08-14 立项）：pystray 一套 API 支持 Windows + macOS，Linux 纯 CLI 不做；托盘=UI 载体（绿/黄/红/灰状态图标、悬停摘要、右键菜单、气泡通知），值守开关复用 enable/disable 通道；安装后默认自启（Windows Inno 默认勾选 + Run key；macOS brew 随登录 LaunchAgent），右键菜单带开关；依赖只进安装包，核心保持零依赖；退出托盘≠停止值守
