"""入口：单轮执行（调度粒度 1h（v0.2.18 起），由平台定时任务调用）。

v0.2.19 流程变更（李工 8 条 ③⑥）：
- 正常态也保持 hosts 段存在：每轮对活跃域名解析最新 IP 并落盘（内容无变化不写），
  保证全局访问生效（不再只有故障切换才写）
- GitHub520 静态兜底：初始化时合入一次（状态标记 github520_initialized），
  后续只做动态更新（从现有 hosts 保留子段，不重复拉取/合入）
- 故障语义保留：探测失败计数/告警冷却/降级域名剔除照旧；自愈=刷新 hosts 段

流程：锁 → 探测 → GitHub520（初始化/保留）→ 解析活跃域名 IP → 写 hosts（变化才写）
→ 自检（实际落盘才验）→ 成功更新状态 / 失败回滚+degraded → 告警（冷却去重）→ 写状态。

退出码：0=正常（含跳过） 1=降级/告警 2=配置/参数错误（供定时任务日志区分）
"""

import os
import sys
import time
from typing import Any, Dict

from . import config as cfgmod
from . import hosts_manager, lock, notifier, probe, resolver, service, state

# SonarCloud S1192：默认配置文件名常量（重复字面量 6 处收敛）
DEFAULT_CONFIG_FILE = "config.json"


def _config_base(config_path: str) -> str:
    """路径解析基准目录（v0.2.19 ⑧②：锁/状态/备份路径绝不依赖 cwd）。

    - config 文件存在 → 其所在目录
    - config 路径为显式指定（非默认 config.json，即使文件暂不存在）→ 其所在目录
    - 默认/未知参数 → 平台默认目录：
      Windows %ProgramData%\\ghlink（SYSTEM 可写）／root /etc/ghlink／其他 ~/.ghlink
    """
    if config_path and config_path != DEFAULT_CONFIG_FILE:
        return os.path.dirname(os.path.abspath(config_path))
    if sys.platform == "win32":
        return os.path.join(
            os.environ.get("PROGRAMDATA", r"C:\ProgramData"), "ghlink"
        )
    if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0:
        return "/etc/ghlink"
    return os.path.join(os.path.expanduser("~"), ".ghlink")


def _resolve_rel(value: str, config_path: str) -> str:
    """相对路径 → 相对 config 目录解析；绝对路径原样返回。

    2026-08-17 赛博补强（Bug B 根治）：老配置/用户自定义的相对路径
    state_file/lock_file/backup 仍会依赖 cwd（systemd 写 /、CLI 读 cwd），
    必须统一相对 config 目录解析，不靠打包补丁。
    2026-08-21（李工 8 条⑧）：config 不存在/未知参数时基准落到平台默认目录，
    绝不依赖 cwd——修 ghlink.exe version 从任意目录跑崩溃（锁路径白名单 ValueError）。
    """
    if not value or os.path.isabs(value):
        return value
    return os.path.join(_config_base(config_path), value)


def _state_path(cfg: Dict[str, Any], config_path: str = DEFAULT_CONFIG_FILE) -> str:
    return _resolve_rel(cfg.get("state_file", "ghlink_status.json"), config_path)


def _lock_path(cfg: Dict[str, Any], config_path: str = DEFAULT_CONFIG_FILE) -> str:
    return _resolve_rel(cfg.get("lock_file", "ghlink.lock"), config_path)


def _backup_dir(cfg: Dict[str, Any], config_path: str = DEFAULT_CONFIG_FILE) -> str:
    return _resolve_rel(cfg.get("hosts_backup_dir", "backup"), config_path)


def _targets(cfg: Dict[str, Any]) -> list:
    return cfg.get("probe", {}).get("targets", ["github.com"])


def _timeout(cfg: Dict[str, Any]) -> float:
    return float(cfg.get("probe", {}).get("timeout_sec", 5))


def _cooldown_sec(cfg: Dict[str, Any]) -> int:
    return int(cfg.get("trigger", {}).get("cooldown_min", 15)) * 60


def _update_state(st: Dict[str, Any], **fields: Any) -> None:
    for k, v in fields.items():
        st[k] = v


