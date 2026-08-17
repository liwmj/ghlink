"""service 值守判据测试（2026-08-17 双确认口径：载体存活 + 心跳新鲜）。

覆盖：_is_registered / _tray_alive / _heartbeat_fresh / _is_enabled / _watch_status_text。
"""

import json
import os
import time

import pytest

from ghlink import main, service


class TestIsRegistered:
    """旧口径：平台任务注册检测。"""

    def test_macos_plist_exists(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "darwin")
        monkeypatch.setattr(
            service.os.path,
            "exists",
            lambda p: p == "/Library/LaunchDaemons/com.ghlink.plist",
        )
        assert service._is_registered() is True

    def test_macos_plist_missing(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "darwin")
        monkeypatch.setattr(service.os.path, "exists", lambda p: False)
        assert service._is_registered() is False

    def test_linux_systemd_timer(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "linux")
        monkeypatch.setattr(
            service.os.path,
            "exists",
            lambda p: p == "/etc/systemd/system/ghlink.timer",
        )
        assert service._is_registered() is True

    def test_linux_crontab(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "linux")
        monkeypatch.setattr(service.os.path, "exists", lambda p: False)
        monkeypatch.setattr(
            service.platform_adapter,
            "_run_cmd_output",
            lambda args: "* * * * * /usr/bin/python3 -m ghlink.main /etc/ghlink/config.json",
        )
        assert service._is_registered() is True

    def test_windows_schtasks(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "win32")
        monkeypatch.setattr(service.platform_adapter, "_run_cmd", lambda args: True)
        assert service._is_registered() is True


class TestTrayAlive:
    """值守载体（托盘进程）存活检测（含 exclude_pid 排除自身，赛博 09:56 问题 B）。"""

    def _mock_pid_file_gone(self, monkeypatch, tmp_path):
        """⑤ v0.2.17：PID 文件兜底——测试隔离到不存在的路径，强制走 pgrep 分支。"""
        monkeypatch.setattr(service, "_tray_pid_file", lambda: str(tmp_path / "nope-tray.pid"))

    def test_macos_tray_running(self, monkeypatch, tmp_path):
        monkeypatch.setattr(service.sys, "platform", "darwin")
        self._mock_pid_file_gone(monkeypatch, tmp_path)
        monkeypatch.setattr(
            service.platform_adapter,
            "_run_cmd_output",
            lambda args: "12345\n",
        )
        assert service._tray_alive() is True

    def test_macos_tray_not_running(self, monkeypatch, tmp_path):
        monkeypatch.setattr(service.sys, "platform", "darwin")
        self._mock_pid_file_gone(monkeypatch, tmp_path)
        monkeypatch.setattr(
            service.platform_adapter,
            "_run_cmd_output",
            lambda args: "",
        )
        assert service._tray_alive() is False

    def test_macos_exclude_self_only(self, monkeypatch, tmp_path):
        """pgrep 只匹配到自身 PID → 排除后无实例（首次启动不被自己挡住）。"""
        monkeypatch.setattr(service.sys, "platform", "darwin")
        self._mock_pid_file_gone(monkeypatch, tmp_path)
        monkeypatch.setattr(
            service.platform_adapter,
            "_run_cmd_output",
            lambda args: "99999\n",  # 99999 = 自身 PID
        )
        assert service._tray_alive(exclude_pid=99999) is False

    def test_macos_exclude_self_with_other(self, monkeypatch, tmp_path):
        """除自身外还有旧实例 → 排除后仍有实例（单实例锁应拦截）。"""
        monkeypatch.setattr(service.sys, "platform", "darwin")
        self._mock_pid_file_gone(monkeypatch, tmp_path)
        monkeypatch.setattr(
            service.platform_adapter,
            "_run_cmd_output",
            lambda args: "99999\n12345\n",  # 99999=自身, 12345=旧实例
        )
        assert service._tray_alive(exclude_pid=99999) is True

    def test_macos_pid_file_primary(self, monkeypatch, tmp_path):
        """⑤ v0.2.17：PID 文件存在且存活 → 直接判 True（pgrep 兜底不触发）。"""
        monkeypatch.setattr(service.sys, "platform", "darwin")
        pid_file = tmp_path / "tray.pid"
        pid_file.write_text(str(99991), encoding="utf-8")
        monkeypatch.setattr(service, "_tray_pid_file", lambda: str(pid_file))
        monkeypatch.setattr(
            service.platform_adapter,
            "_run_cmd_output",
            lambda args: "",  # pgrep 返回空也不影响：PID 文件优先
        )
        monkeypatch.setattr(service, "_pid_alive", lambda pid: pid == 99991)
        assert service._tray_alive() is True

    def test_windows_tray_running(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "win32")
        monkeypatch.setattr(
            service.platform_adapter,
            "_run_cmd_output",
            lambda args: '"ghlink-tray.exe","1234","Console","1","8,000 K"',
        )
        assert service._tray_alive() is True

    def test_windows_tray_not_running(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "win32")
        monkeypatch.setattr(
            service.platform_adapter,
            "_run_cmd_output",
            lambda args: "信息: 没有运行的任务匹配指定标准。",
        )
        assert service._tray_alive() is False

    def test_windows_exclude_self_only(self, monkeypatch):
        """tasklist CSV 只含自身 PID → 排除后无实例。"""
        monkeypatch.setattr(service.sys, "platform", "win32")
        monkeypatch.setattr(
            service.platform_adapter,
            "_run_cmd_output",
            lambda args: '"ghlink-tray.exe","99999","Console","1","8,000 K"',
        )
        assert service._tray_alive(exclude_pid=99999) is False

    def test_linux_no_tray(self, monkeypatch):
        monkeypatch.setattr(service.sys, "platform", "linux")
        assert service._tray_alive() is False


