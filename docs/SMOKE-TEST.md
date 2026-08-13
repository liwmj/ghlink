# ghlink 真机冒烟记录（SMOKE-TEST）

> 双平台真机验证：Ubuntu（拂晓执行）+ Windows Server 2022（赛博执行）
> 批准链: 方案（拂晓 v1.1 + 赛博 2 补充）→ 评审通过 → 李工批准 → 执行

## Ubuntu（Linux，Python 3.12.3，master c85574a）
基线: hosts 无 github 条目｜解析 20.205.243.166｜TCP443 连通正常
- ① 正常路径: EXIT 0 无感跳过，hosts 零改动 ✅
- ② 切换链路: 段落式注入 127.0.0.1 → 触发 → 新 IP 20.205.243.166/168 写入 → 自检通过 → github.com HTTP 200 恢复 ✅
- ③ 回滚兜底: timeout=0.001 注入 → verify failed → 自动回滚，坏配置不留场，state=degraded ✅
- ④ 锁残留: 死 PID 残留锁文件不阻塞（flock 接管）✅
- ⑤ 冷却防抖: cooldown_min=15 内跳过重复切换 ✅
环境发现: 当前网络 codeload/fastly 不可达 → 全量自检保守回滚（正确行为）；resolver 多源实测 alidns/doh.pub 可用、CF/Google 被剔除 ✅

## Windows Server 2022（win32，Python 3.12.7 + pytest 9.1.1）
基线: hosts 无 ghlink 段落，github.com 不通 / api.github.com 通（部分可达天然异常环境）
- ① 正常路径: EXIT 0、hosts 零改动 ✅
- ② 降级路径（E-004 真实行为）: 本机 github 相关 IP 全不可达 → resolver 预检全剔除 → no valid IP candidates → degraded + 保持原配置 + EXIT 1 ✅
- ③ 切换成功: 127.0.0.1 → 20.205.243.168 自动替换 → 2 轮确认 → normal → api.github.com HTTP 200 恢复 ✅
- ④ 锁验证: 残留锁接管 + 并发实例干净跳过（msvcrt 路径）✅
- 收尾: 临时文件清理、hosts 恢复基线、环境无痕 ✅

## 结论
双平台真机冒烟通过：E-001 全链路自愈 / E-002 无感不误触 / E-003 单源回退 / E-004 全源降级 / E-005 防抖 / E-006 崩溃恢复+钁 全场景覆盖。真机验证闭环。
