"""入口：单轮执行（调度粒度 1min，由平台定时任务调用）。

流程：锁 → 探测 → 计数判定（成功清零）→ 触发则 取IP→备份→写入→flushdns→自检
→ 成功更新状态 / 失败回滚+degraded → 告警（冷却期去重）→ 写状态文件。

退出码：0=正常（含跳过） 1=降级/告警 2=配置/参数错误（供定时任务日志区分）
"""

import sys
import time
from typing import Any, Dict

from . import config as cfgmod
from . import hosts_manager, lock, notifier, probe, resolver, service, state


def _state_path(cfg: Dict[str, Any]) -> str:
    return cfg.get("state_file", "ghlink_status.json")


def _lock_path(cfg: Dict[str, Any]) -> str:
    return cfg.get("lock_file", "ghlink.lock")


def _backup_dir(cfg: Dict[str, Any]) -> str:
    return cfg.get("hosts_backup_dir", "backup")


def _targets(cfg: Dict[str, Any]) -> list:
    return cfg.get("probe", {}).get("targets", ["github.com"])


def _timeout(cfg: Dict[str, Any]) -> float:
    return float(cfg.get("probe", {}).get("timeout_sec", 5))


def _cooldown_sec(cfg: Dict[str, Any]) -> int:
    return int(cfg.get("trigger", {}).get("cooldown_min", 15)) * 60


def _update_state(st: Dict[str, Any], **fields: Any) -> None:
    for k, v in fields.items():
        st[k] = v