class TestEntryCmd:
    """值守执行入口（2026-08-17 Bug A 修复：优先 wrapper 带 PYTHONPATH）。"""

    def test_prefer_wrapper(self, monkeypatch):
        """PATH 里有 wrapper → 用 wrapper（带 PYTHONPATH），不裸调 python -m。"""
        monkeypatch.setattr(service.sys, "platform", "darwin")
        monkeypatch.setattr(service.os.path, "exists", lambda p: True)
        monkeypatch.setattr(service.shutil, "which", lambda name: "/usr/local/bin/ghlink")
        cmd = service._python_cmd()
        assert "/usr/local/bin/ghlink" in cmd
        assert "-m ghlink.main" not in cmd

    def test_prefer_wrapper_linux(self, monkeypatch):
        """Linux deb 安装：/usr/bin/ghlink wrapper 优先。"""
        monkeypatch.setattr(service.sys, "platform", "linux")
        monkeypatch.setattr(service.os.path, "exists", lambda p: True)
        monkeypatch.setattr(service.shutil, "which", lambda name: "/usr/bin/ghlink")
        cmd = service._python_cmd()
        assert "/usr/bin/ghlink" in cmd

    def test_fallback_dev_mode(self, monkeypatch):
        """无 wrapper（源码/venv 开发）→ 回退 python -m（PYTHONPATH 天然可用）。"""
        monkeypatch.setattr(service.sys, "platform", "linux")
        monkeypatch.setattr(service.os.path, "exists", lambda p: False)
        monkeypatch.setattr(service.shutil, "which", lambda name: None)
        cmd = service._python_cmd()
        assert "-m ghlink.main" in cmd


class TestEnsureConfig:
    """enable 前配置落位（2026-08-17 Bug B 修复）。"""

    def test_existing_config_untouched(self, tmp_path, monkeypatch):
        """config 已存在 → 不复制不覆盖。"""
        cfg = tmp_path / "config.json"
        cfg.write_text('{"keep": true}', encoding="utf-8")
        monkeypatch.setattr(service, "_config_path", lambda: str(cfg))
        service._ensure_config()
        assert "keep" in cfg.read_text(encoding="utf-8")

    def test_missing_config_copies_example(self, tmp_path, monkeypatch):
        """config 缺失 → 从候选模板复制（真实文件链路，不 mock exists）。"""
        example = tmp_path / "config.example.json"
        example.write_text('{"state_file": "/var/lib/ghlink/x.json"}', encoding="utf-8")
        cfg = tmp_path / "etc" / "ghlink" / "config.json"
        monkeypatch.setattr(service, "_config_path", lambda: str(cfg))
        # 候选列表第一个 = cwd/config.example.json，用 tmp_path 做 cwd（真实存在）
        monkeypatch.chdir(tmp_path)
        service._ensure_config()
        assert cfg.exists()
        assert "var/lib/ghlink" in cfg.read_text(encoding="utf-8")


