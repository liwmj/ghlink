"""托盘模块测试（v0.2.x）。

- 未安装 pystray 依赖时：main() 返回 2（提示装依赖）
- Linux 平台：main() 返回 0（纯 CLI 提示，不阻塞）
- 状态文本/图标颜色映射正确
"""
import json

import pytest

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


def test_status_text(tmp_path, monkeypatch):
    """状态文本拼接：状态 + 值守。"""
    st_file = tmp_path / "state.json"
    st_file.write_text(json.dumps({"state": "degraded"}), encoding="utf-8")
    monkeypatch.setattr(tray, "_config_path", lambda: str(st_file))
    monkeypatch.setattr(tray.service, "_is_enabled", lambda: False)
    text = tray._status_text()
    assert "降级" in text
    assert "值守未启用" in text


def test_color_map():
    """状态 → 颜色映射齐全。"""
    for s in ("normal", "verifying", "switching", "degraded", "disabled"):
        assert tray._COLOR[s].startswith("#")
