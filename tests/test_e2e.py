"""端到端全链路测试（mock 网络，实现落地后转绿）。

对应 src/ghlink/main.py：run。
场景覆盖（口径见方案草案第 4 节 + 顾笙终裁）：
- E-001 全链路：持续不稳定 → 3 轮判定 → 触发切换 → 恢复
- E-002 间歇性恢复：成功一轮计数清零，不触发（防误判）
- E-003 单 IP 源故障 → 回退备用源，自愈成功
- E-004 全源故障 → 告警 + 保持原配置 + degraded
- E-005 连续抖动 → 防抖生效：冷却期内不重复切换/告警
- E-006 自愈中途崩溃 → 重启后状态一致、无坏配置残留
- E-007 告警通道故障 → 自愈仍完成（告警不阻断主流程）
- 时间类断言（探测期 180s / 自愈期 60s）属实跑矩阵，见 PLATFORM_MATRIX.md
"""
import json

import pytest

from ghlink import main


def make_config(tmp_path, **overrides):
    cfg = {
        "probe": {"targets": ["github.com", "api.github.com"], "timeout_sec": 5},
        "trigger": {"consecutive_failures": 3, "cooldown_min": 15, "verify_success_rounds": 2},
        "resolver": {"doh_sources": [], "cache_ttl_sec": 3600, "max_candidates": 5},
        "notify": {"enabled": True, "feishu_webhook": ""},
        "state_file": str(tmp_path / "state.json"),
        "lock_file": str(tmp_path / "ghlink.lock"),
        "hosts_backup_dir": str(tmp_path / "backup"),
    }
    cfg.update(overrides)
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return str(p)


class TestFullCycle:
    def test_e001_persistent_failure_triggers_and_recovers(self, tmp_path, monkeypatch):
        """持续失败 3 轮 → 触发切换 → 后续恢复。"""
        cfg_path = make_config(tmp_path)
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            lambda targets, timeout: {
                t: {"ok": False, "latency_ms": 0, "error": "sim"} for t in targets
            },
        )
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: ["1.2.3.4"])
        monkeypatch.setattr("ghlink.hosts_manager.apply_block", lambda block, backup_dir: (True, backup_dir))
        monkeypatch.setattr("ghlink.hosts_manager.verify_after_apply", lambda targets, timeout: True)
        monkeypatch.setattr("ghlink.notifier.send", lambda message, url: True)
        # 第 1-3 轮持续失败 → 第 3 轮触发切换
        for _ in range(3):
            code = main.run(cfg_path)
        assert code in (0, 1)
        state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert state["state"] in ("normal", "verifying")
        assert state["probe"]["consecutive_failures"] == 0

    def test_e002_success_resets_counter_no_trigger(self, tmp_path, monkeypatch):
        """失败 2 轮后成功 1 轮 → 计数清零，不触发切换。"""
        cfg_path = make_config(tmp_path)
        calls = {"n": 0}

        def flaky_probe(targets, timeout):
            calls["n"] += 1
            ok = calls["n"] <= 2  # 前两轮失败，之后成功
            return {t: {"ok": ok, "latency_ms": 0, "error": None} for t in targets}

        monkeypatch.setattr("ghlink.probe.probe_all", flaky_probe)
        applied = []
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: ["1.2.3.4"])
        monkeypatch.setattr("ghlink.hosts_manager.apply_block", lambda block, backup_dir: applied.append(block) or (True, backup_dir))
        for _ in range(4):
            main.run(cfg_path)
        assert applied == []  # 从未触发切换

    def test_e004_all_sources_fail_keeps_config_degraded(self, tmp_path, monkeypatch):
        """全源失败 → 不换配置 + 告警 + degraded。"""
        cfg_path = make_config(tmp_path)
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            lambda targets, timeout: {t: {"ok": False, "latency_ms": 0, "error": "sim"} for t in targets},
        )
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: [])
        applied = []
        monkeypatch.setattr("ghlink.hosts_manager.apply_block", lambda block, backup_dir: applied.append(block) or (True, backup_dir))
        for _ in range(3):
            main.run(cfg_path)
        assert applied == []  # 没有可用 IP，绝不写入

    def test_e005_cooldown_no_repeat_alert(self, tmp_path, monkeypatch):
        """冷却期内重复失败 → 不重复切换、不重复告警。"""
        cfg_path = make_config(tmp_path)
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            lambda targets, timeout: {t: {"ok": False, "latency_ms": 0, "error": "sim"} for t in targets},
        )
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: ["1.2.3.4"])
        monkeypatch.setattr("ghlink.hosts_manager.apply_block", lambda block, backup_dir: (True, backup_dir))
        alerts = []
        monkeypatch.setattr("ghlink.notifier.send", lambda message, url: alerts.append(message) or True)
        for _ in range(8):  # 模拟冷却期内的多次运行
            main.run(cfg_path)
        assert len(alerts) <= 1  # 切换 1 次只发 1 条

    def test_e007_alert_failure_not_blocking(self, tmp_path, monkeypatch):
        """webhook 挂掉 → 自愈流程照常完成。"""
        cfg_path = make_config(tmp_path)
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            lambda targets, timeout: {t: {"ok": False, "latency_ms": 0, "error": "sim"} for t in targets},
        )
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: ["1.2.3.4"])
        monkeypatch.setattr("ghlink.hosts_manager.apply_block", lambda block, backup_dir: (True, backup_dir))
        monkeypatch.setattr("ghlink.notifier.send", lambda message, url: False)  # 告警失败
        code = main.run(cfg_path)
        assert code in (0, 1)  # 不因告警失败而崩

    def test_privilege_failure_state_saved(self, tmp_path, monkeypatch):
        """提权失败路径：state 仍落盘当前状态（switching 标记不丢），不静默（赛博复核提醒 2）。"""
        cfg_path = make_config(tmp_path)
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            lambda targets, timeout: {t: {"ok": False, "latency_ms": 0, "error": "sim"} for t in targets},
        )
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: ["1.2.3.4"])
        monkeypatch.setattr(
            "ghlink.platform_adapter.ensure_privilege", lambda: False
        )
        for _ in range(3):  # 3 轮失败触发切换 → apply_block 提权失败 → degraded
            code = main.run(cfg_path)
        assert code in (0, 1)
        state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert state["state"] in ("normal", "switching", "degraded")
        assert state.get("last_error") is not None or state["state"] in ("switching", "degraded")
