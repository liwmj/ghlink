"""入口：单轮执行（调度粒度 1min，由平台定时任务调用）。

流程：锁 → 探测 → 计数判定（成功清零）→ 触发则 取IP→备份→写入→flushdns→自检
→ 成功更新状态 / 失败回滚+degraded → 告警（冷却期去重）→ 写状态文件。

退出码：0=正常（含跳过） 1=降级/告警 2=配置/参数错误（供定时任务日志区分）
"""
import os
import sys
import time
from typing import Any, Dict

from . import config as cfgmod
from . import hosts_manager, lock, notifier, platform_adapter, probe, resolver, state


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
        ok = probe.round_ok(results)
        st["probe"]["targets"] = {h: {"ok": bool(r.get("ok"))} for h, r in results.items()}

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

        # 4) 达到阈值 → 自愈：对全部探测域名取 IP → 写 hosts → 自检
        st["state"] = "switching"
        st["switched_at"] = time.time()
        st["verify_success"] = 0
        # 提醒2: 提权 exit 前先落盘 switching 状态（Windows runas 后旧进程退出不丢标记）
        state.save(st_path, st)
        resolver_cfg = cfg.get("resolver", {})
        # P1-2: 替换覆盖全部探测域名（与探测口径一致，避免只换 github.com 其他域名仍走污染 DNS）
        entries: Dict[str, list] = {}
        ok_candidates = True
        for tgt in targets:
            cands = resolver.resolve_best(tgt, resolver_cfg)
            if not cands:
                ok_candidates = False
                break
            entries[tgt] = cands
        if not ok_candidates or not entries:
            st["state"] = "degraded"
            st["last_error"] = "no valid IP candidates"
            if notify_enabled and webhook and notifier.should_alert(st, _cooldown_sec(cfg)):
                notifier.send(f"[ghlink] 无法获取可用 IP，进入 degraded：{st['last_error']}", webhook)
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
        if not hosts_manager.verify_after_apply(targets, timeout):
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
        st["history"].append({
            "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "domain": ",".join(targets),
            "ip": first_ip,
            "trigger": f"{failed} consecutive failures",
        })
        if notify_enabled and webhook and notifier.should_alert(st, _cooldown_sec(cfg)):
            notifier.send(f"[ghlink] 已自动切换 IP {first_ip}（触发：连续 {failed} 次失败）", webhook)
            notifier.mark_alerted(st)
        state.save(st_path, st)
        return 0


def main() -> None:
    try:
        sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else "config.json"))
    except Exception as exc:  # 兜底：任何异常不裸崩，记日志退出 1
        print(f"[ghlink] fatal: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
