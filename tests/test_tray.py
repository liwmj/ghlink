"""托盘模块测试（v0.2.x）。

- 未安装 pystray 依赖时：main() 返回 2（提示装依赖）
- Linux 平台：main() 返回 0（纯 CLI 提示，不阻塞）
- 状态文本/图标颜色映射正确
"""

import json

from ghlink import tray


class _FakeIcon:
    """pystray.Icon 替身：run/stop 直接返回，不真起事件循环（CI 无 GUI）。"""

    def __init__(self, *a, **k):
        pass

    def run(self):
        pass

    def stop(self):
        pass


def _mock_tray_ready(monkeypatch, platform: str, autostart: bool = True) -> None:
    """mock 托盘启动前置：pystray 依赖 + 状态函数 + 值守已启用。

    CI 无 pystray 依赖时 tray.pystray 为 None，需整体替换（不能 setattr(None, ...)）。
    """
    monkeypatch.setattr(tray, "HAS_TRAY", True)
    monkeypatch.setattr(tray.sys, "platform", platform)
    monkeypatch.setattr(tray.service, "_tray_single_instance", lambda: False)
    monkeypatch.setattr(tray, "pystray", type("FakePystray", (), {"Icon": _FakeIcon})())
    monkeypatch.setattr(tray, "_make_icon", lambda *a, **k: None)
    monkeypatch.setattr(tray, "_status_text", lambda: "")
    monkeypatch.setattr(tray, "_build_menu", lambda: None)
    monkeypatch.setattr(tray, "_poll", lambda icon, **k: None)
    monkeypatch.setattr(tray.service, "_is_enabled", lambda: True)  # 跳过自动 enable
    monkeypatch.setattr(tray.service, "_is_autostart", lambda: autostart)


def test_tray_guard_no_dependency(monkeypatch):
    """无 pystray 依赖 → 返回 2（提示安装）。"""
    monkeypatch.setattr(tray, "HAS_TRAY", False)
    monkeypatch.setattr(tray.sys, "platform", "win32")
    assert tray.main() == 2


def test_tray_linux_pure_cli(monkeypatch):
    """Linux 纯 CLI → 返回 0（不提供托盘）。"""
    monkeypatch.setattr(tray.sys, "platform", "linux")
    assert tray.main() == 0


def test_tray_single_instance_guard(monkeypatch):
    """单实例锁（李工 09:49 反馈：自启动后多一个托盘）：已有托盘进程 → 返回 0 不重复拉起。"""
    monkeypatch.setattr(tray, "HAS_TRAY", True)
    monkeypatch.setattr(tray.sys, "platform", "darwin")
    monkeypatch.setattr(tray.service, "_tray_single_instance", lambda: True)
    assert tray.main() == 0


def test_tray_single_instance_guard_windows(monkeypatch):
    """Windows 单实例锁（命名互斥体）同样生效。"""
    monkeypatch.setattr(tray, "HAS_TRAY", True)
    monkeypatch.setattr(tray.sys, "platform", "win32")
    monkeypatch.setattr(tray.service, "_tray_single_instance", lambda: True)
    # v0.2.17 ①：enable 提权前置（Windows 分支 detach 前同步）——mock 值守已启用跳过
    monkeypatch.setattr(tray.service, "_is_enabled", lambda: True)
    monkeypatch.setattr(tray, "_ensure_enabled_sync", lambda: True)
    assert tray.main() == 0


def test_tray_no_instance_starts(monkeypatch):
    """无已有实例 → 正常继续启动，不被自己挡住。

    李工 14:40 Windows 闪退根因：onefile 双进程同名，tasklist 排除自身仍误判
    引导进程为已有实例 → 托盘启动即退出。改用命名互斥体后此场景不再误伤。
    v0.4.24：darwin 已改走原生渲染（tray_macos），本用例改回 win32 pystray 路径。
    """
    _mock_tray_ready(monkeypatch, "win32")
    monkeypatch.setattr(tray, "_ensure_enabled_sync", lambda: True)
    # 无实例时不提前退出：main() 应走到 icon.run()（FakeIcon 直接返回）后返回 0
    assert tray.main() == 0


def test_tray_darwin_pystray_render(monkeypatch):
    """v0.4.25（顾笙 11:39 A/B 实锤）：0.4.24 tray_macos.py 渲染 bug（图标屏幕外），
    回退 pystray 公共路径；darwin 启动时自动注册 LaunchAgent（刀①），不阻塞启动。
    """
    _mock_tray_ready(monkeypatch, "darwin")
    assert tray.main() == 0
    monkeypatch.setattr(tray, "_build_menu", lambda: None)
    monkeypatch.setattr(tray, "_poll", lambda icon, **k: None)
    monkeypatch.setattr(tray.service, "_is_enabled", lambda: True)
    assert tray.main() == 0


