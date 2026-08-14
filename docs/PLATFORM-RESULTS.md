# ghlink 平台测试矩阵回填（PLATFORM-RESULTS）

> 单测: 三平台 51 passed｜真机冒烟: Ubuntu + Windows 双平台通过
> 格式: 平台 × 场景 × 断言

## 单测（51 passed）
- macOS: ✅ 顾笙本机（Python 3.12，51 passed）
- Linux Ubuntu: ✅ 拂晓独立环境复验（Python 3.12.3 + pytest 7.4.4，6.59s）
- Windows Server 2022: ✅ 赛博（Python 3.12.7 + pytest 9.1.1，8.57s）

## Linux Ubuntu（拂晓，真机实测）
- E-001 全链路自愈 | 注入 127.0.0.1 github.com→触发切换 | ✅ hosts 段落 127.0.0.1→20.205.243.166/168，切换后 TCP443 OK + HTTP 200
- E-002 无感不误触 | 网络正常单轮执行 | ✅ EXIT 0 跳过，hosts 无变化
- E-003 单源故障回退 | 多 DoH 源部分不可达 | ✅ CF/Google 剔除，alidns/doh.pub 顶替
- E-004 全源失败/降级 | timeout=0.001 自检必失败 | ✅ state=degraded、回滚到有效 IP、坏配置不留场
- E-005 连续抖动防抖 | 切换失败后立即重跑 | ✅ cooldown 15min 内跳过
- E-006 崩溃恢复/锁 | 死 PID 残留锁 | ✅ flock 接管不阻塞、状态原子写
- 权限 | sudo 提权 | ✅ 成功路径实测；失败路径单测覆盖
- DNS 刷新 | resolvectl flush-caches | ✅ 切换+恢复均实测

## Windows Server 2022（赛博，真机实测）
- E-001 全链路自愈 | 段落注入 127.0.0.1→触发切换 | ✅ 127.0.0.1→20.205.243.168，2 轮确认→normal，api.github.com HTTP 200
- E-004 全源失败 | 本机 github IP 全不可达 | ✅ resolver 预检全剔除→degraded+保持原配置+EXIT 1
- E-002 无感不误触 | 单轮运行 | ✅ EXIT 0 hosts 零改动
- E-006 锁 | 残留锁 + 并发 | ✅ 接管 + 并发干净跳过（msvcrt）
- 权限 | UAC/管理员上下文 | ✅ ensure_privilege 行为记录
- DNS 刷新 | ipconfig /flushdns | ✅ 恢复基线后执行成功

## macOS（CI macos runner，2026-08-14，run 31775934251）
- ✅ ① 正常路径: EXIT 0 + hosts 零改动
- ✅ ② 切换链路: 注入 127.0.0.1 → 触发切换 → 写入真实 IP 140.82.121.3 → 自检通过 → github.com HTTP 200
- ✅ ③ 回滚兜底: 不可达 IP + 超短超时 → 自检失败 → 自动回滚 + degraded，坏配置不留场
- ✅ ④ 锁残留: 死 PID 锁接管不阻塞
- ✅ ⑤ 冷却防抖: 切换后冷却期内不重复切换（history=1）
- ✅ 收尾: hosts 恢复基线无痕（PASS=8 FAIL=0）
- 注：初跑暴露脚本循环 bug（初始 normal 态提前跳出），修复 19ba960 后全绿
