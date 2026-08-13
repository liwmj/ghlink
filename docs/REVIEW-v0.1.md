# ghlink v0.1 代码审查记录

- 审查人：赛博（技术角色 R-TCH-01，主审）
- 审查日期：2026-08-13
- 审查范围：src/ghlink/ 11 文件（对照草案 v0.1 参数/验收口径）
- 总体评价：结构清晰、标准库实现、平台差异收敛正确，接口质量高

## P0（必须修，已修复）

- **P0-1 自检失败未回滚**：verify_after_apply 失败未调用 restore_hosts，坏配置留场。
  修复：apply_block 改返回 `(ok, backup_path)`，main 自检失败立即 rollback(backup_path) → degraded+告警。
- **P0-2 切换冷却期失效**：冷却检查依赖 state=="switching"，切换成功即 normal 导致冷却失效。
  修复：冷却判断基于 switched_at 时间差，与 state 解耦。
- **P0-3 「连续 2 轮成功才恢复」死代码**：切换当轮即 normal，verify_success 永远到不了 2。
  修复：切换后进 verifying，本轮+下一轮共 2 轮成功才 normal。

## P1（建议同批，已修复）

- **P1-1 Windows 提权双跑**：ShellExecuteW runas 后旧进程继续执行 → 虚假 degraded 告警。
  修复：提权进程启动成功后旧进程立即 sys.exit(0)。
- **P1-2 单域名替换 vs 多域名探测口径**：只替换 github.com，api/codeload 仍走污染 DNS。
  修复：替换覆盖全部探测域名（entries 多域名，build_block 全量写入）。
- **P1-3 Windows PID 锁竞态**：检查-创建非原子。
  修复：Windows 用 msvcrt.locking 内核锁（LK_NBLCK 非阻塞）。

## P2（v0.2/测试注意，已修复或记录）

- **P2-1 resolver 本地缓存兜底**：DoH 全挂时无最后防线 → 已实现成功候选缓存（ghlink_cache_*.json，原子写+TTL）。
- **P2-2 probe 未显式禁环境代理**：探测可能走代理失真 → 已用 ProxyHandler({}) 构建独立 opener。
- **P2-3 备份文件同秒覆盖**（低风险）→ 已加序号去重。

## 结论

P0 已修完，P1 同批修复，P2 已处理；等待赛博复核后合入。
