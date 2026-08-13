# ghlink 现成方案调研（RESEARCH）

> 调研人: 拂晓 | 日期: 2026-08-13 | 方法: GitHub API 实测（star/活跃度/维护状态）

## 结论先行
现有开源方案**没有完全对标**"监控→自愈"闭环的产品：
- GitHub520 是被动定时刷 hosts，网络故障时不会主动切换
- dev-sidecar / Watt Toolkit 是本地代理重应用，无自愈语义
- 最接近的 FastGithub 已删库停维护（此类工具长期维护难是共性）

## 方案对比（实测数据）
| 项目 | star | 机制 | 缺点 |
| :--- | :--- | :--- | :--- |
| GitHub520 (521xueweihan) | 29.3k | Actions 定时刷 hosts，用户定时拉取 | 被动定时，无监控无自愈；依赖外部服务 |
| ineo6/hosts | 5.4k | 同 GitHub520，客户端自动更新 | 同上 |
| dev-sidecar | 23.6k | 本地代理 + DNS 优选 | Electron 重依赖、需常驻、图形界面 |
| Watt Toolkit (SteamTools) | 26.5k | 跨平台加速工具箱 | 定位 Steam、功能重、.NET 依赖 |
| FastGithub | 已删库 | 自动检测 + 切换（最接近） | 停止维护，仓库已删除 |
| SwitchHosts | 27k | hosts 管理壳 | 不解决 IP 获取 |

## 核心差距
1. **主动监控-自愈闭环**：探测连通性 → 不稳定自动换 IP → 自检 → 失败回滚（现成方案无）
2. **零依赖轻量**：纯 Python 标准库 + 定时任务，对比 Electron/.NET 轻一个量级
3. **保守安全**：自检失败自动回滚、坏配置不留场、冷却防抖

## 借鉴点
- GitHub520 的 IP 数据源思路（多 DoH 解析）
- dev-sidecar 的 DNS 优选策略（ghlink 已实现多数票 + TCP 443 预检）

## 定位
ghlink 定位"GitHub 链路自愈"垂直场景：自愈闭环 + 可演进架构（三层解耦/平台单点适配/状态驱动/可观测），与现成方案形成差异化，非大而全替代品。
