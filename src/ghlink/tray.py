"""系统托盘（v0.2.x）：pystray 跨平台（Windows + macOS）。

产品口径（v0.2.x 终裁）：
- 托盘 = UI 载体，普通权限运行；值守 = enable/disable 通道（schtasks/LaunchDaemon/timer），独立运行
- 托盘开关复用 service.enable()/disable() 同一逻辑，不另搞 Run key/LaunchAgent 双轨
- 退出托盘 ≠ 停止值守（值守由平台定时任务独立跑，互不影响）
- 依赖只进安装包（pystray + Pillow），核心保持零依赖；未安装时 tray 命令提示并退出 2
- Linux 纯 CLI，不提供托盘（pystray 在 Linux 需 appindicator，成本高收益低，不做）
"""

import os
import sys
import threading
import time
from typing import Any, Dict

from . import service, state

# pystray 依赖可选：核心零依赖，安装包内注入（PyInstaller datas / brew deps）
try:
    import pystray
    from PIL import Image, ImageDraw

    HAS_TRAY = True
except Exception:  # pragma: no cover - 未装依赖时
    pystray = None
    Image = None
    HAS_TRAY = False

# 状态 → 图标颜色（绿=正常 / 黄=切换验证中 / 红=降级 / 灰=值守停用）
_COLOR = {
    "normal": "#34C759",
    "verifying": "#FFD60A",
    "switching": "#FFD60A",
    "degraded": "#FF3B30",
    "disabled": "#8E8E93",
}
_TEXT = {
    "normal": "正常",
    "verifying": "验证中",
    "switching": "切换中",
    "degraded": "降级",
    "disabled": "未启用",
}


def _config_path() -> str:
    """托盘读取状态用的配置路径（与 service 一致）。"""
    return (
        service._config_path() if os.path.exists(service._config_path()) else "ghlink_status.json"
    )


def _load_state() -> Dict[str, Any]:
    p = _config_path()
    return state.load(p) if os.path.exists(p) else {}


def _status_text() -> str:
    st = _load_state()
    s = st.get("state", "normal")
    watching = service._is_enabled()
    txt = _TEXT.get(s, s)
    watch = "值守已启用" if watching else "值守未启用"
    return f"状态: {txt} ｜ {watch}"


def _make_icon(color: str, size: int = 64):
    """生成纯色圆角图标（无外部资源文件）。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, size - 4, size - 4), fill=color)
    # 中心字母 G
    d.text((size * 0.35, size * 0.28), "G", fill="white")
    return img


def _refresh(icon: Any) -> None:
    """定时刷新：状态文件 → 图标颜色 + 菜单文字。"""
    st = _load_state()
    s = st.get("state", "normal")
    watching = service._is_enabled()
    color = _COLOR.get(s, "#8E8E93")
    if not watching:
        color = _COLOR["disabled"]
    try:
        icon.icon = _make_icon(color)
        icon.title = _status_text()
        # 重建菜单（值守开关状态同步）
        icon.menu = _build_menu()
        icon.update_menu()
    except Exception:
        pass


def _toggle_watch(icon: Any, item: Any) -> None:
    """值守开关：复用 enable/disable 通道（Windows schtasks / macOS LaunchDaemon）。"""
    try:
        watching = service._is_enabled()
        if watching:
            code = service.disable()
            msg = "值守已停用" if code == 0 else f"停用失败(code={code})"
        else:
            code = service.enable()
            msg = "值守已启用（1 分钟粒度）" if code == 0 else f"启用失败(code={code})"
        if code != 0:
            _notify(icon, msg)
    except Exception as exc:  # pragma: no cover
        _notify(icon, f"操作失败: {exc}")
    finally:
        _refresh(icon)


def _notify(icon: Any, text: str) -> None:
    try:
        icon.notify(text, "ghlink")
    except Exception:
        pass


def _build_menu():
    watching = service._is_enabled()
    return pystray.Menu(
        pystray.MenuItem(lambda _: _status_text(), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "停用值守" if watching else "启用值守",
            _toggle_watch,
            checked=lambda _: watching,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出托盘", lambda icon, item: icon.stop()),
    )


def main() -> int:
    """托盘入口：ghlink tray。仅 Windows/macOS；Linux 提示纯 CLI。"""
    if sys.platform == "linux":
        print(
            "[ghlink] Linux 为纯 CLI 设计，不提供托盘。值守请用 ghlink enable/disable/status",
            file=sys.stderr,
        )
        return 0
    if not HAS_TRAY:  # pragma: no cover
        print(
            "[ghlink] 缺少托盘依赖（pystray/Pillow）。"
            "请使用安装包版本，或 pip install pystray pillow",
            file=sys.stderr,
        )
        return 2

    st = _load_state()
    s = st.get("state", "normal")
    color = _COLOR.get(s, "#8E8E93")
    icon = pystray.Icon(
        "ghlink",
        _make_icon(color),
        _status_text(),
        menu=_build_menu(),
    )
    # 状态轮询线程（5s），UI 主循环不阻塞
    t = threading.Thread(target=_poll, args=(icon,), daemon=True)
    t.start()
    icon.run()
    return 0


def _poll(icon: Any, interval: float = 5.0) -> None:
    """后台轮询状态文件刷新托盘。"""
    while True:
        time.sleep(interval)
        try:
            _refresh(icon)
        except Exception:
            pass
