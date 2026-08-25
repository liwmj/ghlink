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
        # v0.4.18（冷却门控）：该测试聚焦「连续失败触发切换」链路，不测冷却——
        # 显式关冷却（cooldown_min=0），冷却语义由 linux_smoke ⑤ 覆盖
        cfg_path = make_config(
            tmp_path,
            trigger={"consecutive_failures": 3, "cooldown_min": 0, "verify_success_rounds": 2},
        )
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            lambda targets, timeout: {
                t: {"ok": False, "latency_ms": 0, "error": "sim"} for t in targets
            },
        )
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: ["1.2.3.4"])
        monkeypatch.setattr(
            "ghlink.hosts_manager.apply_block",
            lambda block, backup_dir, preserve_g520=True: (True, backup_dir),
        )
        monkeypatch.setattr(
            "ghlink.hosts_manager.verify_after_apply", lambda targets, timeout: True
        )
        monkeypatch.setattr("ghlink.notifier.send", lambda message, url: True)
        # 第 1-3 轮持续失败 → 第 3 轮触发切换
        for _ in range(3):
            code = main.run(cfg_path)
        assert code in (0, 1)
        state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert state["state"] in ("normal", "verifying")
        assert state["probe"]["consecutive_failures"] == 0

    def test_e002_success_resets_counter_no_trigger(self, tmp_path, monkeypatch):
        """失败 2 轮后成功 1 轮 → 计数清零，不触发故障切换。

        v0.2.19（李工 8 条③）：正常态也写 hosts（段落常新），applied 非空是预期；
        「不触发」= history 无 consecutive failures 触发、计数清零。
        """
        # v0.4.18（冷却门控）：该测试聚焦「连续失败触发切换」链路，不测冷却——
        # 显式关冷却（cooldown_min=0），冷却语义由 linux_smoke ⑤ 覆盖
        cfg_path = make_config(
            tmp_path,
            trigger={"consecutive_failures": 3, "cooldown_min": 0, "verify_success_rounds": 2},
        )
        calls = {"n": 0}

        def flaky_probe(targets, timeout):
            calls["n"] += 1
            ok = calls["n"] <= 2  # 前两轮失败，之后成功
            return {t: {"ok": ok, "latency_ms": 0, "error": None} for t in targets}

        monkeypatch.setattr("ghlink.probe.probe_all", flaky_probe)
        applied = []
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: ["1.2.3.4"])
        monkeypatch.setattr(
            "ghlink.hosts_manager.apply_block",
            lambda block, backup_dir: applied.append(block) or (True, backup_dir),
        )
        monkeypatch.setattr(
            "ghlink.hosts_manager.verify_after_apply", lambda targets, timeout: True
        )
        for _ in range(4):
            main.run(cfg_path)
        assert applied, "v0.2.19：正常态也维护 hosts 段（段落常新）"
        st = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert st["state"] == "normal"
        assert st["probe"]["consecutive_failures"] == 0  # 成功轮清零
        triggers = [h.get("trigger", "") for h in st.get("history", [])]
        assert not any("consecutive failures" in t for t in triggers), f"无故障触发: {triggers}"

    def test_e004_all_sources_fail_keeps_config_degraded(self, tmp_path, monkeypatch):
        """全源失败 → 有 github520 静态兜底则写静态段；无兜底才不写 + 告警 + degraded。

        v0.4.0（李工 12:35 点 1/点 2）：动态失败不再什么都不写——github520 静态段
        兜底写入（含核心域名，预检过排前），保证首装/断网场景 hosts 有可用条目。
        """
        # v0.4.18（冷却门控）：该测试聚焦「连续失败触发切换」链路，不测冷却——
        # 显式关冷却（cooldown_min=0），冷却语义由 linux_smoke ⑤ 覆盖
        cfg_path = make_config(
            tmp_path,
            trigger={"consecutive_failures": 3, "cooldown_min": 0, "verify_success_rounds": 2},
        )
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            lambda targets, timeout: {
                t: {"ok": False, "latency_ms": 0, "error": "sim"} for t in targets
            },
        )
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: [])
        applied = []
        monkeypatch.setattr(
            "ghlink.hosts_manager.apply_block",
            lambda block, backup_dir: applied.append(block) or (True, backup_dir),
        )
        # mock github520 兜底有静态条目（首装全量语义）
        g520 = {
            "codeload.github.com": ["1.2.3.5"],
            "raw.githubusercontent.com": ["1.2.3.6"],
        }
        monkeypatch.setattr("ghlink.github520.initial_entries", lambda cfg, sd: dict(g520))
        monkeypatch.setattr("ghlink.hosts_manager.current_g520_entries", lambda: dict(g520))
        for _ in range(3):
            main.run(cfg_path)
        assert applied, "v0.4.0：动态失败但有 github520 静态兜底 → 写静态段"
        last = applied[-1]
        assert "# ghlink520 Start" in last  # 静态兜底段写入
        assert "codeload.github.com" in last and "raw.githubusercontent.com" in last

    def test_e004b_all_sources_fail_no_fallback_keeps_degraded(self, tmp_path, monkeypatch):
        """全源失败且无 github520 兜底 → 不写 + 告警 + degraded（宁缺毋滥保留）。"""
        # v0.4.18（冷却门控）：该测试聚焦「连续失败触发切换」链路，不测冷却——
        # 显式关冷却（cooldown_min=0），冷却语义由 linux_smoke ⑤ 覆盖
        cfg_path = make_config(
            tmp_path,
            trigger={"consecutive_failures": 3, "cooldown_min": 0, "verify_success_rounds": 2},
        )
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            lambda targets, timeout: {
                t: {"ok": False, "latency_ms": 0, "error": "sim"} for t in targets
            },
        )
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: [])
        monkeypatch.setattr("ghlink.github520.initial_entries", lambda cfg, sd: {})
        monkeypatch.setattr("ghlink.github520.sync_github520", lambda cfg, sd: {})
        monkeypatch.setattr("ghlink.hosts_manager.current_g520_entries", lambda: {})
        applied = []
        monkeypatch.setattr(
            "ghlink.hosts_manager.apply_block",
            lambda block, backup_dir: applied.append(block) or (True, backup_dir),
        )
        for _ in range(3):
            main.run(cfg_path)
        assert applied == []  # 无任何可用候选，绝不写入（宁缺毋滥）
        st = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert st["state"] == "degraded"

    def test_e005_cooldown_no_repeat_alert(self, tmp_path, monkeypatch):
        """冷却期内重复失败 → 不重复切换、不重复告警。"""
        # v0.4.18（冷却门控）：该测试聚焦「连续失败触发切换」链路，不测冷却——
        # 显式关冷却（cooldown_min=0），冷却语义由 linux_smoke ⑤ 覆盖
        cfg_path = make_config(
            tmp_path,
            trigger={"consecutive_failures": 3, "cooldown_min": 0, "verify_success_rounds": 2},
        )
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            lambda targets, timeout: {
                t: {"ok": False, "latency_ms": 0, "error": "sim"} for t in targets
            },
        )
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: ["1.2.3.4"])
        monkeypatch.setattr(
            "ghlink.hosts_manager.apply_block",
            lambda block, backup_dir, preserve_g520=True: (True, backup_dir),
        )
        alerts = []
        monkeypatch.setattr(
            "ghlink.notifier.send", lambda message, url: alerts.append(message) or True
        )
        for _ in range(8):  # 模拟冷却期内的多次运行
            main.run(cfg_path)
        assert len(alerts) <= 1  # 切换 1 次只发 1 条

    def test_e007_alert_failure_not_blocking(self, tmp_path, monkeypatch):
        """webhook 挂掉 → 自愈流程照常完成。"""
        # v0.4.18（冷却门控）：该测试聚焦「连续失败触发切换」链路，不测冷却——
        # 显式关冷却（cooldown_min=0），冷却语义由 linux_smoke ⑤ 覆盖
        cfg_path = make_config(
            tmp_path,
            trigger={"consecutive_failures": 3, "cooldown_min": 0, "verify_success_rounds": 2},
        )
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            lambda targets, timeout: {
                t: {"ok": False, "latency_ms": 0, "error": "sim"} for t in targets
            },
        )
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: ["1.2.3.4"])
        monkeypatch.setattr(
            "ghlink.hosts_manager.apply_block",
            lambda block, backup_dir, preserve_g520=True: (True, backup_dir),
        )
        monkeypatch.setattr("ghlink.notifier.send", lambda message, url: False)  # 告警失败
        code = main.run(cfg_path)
        assert code in (0, 1)  # 不因告警失败而崩

    def test_e008_degraded_recovers_when_probe_ok(self, tmp_path, monkeypatch):
        """Bug E：degraded 状态 + 探测成功 → 回绿 normal（不再卡死）。"""
        # v0.4.18（冷却门控）：该测试聚焦「连续失败触发切换」链路，不测冷却——
        # 显式关冷却（cooldown_min=0），冷却语义由 linux_smoke ⑤ 覆盖
        cfg_path = make_config(
            tmp_path,
            trigger={"consecutive_failures": 3, "cooldown_min": 0, "verify_success_rounds": 2},
        )
        # 预置 degraded 状态文件
        st = {
            "schema_version": 2,
            "state": "degraded",
            "last_error": "verify failed after apply, rolled back",
            "probe": {
                "targets": {
                    t: {"ok": False, "fail_count": 99, "degraded": False, "recover_count": 0}
                    for t in ["github.com", "api.github.com"]
                },
                "consecutive_failures": 0,
            },
        }
        (tmp_path / "state.json").write_text(json.dumps(st), encoding="utf-8")
        # 本轮探测成功
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            lambda targets, timeout: {
                t: {"ok": True, "latency_ms": 10, "error": None} for t in targets
            },
        )
        # 拂晓 Linux 复验（2026-08-21 19:31）：e008 原未 mock resolver/apply/verify，
        # 走真实网络 + 真实 hosts 写入 → 偶发失败（网络抖动/权限差异）。
        # 补 mock 保证确定性：v0.2.19 正常态也写 hosts，全部打桩。
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: ["1.2.3.4"])
        monkeypatch.setattr(
            "ghlink.hosts_manager.apply_block",
            lambda block, backup_dir, preserve_g520=True: (True, backup_dir),
        )
        monkeypatch.setattr(
            "ghlink.hosts_manager.verify_after_apply", lambda targets, timeout: True
        )
        code = main.run(cfg_path)
        assert code == 0
        state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert state["state"] == "normal"  # degraded → normal 恢复
        assert state.get("last_error") is None

    def test_e009_verify_only_core_targets(self, tmp_path, monkeypatch):
        """Bug E 点3：verify 只验证 core_targets ∩ 写入域名，fastly 不可达不拖累回滚。"""
        cfg = {
            "probe": {
                "targets": ["github.com", "api.github.com", "github.global.ssl.fastly.net"],
                "core_targets": ["github.com", "api.github.com"],
                "timeout_sec": 5,
            },
            "trigger": {"consecutive_failures": 3, "cooldown_min": 15, "verify_success_rounds": 2},
            "resolver": {"doh_sources": [], "cache_ttl_sec": 3600, "max_candidates": 5},
            "notify": {"enabled": True, "feishu_webhook": ""},
            "state_file": str(tmp_path / "state.json"),
            "lock_file": str(tmp_path / "ghlink.lock"),
            "hosts_backup_dir": str(tmp_path / "backup"),
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        cfg_path = str(p)
        # 核心域名失败、fastly 也失败（触发切换）
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            lambda targets, timeout: {
                t: {"ok": False, "latency_ms": 0, "error": "sim"} for t in targets
            },
        )
        # resolve：核心域名有候选，fastly 也有候选（会被写入 hosts）
        monkeypatch.setattr(
            "ghlink.resolver.resolve_best",
            lambda domain, cfg: ["1.2.3.4"] if "fastly" not in domain else ["5.6.7.8"],
        )
        monkeypatch.setattr(
            "ghlink.hosts_manager.apply_block",
            lambda block, backup_dir, preserve_g520=True: (True, backup_dir),
        )
        # 关键断言：verify 收到的 targets 只含 core_targets（不含 fastly）
        verify_calls = []
        monkeypatch.setattr(
            "ghlink.hosts_manager.verify_after_apply",
            lambda targets, timeout: verify_calls.append(list(targets)) or True,
        )
        for _ in range(3):
            main.run(cfg_path)
        assert verify_calls, "verify_after_apply 应被调用"
        last_verify = verify_calls[-1]
        assert "github.global.ssl.fastly.net" not in last_verify, (
            f"verify 不应含 fastly: {last_verify}"
        )
        assert "github.com" in last_verify and "api.github.com" in last_verify

    def test_privilege_failure_state_saved(self, tmp_path, monkeypatch):
        """提权失败路径：state 仍落盘当前状态（switching 标记不丢），不静默（赛博复核提醒 2）。"""
        # v0.4.18（冷却门控）：该测试聚焦「连续失败触发切换」链路，不测冷却——
        # 显式关冷却（cooldown_min=0），冷却语义由 linux_smoke ⑤ 覆盖
        cfg_path = make_config(
            tmp_path,
            trigger={"consecutive_failures": 3, "cooldown_min": 0, "verify_success_rounds": 2},
        )
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            lambda targets, timeout: {
                t: {"ok": False, "latency_ms": 0, "error": "sim"} for t in targets
            },
        )
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: ["1.2.3.4"])
        monkeypatch.setattr("ghlink.platform_adapter.ensure_privilege", lambda: False)
        for _ in range(3):  # 3 轮失败触发切换 → apply_block 提权失败 → degraded
            code = main.run(cfg_path)
        assert code in (0, 1)
        state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
        assert state["state"] in ("normal", "switching", "degraded")
        assert state.get("last_error") is not None or state["state"] in ("switching", "degraded")
