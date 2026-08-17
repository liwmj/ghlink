"""托盘模块测试（v0.2.x）。

- 未安装 pystray 依赖时：main() 返回 2（提示装依赖）
- Linux 平台：main() 返回 0（纯 CLI 提示，不阻塞）
- 状态文本/图标颜色映射正确
"""

import json

from ghlink import tray


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
    assert tray.main() == 0


def test_tray_no_instance_starts(monkeypatch):
    """无已有实例 → 正常继续启动，不被自己挡住。

    李工 14:40 Windows 闪退根因：onefile 双进程同名，tasklist 排除自身仍误判
    引导进程为已有实例 → 托盘启动即退出。改用命名互斥体后此场景不再误伤。
    """
    monkeypatch.setattr(tray, "HAS_TRAY", True)
    monkeypatch.setattr(tray.sys, "platform", "darwin")
    monkeypatch.setattr(tray.service, "_tray_single_instance", lambda: False)

    class _FakeIcon:
        def __init__(self, *a, **k):
            pass

        def run(self):
            pass

        def stop(self):
            pass

    # CI 无 pystray 依赖时 tray.pystray 为 None，需整体替换（不能 setattr(None, ...)）
    monkeypatch.setattr(tray, "pystray", type("FakePystray", (), {"Icon": _FakeIcon})())
    monkeypatch.setattr(tray, "_make_icon", lambda *a, **k: None)
    monkeypatch.setattr(tray, "_status_text", lambda: "")
    monkeypatch.setattr(tray, "_build_menu", lambda: None)
    monkeypatch.setattr(tray, "_poll", lambda icon, **k: None)
    monkeypatch.setattr(tray.service, "_is_enabled", lambda: True)  # 跳过自动 enable
    # 无实例时不提前退出：main() 应走到 icon.run()（FakeIcon 直接返回）后返回 0
    assert tray.main() == 0


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
    """macOS 无 objc 库（异常环境）：_hide_dock_icon 静默降级不阻塞托盘。"""
    monkeypatch.setattr(tray.sys, "platform", "darwin")
    monkeypatch.setattr("ctypes.util.find_library", lambda name: None)
    tray._hide_dock_icon()  # 不应抛异常


def test_color_map():
    """状态 → 颜色映射齐全（四色定稿：红/黄/绿/蓝）。"""
    for s in ("normal", "idle", "verifying", "switching", "degraded"):
        assert tray._COLOR[s].startswith("#")