def _github520_entries(cfg: Dict[str, Any], st: Dict[str, Any], st_dir: str) -> Dict[str, list]:
    """GitHub520 静态兜底条目（v0.2.19 ③⑥ 语义）。

    规则：
    - 初始化（state 无 github520_initialized 标记）：拉取一次并合入 hosts，置标记
    - 已初始化：从现有 hosts 保留子段（动态更新不重复拉取、不丢失兜底）
    - hosts 子段被清且缓存空：允许再拉一次恢复（鲁棒，不重置初始化语义）
    核心域名（github.com/api.github.com）永远由 ghlink 动态自愈兜底，不写社区 IP。
    """
    try:
        from . import github520 as g520

        # 1) 已初始化 → 优先保留现有 hosts 子段
        preserved = hosts_manager.current_g520_entries()
        if st.get("github520_initialized") and preserved:
            st.setdefault("github520", {})["last_sync"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            return preserved

        # 2) 未初始化：拉取一次（成功才置标记）
        if not st.get("github520_initialized"):
            fetched = g520.sync_github520(cfg, st_dir)
            if fetched:
                st["github520_initialized"] = True
                st.setdefault("github520", {})["last_sync"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                st["github520"]["domains"] = len(fetched)
                return fetched

        # 3) 已初始化但 hosts 子段被清：缓存兜底 → 缓存空再拉一次
        cached = g520.load_cached(st_dir)
        if cached:
            st.setdefault("github520", {})["last_sync"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            return cached
        fetched = g520.sync_github520(cfg, st_dir)
        if fetched:
            st.setdefault("github520", {})["last_sync"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            st["github520"]["domains"] = len(fetched)
            return fetched
        return {}
    except Exception:
        return {}


def run(config_path: str = DEFAULT_CONFIG_FILE) -> int:
    """单轮执行主流程，返回退出码。"""
    cfg = cfgmod.load_config(config_path)
    targets = _targets(cfg)
    timeout = _timeout(cfg)
    consecutive_needed = int(cfg.get("trigger", {}).get("consecutive_failures", 3))
    st_path = _state_path(cfg, config_path)
    st = state.load(st_path)
    webhook = cfg.get("notify", {}).get("feishu_webhook", "")
    notify_enabled = bool(cfg.get("notify", {}).get("enabled", True))

    with lock.acquire(_lock_path(cfg, config_path)) as got:
        if not got:
            # 已有实例在跑，本轮跳过（不阻塞不排队）
            return 0

        # 1) 探测
        results = probe.probe_all(targets, timeout)
        # 目标域名健康度管理（v0.2）：更新每域名连续失败计数/降级状态
        active = _update_domain_health(st, targets, results, cfg)
        # 仅刷新 ok 字段，保留 fail_count/degraded/recover_count 等健康度字段
        for h, r in results.items():
            st["probe"]["targets"].setdefault(h, {})["ok"] = bool(r.get("ok"))
        ok = probe.round_ok({h: r for h, r in results.items() if h in active})

        # 2) GitHub520 静态兜底：初始化合一次，后续保留现有子段（v0.2.19 ⑥）
        st_dir = os.path.dirname(os.path.abspath(st_path)) if st_path else ""
        github520_entries = _github520_entries(cfg, st, st_dir)
        # 降级域名不写入（与 active 判定一致：坏域名不入场，v0.2.18 语义保留）
        for d in list(github520_entries.keys()):
            if st.get("probe", {}).get("targets", {}).get(d, {}).get("degraded"):
                del github520_entries[d]

        # 3) 解析活跃域名动态 IP（正常态也解析，保证 hosts 段常新）
        entries: Dict[str, list] = {}
        ok_candidates = True
        resolver_cfg = cfg.get("resolver", {})
        for tgt in active:
            cands = resolver.resolve_best(tgt, resolver_cfg)
            if not cands:
                ok_candidates = False
                break
            entries[tgt] = cands
        if not ok_candidates or not entries:
            if ok:
                # v0.2.19（李工 8 条③）：正常态解析失败不降级——probe 网络本就通，
                # hosts 段是优化不是必需（root 值守可补写），保持 normal 仅记录
                st["state"] = "normal"
                st["last_error"] = "resolver failed (normal state kept)"
                state.save(st_path, st)
                return 0
            st["state"] = "degraded"
            st["last_error"] = "no valid IP candidates"
            # v0.2.8：缓存也空时补充提示（帮助区分全源失败 vs 缓存兜底失效）
            if not st.get("source_health"):
                st["last_error"] += " (candidates unreachable, no cache fallback)"
            if notify_enabled and webhook and notifier.should_alert(st, _cooldown_sec(cfg)):
                notifier.send(
                    f"[ghlink] 无法获取可用 IP，进入 degraded：{st['last_error']}", webhook
                )
                notifier.mark_alerted(st)
            state.save(st_path, st)
            return 1

        # 4) 构建复合段落（动态段 + GitHub520 子段）并写入（内容无变化自动跳过）
        block = hosts_manager.build_combined_block(entries, github520_entries)
        st["state"] = "switching"  # 落盘前标记（提权 exit 前已落盘，见下）
        st["verify_success"] = 0
        # 提醒2: 提权 exit 前先落盘 switching 状态（Windows runas 后旧进程退出不丢标记）
        state.save(st_path, st)
        ok_apply, backup_path = hosts_manager.apply_block(block, _backup_dir(cfg, config_path))
        if not ok_apply:
            if ok:
                # v0.2.19（李工 8 条③）：正常态写 hosts 失败不降级——网络本来就通，
                # hosts 段由 root 值守（systemd/launchd/schtasks）负责写，普通用户手动
                # 单轮跑无权限时保持 normal，避免误报 degraded（E-008 恢复链路依赖此语义）
                st["state"] = "normal"
                st["last_error"] = None
                state.save(st_path, st)
                return 0
            st["state"] = "degraded"
            st["last_error"] = "hosts write failed (privilege/permission)"
            if notify_enabled and webhook and notifier.should_alert(st, _cooldown_sec(cfg)):
                notifier.send(f"[ghlink] hosts 写入失败（权限/降级）：{st['last_error']}", webhook)
                notifier.mark_alerted(st)
            state.save(st_path, st)
            return 1

        wrote = bool(backup_path)  # 真正落盘才需要自检（内容未变化不算写入）
        if wrote:
            # Bug E 点3（赛博定案）：verify 只验证核心域名 ∩ 写入域名——
            # fastly 等非核心域名即使写入 hosts 也不参与 verify（它本身不可达，
            # 参与会误判回滚；应走健康度降级剔除，不拖累整体切换）
            probe_cfg = cfg.get("probe", {})
            core_targets = set(probe_cfg.get("core_targets", ["github.com", "api.github.com"]))
            verify_targets = [t for t in entries if t in core_targets] or list(entries.keys())
            st["state"] = "verifying"
            if not hosts_manager.verify_after_apply(verify_targets, timeout):
                # P0-1: 自检失败 → 立即回滚 + degraded（坏配置绝不留场）
                hosts_manager.rollback(backup_path)
                st["state"] = "degraded"
                st["last_error"] = "verify failed after apply, rolled back"
                if notify_enabled and webhook and notifier.should_alert(st, _cooldown_sec(cfg)):
                    notifier.send(f"[ghlink] 自检失败已回滚+降级：{st['last_error']}", webhook)
                    notifier.mark_alerted(st)
                state.save(st_path, st)
                return 1

        # 5) 成功：hosts 已保持最新（v0.2.19 ③：正常态也写 hosts 段，全局访问生效）
        failed_before = st.get("probe", {}).get("consecutive_failures", 0)
        if wrote or ok:
            # 自愈成功（hosts 段已刷新）或探测正常 → 失败计数清零，重新累计
            st["probe"]["consecutive_failures"] = 0
        else:
            st["probe"]["consecutive_failures"] = failed_before + 1
        st["state"] = "normal"
        st["verify_success"] = 0
        first_ip = next(iter(entries.values()))[0]
        st["current_ip"] = first_ip
        st["last_error"] = None
        if wrote:
            st["last_switched_at"] = time.time()  # v0.2.18 方案④：统一字段名
            failed = failed_before + (0 if ok else 1)
            trigger = (
                f"{failed} consecutive failures"
                if failed >= consecutive_needed
                else "periodic refresh"
            )
            st["history"] = (st.get("history") or [])[-19:]
            st["history"].append(
                {
                    "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "domain": ",".join(active),
                    "ip": first_ip,
                    "trigger": trigger,
                }
            )
            if notify_enabled and webhook and notifier.should_alert(st, _cooldown_sec(cfg)):
                notifier.send(
                    f"[ghlink] 已更新 hosts IP {first_ip}（触发：{trigger}）", webhook
                )
                notifier.mark_alerted(st)
        state.save(st_path, st)
        return 0


def _update_domain_health(
    st: Dict[str, Any], targets: list, results: Dict[str, Any], cfg: Dict[str, Any]
) -> list:
    """目标域名健康度管理（v0.2）。

    规则：
    - 核心域名（probe.core_targets）永不降级
    - 非核心域名连续失败 degrade_after_rounds 轮 → 标记 degraded（从探测判定集/自检集剔除）
    - 降级域名连续成功 recover_rounds 轮 → 恢复纳入
    返回本轮活跃（未降级）域名列表。
    """
    probe_cfg = cfg.get("probe", {})
    core = set(probe_cfg.get("core_targets", ["github.com", "api.github.com"]))
    degrade_after = int(probe_cfg.get("degrade_after_rounds", 10) or 10)
    recover_rounds = int(probe_cfg.get("recover_rounds", 2) or 2)

    per = st.setdefault("probe", {}).setdefault("targets", {})
    active = []
    for t in targets:
        entry = per.setdefault(
            t, {"ok": False, "fail_count": 0, "degraded": False, "recover_count": 0}
        )
        r = results.get(t, {})
        ok = bool(r.get("ok"))
        entry["ok"] = ok
        if ok:
            if entry.get("degraded"):
                entry["recover_count"] = entry.get("recover_count", 0) + 1
                if entry["recover_count"] >= recover_rounds:
                    entry["degraded"] = False
                    entry["recover_count"] = 0
                    entry["fail_count"] = 0
            else:
                entry["fail_count"] = 0
        else:
            entry["recover_count"] = 0
            if t in core:
                entry["fail_count"] = entry.get("fail_count", 0) + 1  # 核心域名记录但不降级
            else:
                entry["fail_count"] = entry.get("fail_count", 0) + 1
                if entry["fail_count"] >= degrade_after:
                    entry["degraded"] = True
        if not entry.get("degraded"):
            active.append(t)
    return active


def main() -> None:
    """CLI 入口：支持子命令 run / enable / disable / status / tray。"""

    # Windows 控制台默认 GBK，打印中文会 charmap 编码报错（W3 实测暴露）
    # 统一强制 UTF-8 输出，errors=replace 兜底非法字符
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    args = sys.argv[1:]
    # Windows 打包版（PyInstaller frozen）：无参数默认进托盘（方案 B，李工 16:57 拍板）
    # 覆盖：双击 exe / 双击快捷方式 / 手动无参运行 → 均托盘常驻；子命令不受影响
    if not args:
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            from . import tray

            sys.exit(tray.main())
        # 非打包/其他平台：兼容旧用法，当作 config 路径单轮运行
        sys.exit(run(DEFAULT_CONFIG_FILE))

    first = args[0]
    if first in ("run",):
        cfg = args[1] if len(args) > 1 else DEFAULT_CONFIG_FILE
        sys.exit(run(cfg))
    if first == "enable":
        sys.exit(service.enable())
    if first == "disable":
        sys.exit(service.disable())
    if first == "status":
        sys.exit(service.status())
    if first == "tray":
        from . import tray

        sys.exit(tray.main())
    if first in ("--version", "-V", "version"):
        from . import __version__

        print(f"ghlink {__version__}")
        sys.exit(0)
    if first in ("--help", "-h", "help"):
        print("用法: ghlink [run|enable|disable|status|tray|version|help] [config.json]")
        print("  run      单轮探测+自愈（默认，可省略）")
        print("  enable   注册定时任务（1 小时粒度，需管理员/root）")
        print("  disable  移除定时任务")
        print("  status   显示当前状态与值守情况")
        print("  tray     系统托盘（Windows/macOS，需安装包版；Linux 纯 CLI）")
        print("  version  显示版本号")
        sys.exit(0)
    # v0.2.19（李工 8 条⑧）：未知参数不再裸当 config 路径跑 run()——
    # 之前 ghlink.exe version 会触发锁路径依赖 cwd → 非白名单目录崩溃。
    # 仅当参数是已存在的 config 文件时才兼容旧用法，否则提示帮助。
    if os.path.exists(first):
        sys.exit(run(first))
    print(
        f"[ghlink] 未知命令或 config 不存在: {first}（可用 ghlink --help 查看用法）",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