class TestResolveRel:
    """相对路径 → 相对 config.json 目录解析（2026-08-17 赛博补强，Bug B 根治）。"""

    def test_absolute_unchanged(self):
        assert (
            main._resolve_rel("/var/lib/ghlink/x.json", "/etc/ghlink/config.json")
            == "/var/lib/ghlink/x.json"
        )

    def test_relative_resolved_to_config_dir(self):
        # 期望值动态构造（Windows 路径分隔符不同，CI 跨平台）
        cfg_abs = os.path.abspath("/etc/ghlink/config.json")
        expected = os.path.join(os.path.dirname(cfg_abs), "ghlink_status.json")
        assert main._resolve_rel("ghlink_status.json", "/etc/ghlink/config.json") == expected

    def test_empty_unchanged(self):
        assert main._resolve_rel("", "/etc/ghlink/config.json") == ""

    def test_lock_and_backup_relative(self):
        cfg_abs = os.path.abspath("/etc/ghlink/config.json")
        base = os.path.dirname(cfg_abs)
        assert main._resolve_rel("ghlink.lock", "/etc/ghlink/config.json") == os.path.join(
            base, "ghlink.lock"
        )
        assert main._resolve_rel("backup", "/etc/ghlink/config.json") == os.path.join(
            base, "backup"
        )


class TestHeartbeatFresh:
    """状态文件心跳新鲜度（探测 1 小时粒度，v0.2.18 宽限 90 分钟）。"""

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

    def test_fresh_within_90min(self, tmp_path, monkeypatch):
        """v0.2.18：1h 探测粒度下，1 小时前的时间戳仍算新鲜。"""
        old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3600))
        st_path = self._write_state(tmp_path, old)
        monkeypatch.setattr(service, "_config_path", lambda: "")
        monkeypatch.setattr(service.os.path, "exists", lambda p: p == st_path)
        monkeypatch.setattr(service.state, "load", lambda p: {"timestamp": old})
        assert service._heartbeat_fresh() is True

    def test_stale(self, tmp_path, monkeypatch):
        # 超过 90 分钟宽限（5400s）才判 stale（v0.2.18：max_age 180→5400）
        old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 7200))
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
        cfg.write_text(json.dumps({"state_file": str(tmp_path / "state.json")}), encoding="utf-8")
        # 真实状态文件，timestamp 新鲜
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        (tmp_path / "state.json").write_text(json.dumps({"timestamp": ts}), encoding="utf-8")
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
    """值守判据（2026-08-17 李工新口径）：平台任务注册 + 心跳新鲜，全平台统一。"""

    def test_registered_heartbeat_fresh(self, monkeypatch):
        monkeypatch.setattr(service, "_is_registered", lambda: True)
        monkeypatch.setattr(service, "_heartbeat_fresh", lambda: True)
        assert service._is_enabled() is True

    def test_registered_heartbeat_stale_zombie(self, monkeypatch):
        """注册但心跳停 = 僵尸，不算启用（防假阳性）。"""
        monkeypatch.setattr(service, "_is_registered", lambda: True)
        monkeypatch.setattr(service, "_heartbeat_fresh", lambda: False)
        assert service._is_enabled() is False

    def test_not_registered(self, monkeypatch):
        """未注册 = 未启用（不启动托盘也能值守，但没 enable 就不值守）。"""
        monkeypatch.setattr(service, "_is_registered", lambda: False)
        monkeypatch.setattr(service, "_heartbeat_fresh", lambda: True)
        assert service._is_enabled() is False

    def test_all_platforms_unified(self, monkeypatch):
        """全平台统一判据：不区分托盘进程（托盘=展示层）。"""
        for plat in ("darwin", "win32", "linux"):
            monkeypatch.setattr(service.sys, "platform", plat)
            monkeypatch.setattr(service, "_is_registered", lambda: True)
            monkeypatch.setattr(service, "_heartbeat_fresh", lambda: True)
            assert service._is_enabled() is True


class TestWatchStatusText:
    """status 值守状态细分文本（三态 + 托盘辅助行，2026-08-17 李工新口径）。"""

    def test_enabled(self, monkeypatch):
        monkeypatch.setattr(service, "_is_registered", lambda: True)
        monkeypatch.setattr(service, "_heartbeat_fresh", lambda: True)
        monkeypatch.setattr(service, "_tray_alive", lambda: True)
        text = service._watch_status_text()
        assert "已启用" in text
        assert "托盘: 运行中" in text

    def test_zombie(self, monkeypatch):
        """注册但心跳停 = 僵尸（异常）。"""
        monkeypatch.setattr(service, "_is_registered", lambda: True)
        monkeypatch.setattr(service, "_heartbeat_fresh", lambda: False)
        monkeypatch.setattr(service, "_tray_alive", lambda: True)
        text = service._watch_status_text()
        assert "异常" in text
        assert "僵尸" in text

    def test_disabled(self, monkeypatch):
        """未注册 = 未启用；托盘在但未注册 = 提示启动值守。"""
        monkeypatch.setattr(service, "_is_registered", lambda: False)
        monkeypatch.setattr(service, "_heartbeat_fresh", lambda: False)
        monkeypatch.setattr(service, "_tray_alive", lambda: True)
        text = service._watch_status_text()
        assert "未启用" in text
        assert "托盘: 运行中" in text
