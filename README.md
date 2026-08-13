# ghlink — GitHub 链路自愈工具

监控 GitHub 连通性，异常时自动获取新 IP 替换 hosts，保障无感使用。

- 仓库：https://github.com/liwmj/ghlink
- 方案：docs/technical/ghlink-技术方案草案-v0.1.md（D+3 2026-08-16 评审定稿）
- 语言：Python 3.8+，零第三方依赖（标准库实现）
- 平台：Windows 10/11/Server2022、macOS 13+、Linux（Ubuntu/Debian）

## 目录结构

```
ghlink/
├── src/ghlink/
│   ├── __init__.py        # 版本号
│   ├── config.py          # 配置加载（JSON，零依赖）
│   ├── platform_adapter.py# 平台适配层：hosts 路径/提权/DNS 刷新/备份
│   ├── probe.py           # 监控探测：TCP 443 + TLS + HTTP HEAD
│   ├── resolver.py        # IP 多源获取：DoH 多源 + 系统 DNS + 缓存 + 多数票
│   ├── hosts_manager.py   # hosts 段落式管理：写入/备份/回滚/自检
│   ├── state.py           # ghlink_status.json 读写（schema v1）
│   ├── notifier.py        # 飞书 Webhook 告警（失败不阻断主流程）
│   ├── lock.py            # 跨平台文件锁（防重入，PID+过期时间戳）
│   └── main.py            # 入口：单轮执行（探测→判定→切换→验证）
├── tests/                 # 测试用例（小爪主责，mock 探测/切换）
├── config.example.json    # 配置模板
└── README.md
```

## 核心流程（单轮，调度粒度 1min）

```
probe（多域名并行）→ state 累计失败计数（成功清零）
→ 连续 3 轮失败？→ resolver 多源取 IP（预检连通）→ hosts_manager
（备份→写入→flushdns→自检，失败回滚）→ state 更新 → notifier 告警（冷却期去重）
```

## 开发约定

- 零第三方依赖：import 仅限标准库（socket/ssl/urllib/json/os/sys/ctypes/subprocess/time）
- 平台差异全部收敛在 platform_adapter.py，业务代码不写平台分支
- 所有模块可单测：探测/切换行为可 mock（测试不依赖真实网络）
- 状态文件 schema 以方案草案第 7 节为准，禁止私自改字段
