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
    monkeypatch.setattr(tray.service, "_tray_alive", lambda exclude_pid=0: True)
    assert tray.main() == 0


def test_tray_single_instance_guard_windows(monkeypatch):
    """Windows 单实例锁同样生效。"""
    monkeypatch.setattr(tray, "HAS_TRAY", True)
    monkeypatch.setattr(tray.sys, "platform", "win32")
    monkeypatch.setattr(tray.service, "_tray_alive", lambda exclude_pid=0: True)
    assert tray.main() == 0


def test_tray_no_instance_starts(monkeypatch):
    """无已有实例（pgrep 只匹配到自身，排除后无实例）→ 正常继续启动，不被自己挡住。

    赛博 09:56 问题 B：单实例锁必须排除自身 PID，否则首次 ghlink tray 会被自己拦截。
    """
    monkeypatch.setattr(tray, "HAS_TRAY", True)
    monkeypatch.setattr(tray.sys, "platform", "darwin")
    monkeypatch.setattr(tray.service, "_tray_alive", lambda exclude_pid=0: False)

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
    """状态文本拼接：状态 + 值守。"""
    st_file = tmp_path / "state.json"
    st_file.write_text(json.dumps({"state": "degraded"}), encoding="utf-8")
    monkeypatch.setattr(tray, "_state_path", lambda: str(st_file))
    monkeypatch.setattr(tray.service, "_is_enabled", lambda: False)
    text = tray._status_text()
    assert "降级" in text
    assert "值守未启用" in text


def test_color_map():
    """状态 → 颜色映射齐全（四色定稿：红/黄/绿/蓝）。"""
    for s in ("normal", "idle", "verifying", "switching", "degraded"):
        assert tray._COLOR[s].startswith("#")