def test_tray_darwin_autoregister_launchagent(monkeypatch):
    """v0.4.25（李工 11:34 反馈：退出后双击 APP 起不来）：darwin 启动时若未注册
    LaunchAgent 则自动注册（刀①，幂等），保证「双击 APP 启动过 → 下次登录自启」。"""
    calls = []
    monkeypatch.setattr(tray.sys, "platform", "darwin")
    monkeypatch.setattr(tray, "HAS_TRAY", True)
    monkeypatch.setattr(tray.service, "_tray_single_instance", lambda: False)
    monkeypatch.setattr(tray.service, "_is_autostart", lambda: False)
    monkeypatch.setattr(tray.service, "_enable_autostart", lambda: calls.append(True) or True)

    class _FakeIcon:
        def __init__(self, *a, **k):
            pass

        def run(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(tray, "pystray", type("FakePystray", (), {"Icon": _FakeIcon})())
    monkeypatch.setattr(tray, "_make_icon", lambda *a, **k: None)
    monkeypatch.setattr(tray, "_status_text", lambda: "")
    monkeypatch.setattr(tray, "_build_menu", lambda: None)
    monkeypatch.setattr(tray, "_poll", lambda icon, **k: None)
    monkeypatch.setattr(tray.service, "_is_enabled", lambda: True)
    assert tray.main() == 0
    assert calls, "darwin 未注册 LaunchAgent 时应自动注册"


def test_status_text(tmp_path, monkeypatch):
    """状态文本拼接：状态 + 值守（v0.2.16：统一走 _watch_status_text 新判据）。"""
    st_file = tmp_path / "state.json"
    st_file.write_text(json.dumps({"state": "degraded"}), encoding="utf-8")
    monkeypatch.setattr(tray, "_state_path", lambda: str(st_file))
    monkeypatch.setattr(
        tray.service, "_watch_status_text", lambda: "已启用（值守注册 + 心跳正常）｜托盘: 运行中"
    )
    text = tray._status_text()
    assert "降级" in text
    assert (
        "已启用（值守注册 + 心跳正常）" in text
    )  # 新判据（平台注册+心跳），不再是旧的“值守未启用”


def test_status_text_unregistered(tmp_path, monkeypatch):
    """未注册值守 → 托盘文本显示未启用（新判据透传）。"""
    st_file = tmp_path / "state.json"
    st_file.write_text(json.dumps({"state": "normal"}), encoding="utf-8")
    monkeypatch.setattr(tray, "_state_path", lambda: str(st_file))
    monkeypatch.setattr(
        tray.service,
        "_watch_status_text",
        lambda: "未启用（运行 ghlink enable 开启值守）｜托盘: 运行中",
    )
    text = tray._status_text()
    assert "正常" in text
    assert "未启用（运行 ghlink enable 开启值守）" in text


def test_hide_dock_icon_non_darwin(monkeypatch):
    """非 macOS：_hide_dock_icon 直接返回不抛错。"""
    monkeypatch.setattr(tray.sys, "platform", "win32")
    tray._hide_dock_icon()  # 不应抛异常


def test_hide_dock_icon_darwin_fallback(monkeypatch):
    """macOS 无 objc 库（异常环境）：_hide_dock_icon 静默降级不阻塞托盘。

    拂晓 Linux 复验（2026-08-21 19:31）：字符串路径 monkeypatch
    "ctypes.util.find_library" 在 darwin 平台假象下触发 ctypes.util 重新
    import → 走 ctypes.macholib（仅 macOS 有）→ Linux ImportError。
    改为直接对象引用：先取 ctypes.util 模块（真实平台下已 import 完成），
    再 monkeypatch 其属性，不再触发重导入。
    """
    import ctypes.util as _ctu

    monkeypatch.setattr(tray.sys, "platform", "darwin")
    monkeypatch.setattr(_ctu, "find_library", lambda name: None)
    tray._hide_dock_icon()  # 不应抛异常


def test_detach_non_terminal(monkeypatch):
    """非终端启动（自启动/双击）：不 detach，直接前台跑。"""

    class _FakeStdin:
        def isatty(self):
            return False

    monkeypatch.setattr(tray.sys, "stdin", _FakeStdin())
    monkeypatch.setattr(tray.sys, "platform", "darwin")
    monkeypatch.setattr(tray.sys, "frozen", False, raising=False)
    assert tray._detach_if_terminal() is False


def test_detach_terminal_posix(monkeypatch):
    """POSIX 终端启动：detach 拉起后台进程，返回 True（本进程退出）。"""

    class _FakeStdin:
        def isatty(self):
            return True

    popen_calls = []

    class _FakePopen:
        def __init__(self, *a, **k):
            popen_calls.append((a, k))

    monkeypatch.setattr(tray.sys, "stdin", _FakeStdin())
    monkeypatch.setattr(tray.sys, "platform", "darwin")
    monkeypatch.setattr(tray.sys, "frozen", False, raising=False)
    monkeypatch.setattr("subprocess.Popen", _FakePopen)
    assert tray._detach_if_terminal() is True
    assert popen_calls, "应拉起后台托盘进程"
    args, kwargs = popen_calls[0]
    assert args[0][-1] == "tray"  # 命令尾部是 tray 子命令
    assert kwargs.get("start_new_session") is True  # POSIX 脱离会话
    assert kwargs.get("stdin") is not None  # stdio 重定向


def test_detach_terminal_windows(monkeypatch):
    """Windows 终端启动：detach 用 DETACHED_PROCESS 拉起，返回 True。"""

    class _FakeStdin:
        def isatty(self):
            return True

    popen_calls = []

    class _FakePopen:
        def __init__(self, *a, **k):
            popen_calls.append((a, k))

    monkeypatch.setattr(tray.sys, "stdin", _FakeStdin())
    monkeypatch.setattr(tray.sys, "platform", "win32")
    monkeypatch.setattr(tray.sys, "frozen", False, raising=False)
    monkeypatch.setattr("subprocess.DETACHED_PROCESS", 0x00000008, raising=False)
    monkeypatch.setattr("subprocess.Popen", _FakePopen)
    assert tray._detach_if_terminal() is True
    assert popen_calls
    args, kwargs = popen_calls[0]
    assert kwargs.get("creationflags", 0) != 0  # DETACHED_PROCESS 标志


def test_color_map():
    """状态 → 颜色映射齐全（四色定稿：红/黄/绿/蓝）。"""
    for s in ("normal", "idle", "verifying", "switching", "degraded"):
        assert tray._COLOR[s].startswith("#")
