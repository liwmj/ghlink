"""service 值守判据测试（2026-08-17 双确认口径：载体存活 + 心跳新鲜）。

覆盖：_is_registered / _tray_alive / _heartbeat_fresh / _is_enabled / _watch_status_text。
"""

import json
import time

import pytest

from ghlink import service


class TestIsRegistered:
    """旧口径：平台任务注册检测。"""

    def test_macos_plist_exists(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "darwin")
        monkeypatch.setattr(service.os.path, "exists", lambda p: p == "/Library/LaunchDaemons/com.ghlink.plist")
        assert service._is_registered() is True

    def test_macos_plist_missing(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "darwin")
        monkeypatch.setattr(service.os.path, "exists", lambda p: False)
        assert service._is_registered() is False

    def test_linux_systemd_timer(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "linux")
        monkeypatch.setattr(
            service.os.path, "exists",
            lambda p: p == "/etc/systemd/system/ghlink.timer",
        )
        assert service._is_registered() is True

    def test_linux_crontab(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "linux")
        monkeypatch.setattr(service.os.path, "exists", lambda p: False)
        monkeypatch.setattr(
            service.platform_adapter, "_run_cmd_output", lambda args: "* * * * * /usr/bin/python3 -m ghlink.main /etc/ghlink/config.json"
        )
        assert service._is_registered() is True

    def test_windows_schtasks(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "win32")
        monkeypatch.setattr(service.platform_adapter, "_run_cmd", lambda args: True)
        assert service._is_registered() is True


class TestTrayAlive:
    """值守载体（托盘进程）存活检测。"""

    def test_macos_tray_running(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "darwin")
        monkeypatch.setattr(
            service.platform_adapter, "_run_cmd_output",
            lambda args: "12345\n",
        )
        assert service._tray_alive() is True

    def test_macos_tray_not_running(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "darwin")
        monkeypatch.setattr(
            service.platform_adapter, "_run_cmd_output",
            lambda args: "",
        )
        assert service._tray_alive() is False

    def test_windows_tray_running(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "win32")
        monkeypatch.setattr(
            service.platform_adapter, "_run_cmd_output",
            lambda args: "ghlink-tray.exe 1234 Console 1 8,000 K",
        )
        assert service._tray_alive() is True

    def test_windows_tray_not_running(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "win32")
        monkeypatch.setattr(
            service.platform_adapter, "_run_cmd_output",
            lambda args: "信息: 没有运行的任务匹配指定标准。",
        )
        assert service._tray_alive() is False

    def test_linux_no_tray(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "linux")
        assert service._tray_alive() is False


class TestHeartbeatFresh:
    """状态文件心跳新鲜度（探测 1 分钟粒度，3 分钟宽限）。"""

    def _write_state(self, tmp_path, ts):
        p = tmp_path / "ghlink_status.json"
        p.write_text(json.dumps({"timestamp": ts}), encoding="utf-8")
        return str(p)

    def test_fresh(self, tmp_path, monkeypatch):
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        st_path = self._write_state(tmp_path, ts)
        monkeypatch.setattr(service, "_config_path", lambda: "")
        monkeypatch.setattr(service.os.path, "exists", lambda p: p == st_path)
        monkeypatch.setattr(service.state, "load", lambda p: {"timestamp": ts})
        assert service._heartbeat_fresh() is True

    def test_stale(self, tmp_path, monkeypatch):
        old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 600))
        st_path = self._write_state(tmp_path, old)
        monkeypatch.setattr(service, "_config_path", lambda: "")
        monkeypatch.setattr(service.os.path, "exists", lambda p: p == st_path)
        monkeypatch.setattr(service.state, "load", lambda p: {"timestamp": old})
        assert service._heartbeat_fresh() is False

    def test_missing_timestamp(self, tmp_path, monkeypatch):
        st_path = self._write_state(tmp_path, "")
        monkeypatch.setattr(service, "_config_path", lambda: "")
        monkeypatch.setattr(service.os.path, "exists", lambda p: p == st_path)
        monkeypatch.setattr(service.state, "load", lambda p: {"timestamp": ""})
        assert service._heartbeat_fresh() is False

    def test_integration_real_files(self, tmp_path):
        """集成：真实 config.json（state_file 字段）+ 真实状态文件 → 读状态文件心跳。

        赛博 08:51 复核 bug：旧实现直接读 _config_path()（配置文件无 timestamp），
        config.json 存在时恒判不新鲜 → 假阴性。此测试不 monkeypatch state.load，
        走真实文件链路验证修复。
        """
        # config.json 带 state_file 指向状态文件
        cfg = tmp_path / "config.json"
        cfg.write_text(
            json.dumps({"state_file": str(tmp_path / "state.json")}), encoding="utf-8"
        )
        # 真实状态文件，timestamp 新鲜
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        (tmp_path / "state.json").write_text(
            json.dumps({"timestamp": ts}), encoding="utf-8"
        )
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(service, "_config_path", lambda: str(cfg))
        try:
            assert service._heartbeat_fresh() is True
        finally:
            monkeypatch.undo()

    def test_integration_config_without_state_file(self, tmp_path, monkeypatch):
        """集成：config.json 存在但无 state_file 字段 → 回退默认 ghlink_status.json（cwd）。"""
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"probe": {"targets": ["github.com"]}}), encoding="utf-8")
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        (tmp_path / "ghlink_status.json").write_text(
            json.dumps({"timestamp": ts}), encoding="utf-8"
        )
        monkeypatch.setattr(service, "_config_path", lambda: str(cfg))
        monkeypatch.chdir(tmp_path)
        assert service._heartbeat_fresh() is True


