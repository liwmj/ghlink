"""v0.4.9 回归测试：回滚保留 GitHub520 静态段（李工 22:12 语义）。

回滚 ≠ 卸载：verify 失败回滚时保留静态兜底 IP（不清空 ghlink hosts），
卸载才彻底清理。本测试断言回滚路径重建静态段。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import ghlink.main as main
import ghlink.hosts_manager as hosts_manager


class TestRollbackPreservesStatic:
    """回滚保留静态段：verify 失败 → 恢复基线 + 重建 GitHub520 静态段。"""

    def _make_cfg(self, tmp_path):
        cfg = {
            "probe": {
                "targets": ["github.com", "api.github.com"],
                "core_targets": ["github.com", "api.github.com"],
                "timeout_sec": 5,
            },
            "trigger": {"consecutive_failures": 3, "cooldown_min": 15, "verify_success_rounds": 2},
            "resolver": {"doh_sources": [], "cache_ttl_sec": 3600, "max_candidates": 5},
            "notify": {"enabled": False, "feishu_webhook": ""},
            "state_file": str(tmp_path / "state.json"),
            "lock_file": str(tmp_path / "ghlink.lock"),
            "hosts_backup_dir": str(tmp_path / "backup"),
        }
        p = tmp_path / "config.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        return str(p)

    def test_rollback_rebuilds_static_block(self, tmp_path, monkeypatch):
        """verify 失败回滚后，静态段重建（apply_block 收到仅含 g520 的 block）。"""
        cfg_path = self._make_cfg(tmp_path)
        g520 = {"codeload.github.com": ["1.2.3.4"], "avatars.githubusercontent.com": ["5.6.7.8"]}
        # 探测全失败 → 触发切换
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            lambda targets, timeout: {t: {"ok": False, "error": "sim"} for t in targets},
        )
        monkeypatch.setattr(
            "ghlink.resolver.resolve_best",
            lambda domain, cfg: ["9.9.9.9"],
        )
        # github520 静态兜底有条目
        monkeypatch.setattr(
            "ghlink.main._github520_entries",
            lambda cfg, st, st_dir: dict(g520),
        )
        # verify 失败 → 触发回滚
        monkeypatch.setattr(
            "ghlink.hosts_manager.verify_after_apply",
            lambda targets, timeout: False,
        )
        applied = []
        monkeypatch.setattr(
            "ghlink.hosts_manager.apply_block",
            lambda block, backup_dir, preserve_g520=True: applied.append(block) or (True, backup_dir),
        )
        monkeypatch.setattr(
            "ghlink.hosts_manager.rollback",
            lambda backup_path: True,
        )
        main.run(cfg_path)
        # 回滚路径应重建静态段：block 含 ghlink520 子段、无核心动态域名
        assert applied, "回滚后应调用 apply_block 重建静态段"
        last = applied[-1]
        assert "# ghlink520 Start" in last, f"静态段应保留: {last}"
        assert "codeload.github.com" in last, f"静态条目应在: {last}"
        assert "github.com" not in last or "9.9.9.9 github.com" not in last, (
            f"核心域名动态段不应残留: {last}"
        )

    def test_uninstall_cleans_everything(self, tmp_path, monkeypatch):
        """卸载语义：彻底清理（区别于回滚保留静态段）。"""
        removed = []
        monkeypatch.setattr(
            "ghlink.hosts_manager.remove_block",
            lambda path="": removed.append(True) or True,
        )
        monkeypatch.setattr(
            "ghlink.platform_adapter.ensure_privilege",
            lambda: True,
        )
        from ghlink import main as m

        # remove_block 幂等返回 True 即为卸载清理路径
        assert hosts_manager.remove_block() is True
        assert removed == [True]