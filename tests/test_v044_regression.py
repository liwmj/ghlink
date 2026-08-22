"""v0.4.4 回归测试：verify 分级宽容降级（李工 03:27 终裁 B 方案）。

顾笙无缓存场景专项发现：verify 三层校验（TCP+TLS+HTTP HEAD）在 TLS 干扰
环境下误杀 → 兜底写入后回滚清空主条目。B 方案：先三层全检，失败目标
TCP-only 复检——TCP 通判通过（与预检同口径）、TCP 也不通才回滚。
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ghlink import hosts_manager, probe


class TestVerifyTieredFallback:
    """v0.4.4：verify 分级宽容——TLS 干扰不误杀、真坏 IP 仍回滚。"""

    def test_tls_interference_ok_after_tcp_fallback(self, monkeypatch):
        """TLS 干扰场景：三层全检失败，TCP-only 复检通过 → verify 成功。"""
        # probe_all 返回 TLS 层失败；probe_tcp_only_many 返回 TCP 通
        monkeypatch.setattr(
            probe,
            "probe_all",
            lambda targets, timeout: {
                "github.com": {"ok": False, "error": "TLS handshake timeout"},
            },
        )
        monkeypatch.setattr(
            probe,
            "probe_tcp_only_many",
            lambda targets, timeout: {"github.com": {"ok": True}},
        )
        assert hosts_manager.verify_after_apply(["github.com"], 5.0) is True

    def test_dead_ip_still_rolls_back(self, monkeypatch):
        """真坏 IP：三层全检失败 + TCP-only 复检也失败 → verify 失败（回滚）。"""
        monkeypatch.setattr(
            probe,
            "probe_all",
            lambda targets, timeout: {
                "github.com": {"ok": False, "error": "TLS handshake timeout"},
            },
        )
        monkeypatch.setattr(
            probe,
            "probe_tcp_only_many",
            lambda targets, timeout: {
                "github.com": {"ok": False, "error": "Connection refused"},
            },
        )
        assert hosts_manager.verify_after_apply(["github.com"], 5.0) is False

    def test_all_pass_no_fallback_needed(self, monkeypatch):
        """全部三层通过：不触发降级，直接成功。"""
        monkeypatch.setattr(
            probe,
            "probe_all",
            lambda targets, timeout: {"github.com": {"ok": True}},
        )
        assert hosts_manager.verify_after_apply(["github.com"], 5.0) is True

    def test_empty_failed_no_fallback(self, monkeypatch):
        """部分失败：仅失败目标走 TCP 复检。"""
        calls = []

        def fake_probe_all(targets, timeout):
            return {
                "github.com": {"ok": True},
                "api.github.com": {"ok": False, "error": "HTTP 502"},
            }

        def fake_tcp_many(targets, timeout):
            calls.append(list(targets))
            return {"api.github.com": {"ok": True}}

        monkeypatch.setattr(probe, "probe_all", fake_probe_all)
        monkeypatch.setattr(probe, "probe_tcp_only_many", fake_tcp_many)
        assert hosts_manager.verify_after_apply(["github.com", "api.github.com"], 5.0) is True
        assert calls == [["api.github.com"]]