class TestIsEnabled:
    """双确认：载体存活 + 心跳新鲜。"""

    def test_macos_tray_alive_heartbeat_fresh(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "darwin")
        monkeypatch.setattr(service, "_tray_alive", lambda: True)
        monkeypatch.setattr(service, "_heartbeat_fresh", lambda: True)
        assert service._is_enabled() is True

    def test_macos_zombie_tray_heartbeat_stale(self, monkeypatch):
        """进程在但心跳停 = 僵尸，不算启用（防假阳性）。"""
        monkeypatch.setattr(service.sys, "platform", "darwin")
        monkeypatch.setattr(service, "_tray_alive", lambda: True)
        monkeypatch.setattr(service, "_heartbeat_fresh", lambda: False)
        assert service._is_enabled() is False

    def test_macos_tray_dead(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "darwin")
        monkeypatch.setattr(service, "_tray_alive", lambda: False)
        monkeypatch.setattr(service, "_heartbeat_fresh", lambda: True)
        assert service._is_enabled() is False

    def test_windows_tray_alive_heartbeat_fresh(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "win32")
        monkeypatch.setattr(service, "_tray_alive", lambda: True)
        monkeypatch.setattr(service, "_heartbeat_fresh", lambda: True)
        assert service._is_enabled() is True

    def test_linux_registered_heartbeat_fresh(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "linux")
        monkeypatch.setattr(service, "_is_registered", lambda: True)
        monkeypatch.setattr(service, "_heartbeat_fresh", lambda: True)
        assert service._is_enabled() is True

    def test_linux_registered_heartbeat_stale(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "linux")
        monkeypatch.setattr(service, "_is_registered", lambda: True)
        monkeypatch.setattr(service, "_heartbeat_fresh", lambda: False)
        assert service._is_enabled() is False


class TestWatchStatusText:
    """status 值守状态细分文本（三态）。"""

    def test_enabled(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "darwin")
        monkeypatch.setattr(service, "_tray_alive", lambda: True)
        monkeypatch.setattr(service, "_heartbeat_fresh", lambda: True)
        assert "已启用" in service._watch_status_text()

    def test_zombie(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "darwin")
        monkeypatch.setattr(service, "_tray_alive", lambda: True)
        monkeypatch.setattr(service, "_heartbeat_fresh", lambda: False)
        assert "异常" in service._watch_status_text()
        assert "僵尸" in service._watch_status_text()

    def test_disabled(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "darwin")
        monkeypatch.setattr(service, "_tray_alive", lambda: False)
        monkeypatch.setattr(service, "_heartbeat_fresh", lambda: False)
        assert "未启用" in service._watch_status_text()
