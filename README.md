# ghlink — GitHub 链路自愈工具

监控 GitHub 网络连通性，不稳定时自动获取新 IP 并替换 hosts，实现无感自愈。

## 定位

| 项 | 值 |
|----|----|
| 项目 | PJ-002 [github网络优化]项目组 |
| 仓库 | https://github.com/liwmj/ghlink |
| 状态 | ✅ 开发中（v0.1 核心模块已实现） |
| 依赖 | 零第三方依赖（纯 Python 标准库） |
| 平台 | Windows 10/11 / macOS 13+ / Linux (Ubuntu/Debian) |

## 核心机制（三层）

1. **监控层**：定时（默认 1min）对 GitHub 核心域名做 TCP 443 + TLS + HTTP HEAD 三层探测
2. **替换层**：连续 N 次失败（默认 3）→ 自动获取新 IP → 备份 hosts → 写入 → 刷 DNS → 自检 → 失败回滚
3. **IP 通路自稳**：多 DoH 源 + 系统 DNS + 本地缓存，多数票 + TCP 443 预检，单源失败自动剔除

## 快速开始

```bash
# 1. 生成配置
cp config.example.json config.json

# 2. 配置飞书告警（可选，留空则关闭）
#    config.json → notify.feishu_webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"

# 3. 单轮手动执行（Linux/macOS 需 root 写 hosts）
sudo python3 -m ghlink.main config.json

# 4. 定时调度（平台定时任务，1 分钟粒度）
#    Linux   → crontab:  * * * * * cd /path/ghlink && sudo python3 -m ghlink.main config.json
#    macOS   → launchd plist（StartInterval=60）
#    Windows → 任务计划程序（每 1 分钟）
```

## 退出码

| 码 | 含义 |
|----|------|
| 0 | 正常（含跳过：探测通过 / 锁占用 / 冷却期） |
| 1 | 降级或告警（提权失败 / 写入失败 / 自检失败 / 无可用 IP） |
| 2 | 配置/参数错误 |

## 状态文件 ghlink_status.json（schema v1）

```json
{
  "schema_version": 1,
  "state": "normal | switching | verifying | degraded",
  "probe": { "targets": {}, "consecutive_failures": 0 },
  "current_ip": null,
  "history": [],
  "last_error": null
}
```

字段为 schema v1 定稿，改动必须走 D+3 评审。

## 验收口径（终裁版，D+3 评审按此打勾）

- 探测期（持续不稳定 → 触发切换）≤ 3min（含调度相位最坏情况）
- 自愈期（触发 → GitHub 恢复可用）≤ 60s（目标 30s）
- 两段分别断言；总恢复时间 = 两段之和（最坏 ≈4min）
- 自愈成功率 ≥ 95%；误触发率 ≤ 5%
- 告警：冷却期去重、发送失败不阻断主流程

## 目录结构

```
src/ghlink/
├── config.py            # 配置加载/深合并/缺省回退
├── platform_adapter.py  # 平台差异唯一出口（hosts/权限/刷DNS/备份回滚）
├── probe.py             # TCP443+TLS+HTTP HEAD 三层探测
├── resolver.py          # 多 DoH + 系统 DNS 多数票 + 443 预检
├── hosts_manager.py     # 段落式写入/备份/自检/回滚
├── state.py             # 状态文件原子写
├── notifier.py          # 飞书 webhook（失败不阻断）
├── lock.py              # 跨平台防重入锁
└── main.py              # 单轮执行闭环
tests/                   # 测试用例（小爪主责，拂晓辅助验证）
```

## 开发记录

- 2026-08-13：立项（PJ-002）→ 脚手架 v0.1 → 核心 9 模块实现 → 冒烟测试通过
- D+3 评审（2026-08-16）：技术方案 + 测试用例 + 可跑版本对齐

## 📦 下载与发布

- **v0.1.0 Release**：https://github.com/liwmj/ghlink/releases/tag/v0.1.0
- 源码包（tar.gz / zip）由 GitHub 自动生成，https 直链下载即可
- 发布节奏：D+3 评审 → 修改合入 master → tag → Release → 链接更新
