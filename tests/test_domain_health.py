"""目标域名健康度管理（v0.2）测试。

覆盖（DESIGN §9 → v0.2 实现）：
- H-001 非核心域名长期不可达 → 自动降级（从判定集/自检集剔除）
- H-002 降级后核心域名正常 → 不再误触发切换
- H-003 降级域名恢复（连续成功 recover_rounds 轮）→ 重新纳入
- H-004 核心域名永不降级
- H-005 触发切换时只替换活跃域名（降级域名不写入 hosts）
"""
import json

import pytest

from ghlink import main


def make_config(tmp_path, **overrides):
    cfg = {
        "probe": {
            "targets": ["github.com", "api.github.com", "codeload.github.com"],
            "timeout_sec": 5,
            "core_targets": ["github.com", "api.github.com"],
            "degrade_after_rounds": 3,
            "recover_rounds": 2,
        },
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


def _probe_factory(domain_ok):
    """domain_ok: {域名: bool}，构造对应 probe_all。"""

    def _probe(targets, timeout):
        return {t: {"ok": domain_ok.get(t, True), "latency_ms": 0, "error": None if domain_ok.get(t, True) else "sim"} for t in targets}

    return _probe


def _read_state(tmp_path):
    return json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))


class TestDomainHealth:
    def test_h001_noncore_degrades_after_rounds(self, tmp_path, monkeypatch):
        """非核心域名连续失败 3 轮 → 标记 degraded。"""
        cfg_path = make_config(tmp_path)
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            _probe_factory({"github.com": True, "api.github.com": True, "codeload.github.com": False}),
        )
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: ["1.2.3.4"])
        monkeypatch.setattr("ghlink.hosts_manager.apply_block", lambda block, backup_dir: (True, backup_dir))
        monkeypatch.setattr("ghlink.hosts_manager.verify_after_apply", lambda targets, timeout: True)
        for _ in range(3):
            main.run(cfg_path)
        st = _read_state(tmp_path)
        assert st["probe"]["targets"]["codeload.github.com"]["degraded"] is True
        assert st["probe"]["targets"]["github.com"]["degraded"] is False

    def test_h002_degraded_not_triggering_switch(self, tmp_path, monkeypatch):
        """codeload 降级后，核心域名正常 → 全程零切换（不误触）。"""
        cfg_path = make_config(tmp_path)
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            _probe_factory({"github.com": True, "api.github.com": True, "codeload.github.com": False}),
        )
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: ["1.2.3.4"])
        applied = []
        monkeypatch.setattr("ghlink.hosts_manager.apply_block", lambda block, backup_dir: applied.append(block) or (True, backup_dir))
        monkeypatch.setattr("ghlink.hosts_manager.verify_after_apply", lambda targets, timeout: True)
        for _ in range(6):  # 超过降级阈值 + 超过触发阈值
            main.run(cfg_path)
        assert applied == []  # 从未触发切换

    def test_h003_degraded_recovers(self, tmp_path, monkeypatch):
        """降级域名连续成功 2 轮 → 恢复纳入（degraded=False）。"""
        cfg_path = make_config(tmp_path)
        calls = {"n": 0}
        ok_map = {"github.com": True, "api.github.com": True, "codeload.github.com": False}

        def flaky(targets, timeout):
            calls["n"] += 1
            if calls["n"] > 3:  # 前 3 轮 codeload 失败，之后恢复
                ok_map["codeload.github.com"] = True
            return _probe_factory(dict(ok_map))(targets, timeout)

        monkeypatch.setattr("ghlink.probe.probe_all", flaky)
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: ["1.2.3.4"])
        monkeypatch.setattr("ghlink.hosts_manager.apply_block", lambda block, backup_dir: (True, backup_dir))
        monkeypatch.setattr("ghlink.hosts_manager.verify_after_apply", lambda targets, timeout: True)
        for _ in range(3):  # 3 轮失败 → codeload 降级
            main.run(cfg_path)
        assert _read_state(tmp_path)["probe"]["targets"]["codeload.github.com"]["degraded"] is True
        for _ in range(2):  # 2 轮成功 → 恢复
            main.run(cfg_path)
        assert _read_state(tmp_path)["probe"]["targets"]["codeload.github.com"]["degraded"] is False

    def test_h004_core_never_degrades(self, tmp_path, monkeypatch):
        """核心域名长期失败 → 永不标记 degraded。"""
        cfg_path = make_config(tmp_path)
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            _probe_factory({"github.com": False, "api.github.com": False, "codeload.github.com": True}),
        )
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: ["1.2.3.4"])
        monkeypatch.setattr("ghlink.hosts_manager.apply_block", lambda block, backup_dir: (True, backup_dir))
        monkeypatch.setattr("ghlink.hosts_manager.verify_after_apply", lambda targets, timeout: True)
        for _ in range(5):
            main.run(cfg_path)
        st = _read_state(tmp_path)
        assert st["probe"]["targets"]["github.com"]["degraded"] is False
        assert st["probe"]["targets"]["api.github.com"]["degraded"] is False

    def test_h005_switch_only_active_domains(self, tmp_path, monkeypatch):
        """触发切换时：活跃域名写入 hosts，降级域名不写入。"""
        cfg_path = make_config(tmp_path)
        # 核心域名失败触发切换，codeload 已降级
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            _probe_factory({"github.com": False, "api.github.com": False, "codeload.github.com": False}),
        )
        monkeypatch.setattr("ghlink.resolver.resolve_best", lambda domain, cfg: ["1.2.3.4"])
        blocks = []
        monkeypatch.setattr("ghlink.hosts_manager.apply_block", lambda block, backup_dir: blocks.append(block) or (True, backup_dir))
        monkeypatch.setattr("ghlink.hosts_manager.verify_after_apply", lambda targets, timeout: True)
        # 先跑 3 轮让 codeload 降级（同时核心域名累计失败）
        for _ in range(3):
            main.run(cfg_path)
        st = _read_state(tmp_path)
        assert st["probe"]["targets"]["codeload.github.com"]["degraded"] is True
        # 继续跑：核心域名连续失败达 3 → 触发切换
        for _ in range(2):
            main.run(cfg_path)
        assert blocks, "应至少触发一次切换"
        last = blocks[-1]
        assert "codeload.github.com" not in last  # 降级域名不写入
        assert "github.com" in last and "api.github.com" in last  # 核心域名写入
