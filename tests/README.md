# ghlink 测试目录（小爪主责，拂晓辅助验证）

用例按方案草案 v0.1 口径编写，D+3 评审前随评审材料提交。

## 用例分组（对应草案与小爪 5 点确认）

- `test_config.py` — 配置加载/合并/缺省回退
- `test_probe.py` — 探测逻辑（mock 网络）：单域名失败/多域名并行/超时兜底
- `test_state.py` — 状态文件读写、计数跨运行累计、成功清零、重启不丢、原子写
- `test_resolver.py` — 多源获取：单源失败剔除、多数票、预检剔除、全源失败降级
- `test_hosts_manager.py` — 段落式写入/备份/回滚/自检失败回滚、坏配置不留场
- `test_lock.py` — 防重入：定时跑+手动跑不重叠、残留锁接管（PID 死/超时）
- `test_notifier.py` — webhook 失败不阻断主流程、冷却期去重、抖动只发 1 条
- `test_e2e.py` — 端到端（mock 网络）：触发切换全链路、degraded 路径

## 平台矩阵（实跑）

- Windows 10/11/Server2022：计划任务（时区/任务丢失）、ipconfig /flushdns
- macOS 13+：launchd、dscacheutil
- Linux Ubuntu/Debian：cron、resolvectl / systemd-resolve / nscd 回退链
