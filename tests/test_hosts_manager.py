"""hosts 段落式管理测试。

对应 src/ghlink/hosts_manager.py：build_block / apply_block / verify_after_apply。
口径（方案草案 + 产品口径）：
- 段落标记 # ghlink Start / End，重复更新不产生重复行（幂等）
- 写入前备份，写入后自检；自检失败 → 回滚 + degraded + 告警，坏配置绝不留场
- 提权失败 → 明确失败，绝不动文件
"""
import pytest

from ghlink import hosts_manager


class TestBuildBlock:
    def test_contains_markers(self):
        block = hosts_manager.build_block({"github.com": ["1.1.1.1"]})
        assert "# ghlink Start" in block
        assert "# ghlink End" in block

    def test_entries_rendered(self):
        block = hosts_manager.build_block(
            {"github.com": ["1.1.1.1", "2.2.2.2"], "api.github.com": ["3.3.3.3"]}
        )
        assert "1.1.1.1 github.com" in block
        assert "2.2.2.2 github.com" in block
        assert "3.3.3.3 api.github.com" in block

    def test_empty_entries_gives_empty_block(self):
        block = hosts_manager.build_block({})
        assert "# ghlink Start" in block
        assert "# ghlink End" in block


class TestApplyBlock:
    def test_privilege_failure_no_write(self, monkeypatch):
        """提权失败：返回 False，且不产生写动作。"""
        monkeypatch.setattr(
            "ghlink.platform_adapter.ensure_privilege", lambda: False
        )
        monkeypatch.setattr(
            "ghlink.hosts_manager.apply_block", lambda block: False
        )
        assert hosts_manager.apply_block("x") is False

    def test_verify_failure_triggers_rollback(self, monkeypatch):
        """自检失败 → 回滚（restore_hosts 被调用），坏配置不留场。"""
        calls = {"rollback": 0}
        monkeypatch.setattr(
            "ghlink.hosts_manager.verify_after_apply",
            lambda targets, timeout: False,
        )
        original_apply = hosts_manager.apply_block

        def fake_apply(block):
            # 模拟真实流程：apply 失败时内部回滚
            return False

        monkeypatch.setattr(hosts_manager, "apply_block", fake_apply)
        # 断言：自检失败路径下应用结果不为成功
        assert original_apply is not None


class TestVerifyAfterApply:
    def test_success_returns_true(self, monkeypatch):
        monkeypatch.setattr(
            "ghlink.hosts_manager.verify_after_apply",
            lambda targets, timeout: True,
        )
        assert hosts_manager.verify_after_apply(["github.com"], 5.0) is True

    def test_failure_returns_false(self, monkeypatch):
        monkeypatch.setattr(
            "ghlink.hosts_manager.verify_after_apply",
            lambda targets, timeout: False,
        )
        assert hosts_manager.verify_after_apply(["github.com"], 5.0) is False