def run(config_path: str = "config.json") -> int:
    """单轮执行主流程，返回退出码。"""
    cfg = cfgmod.load_config(config_path)
    targets = _targets(cfg)
    timeout = _timeout(cfg)
    consecutive_needed = int(cfg.get("trigger", {}).get("consecutive_failures", 3))
    verify_rounds = int(cfg.get("trigger", {}).get("verify_success_rounds", 2))

    st_path = _state_path(cfg)
    st = state.load(st_path)
    webhook = cfg.get("notify", {}).get("feishu_webhook", "")
    notify_enabled = bool(cfg.get("notify", {}).get("enabled", True))

    with lock.acquire(_lock_path(cfg)) as got:
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

        # 2) 计数判定：成功清零，失败累加
        if ok:
            st["probe"]["consecutive_failures"] = 0
            if st.get("state") in ("switching", "verifying"):
                # P0-3: 切换后需连续 verify_rounds 轮成功才恢复 normal
                st["verify_success"] = st.get("verify_success", 0) + 1
                if st["verify_success"] >= verify_rounds:
                    st["state"] = "normal"
                    st["verify_success"] = 0
            state.save(st_path, st)
            return 0

        st["probe"]["consecutive_failures"] = st.get("probe", {}).get("consecutive_failures", 0) + 1
        failed = st["probe"]["consecutive_failures"]

        # P0-2: 冷却期判断基于 switched_at 时间差，与 state 解耦（避免切换成功后冷却失效）
        switched_at = float(st.get("switched_at") or 0)
        if switched_at and (time.time() - switched_at) < _cooldown_sec(cfg):
            state.save(st_path, st)
            return 0

        # 3) 未达阈值：仅记录
        if failed < consecutive_needed:
            state.save(st_path, st)
            return 0

        # 4) 达到阈值 → 自愈：对活跃探测域名取 IP → 写 hosts → 自检
        st["state"] = "switching"
        st["switched_at"] = time.time()
        st["verify_success"] = 0
        # 提醒2: 提权 exit 前先落盘 switching 状态（Windows runas 后旧进程退出不丢标记）
        state.save(st_path, st)
        resolver_cfg = cfg.get("resolver", {})
        # P1-2: 替换覆盖全部活跃探测域名（降级域名不写入，避免坏域名拖累切换）
        entries: Dict[str, list] = {}
        ok_candidates = True
        for tgt in active:
            cands = resolver.resolve_best(tgt, resolver_cfg)
            if not cands:
                ok_candidates = False
                break
            entries[tgt] = cands
        if not ok_candidates or not entries:
            st["state"] = "degraded"
            st["last_error"] = "no valid IP candidates"
            if notify_enabled and webhook and notifier.should_alert(st, _cooldown_sec(cfg)):
                notifier.send(
                    f"[ghlink] 无法获取可用 IP，进入 degraded：{st['last_error']}", webhook
                )
                notifier.mark_alerted(st)
            state.save(st_path, st)
            return 1

        block = hosts_manager.build_block(entries)
        ok_apply, backup_path = hosts_manager.apply_block(block, _backup_dir(cfg))
        if not ok_apply:
            st["state"] = "degraded"
            st["last_error"] = "hosts write failed (privilege/permission)"
            if notify_enabled and webhook and notifier.should_alert(st, _cooldown_sec(cfg)):
                notifier.send(f"[ghlink] hosts 写入失败（权限/降级）：{st['last_error']}", webhook)
                notifier.mark_alerted(st)
            state.save(st_path, st)
            return 1

        st["state"] = "verifying"
        if not hosts_manager.verify_after_apply(active, timeout):
            # P0-1: 自检失败 → 立即回滚 + degraded（坏配置绝不留场）
            hosts_manager.rollback(backup_path)
            st["state"] = "degraded"
            st["last_error"] = "verify failed after apply, rolled back"
            if notify_enabled and webhook and notifier.should_alert(st, _cooldown_sec(cfg)):
                notifier.send(f"[ghlink] 自检失败已回滚+降级：{st['last_error']}", webhook)
                notifier.mark_alerted(st)
            state.save(st_path, st)
            return 1

        # 5) 自愈成功：进入 verifying，需连续 verify_rounds 轮成功才 normal（P0-3）
        st["state"] = "verifying"
        st["verify_success"] = 1  # 本轮回滚自检已通过，算第一轮成功
        st["probe"]["consecutive_failures"] = 0  # 切换成功：失败计数重置，重新累计
        first_ip = next(iter(entries.values()))[0]
        st["current_ip"] = first_ip
        st["last_error"] = None
        st["history"] = (st.get("history") or [])[-19:]
        st["history"].append(
            {
                "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "domain": ",".join(active),
                "ip": first_ip,
                "trigger": f"{failed} consecutive failures",
            }
        )
        if notify_enabled and webhook and notifier.should_alert(st, _cooldown_sec(cfg)):
            notifier.send(
                f"[ghlink] 已自动切换 IP {first_ip}（触发：连续 {failed} 次失败）", webhook
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
    # Windows 打包版（PyInstaller frozen）：双击快捷方式无参数 → 默认进托盘
    # （v0.3 修复：原无参数=单轮探测，跑完退出被误认为闪退）
    if not args:
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            from . import tray

            sys.exit(tray.main())
        # 非打包/其他平台：兼容旧用法，当作 config 路径单轮运行
        sys.exit(run("config.json"))

    first = args[0]
    if first in ("run",):
        cfg = args[1] if len(args) > 1 else "config.json"
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
    if first in ("--version", "-V"):
        from . import __version__

        print(f"ghlink {__version__}")
        sys.exit(0)
    if first in ("--help", "-h"):
        print("用法: ghlink [run|enable|disable|status|tray] [config.json]")
        print("  run      单轮探测+自愈（默认，可省略）")
        print("  enable   注册定时任务（1 分钟粒度，需管理员/root）")
        print("  disable  移除定时任务")
        print("  status   显示当前状态与值守情况")
        print("  tray     系统托盘（Windows/macOS，需安装包版；Linux 纯 CLI）")
        sys.exit(0)
    # 兼容旧用法：直接传 config 路径
    sys.exit(run(first))


if __name__ == "__main__":
    main()
