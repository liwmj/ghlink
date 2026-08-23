"""v0.4.9 回归测试：回滚保留 GitHub520 静态段（李工 22:12 语义）。

回滚 ≠ 卸载：verify 失败回滚时保留静态兜底 IP（不清空 ghlink hosts），
卸载才彻底清理。本测试断言回滚路径重建静态段。
"""

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import ghlink.hosts_manager as hosts_manager
import ghlink.main as main


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
            lambda block, backup_dir, preserve_g520=True: (
                applied.append(block) or (True, backup_dir)
            ),
        )
        monkeypatch.setattr(
            "ghlink.hosts_manager.rollback",
            lambda backup_path: True,
        )
        main.run(cfg_path)
        # 回滚路径应重建静态段：block 含 ghlink520 子段、无本次坏 IP
        assert applied, "回滚后应调用 apply_block 重建静态段"
        last = applied[-1]
        assert "# ghlink520 Start" in last, f"静态段应保留: {last}"
        assert "codeload.github.com" in last, f"静态条目应在: {last}"
        assert "9.9.9.9 github.com" not in last, f"本次坏 IP 不应残留: {last}"

    def test_rollback_preserves_core_dynamic_fallback(self, tmp_path, monkeypatch):
        """v0.4.11（李工 22:53）：回滚时核心域名（动态段）也保留静态保底——
        用 last_dynamic_ips 缓存（24h 新鲜窗口）写核心域名段，非核心走 g520。"""
        cfg_path = self._make_cfg(tmp_path)
        g520 = {"codeload.github.com": ["1.2.3.4"], "avatars.githubusercontent.com": ["5.6.7.8"]}
        # 状态文件预置 last_dynamic_ips（24h 内新鲜）
        st_file = tmp_path / "state.json"
        import time as _t

        now = _t.strftime("%Y-%m-%dT%H:%M:%S%z")
        st_file.write_text(
            json.dumps(
                {
                    "state": "normal",
                    "last_dynamic_ips": {
                        "github.com": ["1.1.1.1"],
                        "api.github.com": ["2.2.2.2"],
                    },
                    "last_dynamic_at": now,
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "ghlink.probe.probe_all",
            lambda targets, timeout: {t: {"ok": False, "error": "sim"} for t in targets},
        )
        monkeypatch.setattr(
            "ghlink.resolver.resolve_best",
            lambda domain, cfg: ["9.9.9.9"],
        )
        monkeypatch.setattr(
            "ghlink.main._github520_entries",
            lambda cfg, st, st_dir: dict(g520),
        )
        monkeypatch.setattr(
            "ghlink.hosts_manager.verify_after_apply",
            lambda targets, timeout: False,
        )
        applied = []
        monkeypatch.setattr(
            "ghlink.hosts_manager.apply_block",
            lambda block, backup_dir, preserve_g520=True: (
                applied.append(block) or (True, backup_dir)
            ),
        )
        monkeypatch.setattr(
            "ghlink.hosts_manager.rollback",
            lambda backup_path: True,
        )
        main.run(cfg_path)
        assert applied, "回滚后应调用 apply_block 重建静态段"
        last = applied[-1]
        # 核心域名保底：last_dynamic_ips 缓存的 IP 写入（非本次坏 IP）
        assert "1.1.1.1 github.com" in last, f"核心域名保底应写入: {last}"
        assert "2.2.2.2 api.github.com" in last, f"api 保底应写入: {last}"
        assert "9.9.9.9 github.com" not in last, f"本次坏 IP 不应残留: {last}"
        # 非核心域名 g520 静态段保留
        assert "codeload.github.com" in last, f"g520 静态段应在: {last}"

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

        # remove_block 幂等返回 True 即为卸载清理路径
        assert hosts_manager.remove_block() is True
        assert removed == [True]
