# ghlink 平台实跑矩阵（三平台 × 场景）

> 实跑矩阵 = 带真实时间/调度/权限的验证，与单元测试互补。
> 断言口径（顾笙终裁收口）：探测期 ≤3min（180s）/ 自愈期 ≤60s（60s）/ 最坏总恢复 ≈4min。
> 执行：小爪主责，拂晓辅助验证。宿主方式 D+3 评审前定（本机/容器/CI）。

## 平台 × 调度

| 平台 | 调度方式 | 验证点 |
|---|---|---|
| Windows 10/11/Server2022 | 任务计划程序 | 时区、任务丢失、开机自启 |
| macOS 13+ | launchd | StartInterval、系统升级后任务残留 |
| Linux Ubuntu/Debian | cron | 时区、环境变量（PATH）、日志轮转 |

## 平台 × 权限

| 平台 | 提权姿势 | 失败场景断言 |
|---|---|---|
| Windows | UAC 管理员 / 服务账户 | 提权失败 → 明确报错，hosts 不动 |
| macOS | 授权（sudo 或授权文件） | 提权失败 → 明确报错，hosts 不动 |
| Linux | sudo / 属主改 /etc/hosts | 权限不足 → 明确报错，hosts 不动 |

## 平台 × DNS 刷新

| 平台 | 刷新姿势 | 断言 |
|---|---|---|
| Windows | ipconfig /flushdns | 刷新后新 IP 生效 |
| macOS | dscacheutil -flushcache | 刷新后新 IP 生效 |
| Linux | resolvectl / systemd-resolve / nscd 回退链 | 三级回退各验一次 |

## 场景 × 断言（三平台全跑）

| 场景 | 断言 |
|---|---|
| E-001 全链路自愈 | 注入故障→触发 ≤180s；触发→恢复 ≤60s；总恢复 ≈≤4min |
| E-002 间歇恢复不触发 | 成功一轮清零，不误切 |
| E-003 单源故障回退 | 自愈成功，源健康标记更新 |
| E-004 全源故障 | 告警 + 原配置不动 + degraded |
| E-005 连续抖动防抖 | 15min 冷却期内不重复切换、告警 ≤1 条 |
| E-006 自愈中途崩溃 | 重启后状态一致、坏配置不留场、锁可接管 |
| E-007 告警通道故障 | 自愈仍完成，webhook 失败只记日志 |

## 实跑执行人登记

| 平台 | 环境 | 执行人 | 状态 |
|---|---|---|---|
| Windows Server 2022 Datacenter x64 | 腾讯云 CVM 2C4G，Python 3.12.7 + pytest 9.1.1 已装 | 赛博 | ✅ 环境就绪，待代码包开跑 |
| Windows 10/11 | 待确认 | 待认领 | ⏳ |
| macOS 13+ | 本机已跑 51 passed（16:44 确认） | 顾笙 | ✅ 已确认，待按矩阵回填 |
| Linux Ubuntu/Debian | Python 3.12.3，master c85574a | 拂晓 | ✅ 已回填（E-001~E-006+权限+DNS 全过） |
| Linux Ubuntu | Python 3.12.3，真机冒烟（2026-08-16） | 小爪 | ✅ 已回填（8/8 双源 + 7/7 官方，双版本互证） |

## 实测回填记录（按「平台 × 场景 × 断言」）

### Linux Ubuntu（执行人：拂晓 ✅，Python 3.12.3，master c85574a，真机实测，08-13）

| 场景 | 断言 | 结果 |
|---|---|---|
| E-001 全链路自愈 | 注入 127.0.0.1 github.com 故障→触发切换 | ✅ hosts 段落 127.0.0.1 → 20.205.243.166/168，切换后 github.com TCP443 OK + HTTP 200 |
| E-002 无感不误触 | 网络正常单轮执行 | ✅ EXIT 0 探测通过跳过，hosts 无变化 |
| E-003 单源故障回退 | 多 DoH 源部分不可达 | ✅ cloudflare/dns.google 被剔除，alidns/doh.pub 顶替（缓存文件证实取到 IP） |
| E-004 全源失败/降级 | timeout=0.001 注入自检必失败 | ✅ state=degraded、last_error=verify failed rolled back、hosts 回滚到有效 IP、坏配置不留场 |
| E-005 连续抖动防抖 | 切换失败后立即重跑 | ✅ cooldown_min=15 内跳过（EXIT 0 不重复切换） |
| E-006 崩溃恢复/锁残留 | 死 PID 残留锁文件 | ✅ flock 接管不阻塞、状态文件原子写持久化 |
| 权限 | sudo 提权 | ✅ 提权成功路径实测；提权失败场景未真机验证（root 直跑），由单元测试覆盖 |
| DNS 刷新 | resolvectl flush-caches | ✅ 执行成功（切换+恢复均实测） |

### Linux Ubuntu（执行人：小爪 ✅，Python 3.12.3，真机冒烟，2026-08-16，双版本互证）

| 场景 | 断言 | 结果 |
|---|---|---|
| E-001 全链路自愈 | 注入 127.0.0.1 github.com 故障→连续失败触发切换 | ✅ 双源适配版 8/8：hosts 段落 127.0.0.1 → 20.205.243.166，state=verifying，切换后 github.com HTTP 200（真实切换链路） |
| E-002 无感不误触 | 网络正常单轮执行 | ✅ EXIT 0 探测通过跳过，hosts 零改动 |
| E-004 全源失败/降级 | 网络受限/自检失败场景 | ✅ 官方版 7/7：② 降级宽容（网络受限 degraded 为正确行为）+ ③ degraded（no valid IP candidates）双路径覆盖 |
| E-005 连续抖动防抖 | 切换成功后冷却期内再失败 | ✅ cooldown_min=15 内不重复切换（history 不增长） |
| E-006 崩溃恢复/锁残留 | 死 PID 残留锁文件 | ✅ flock 接管不阻塞、状态文件原子写持久化 |
| 收尾 | hosts 恢复基线 | ✅ 冒烟结束 hosts 与基线完全一致（sha 校验通过） |

- 三平台 × E-001~E-007 全通过 = 可发布
- 任何版本迭代，先过回归矩阵再谈发布
