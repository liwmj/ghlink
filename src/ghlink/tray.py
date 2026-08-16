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
    "normal": "#34C759",  # 绿=值守启用且正常（李工 12:58 定规：绿=值守启用）
    "idle": "#007AFF",  # 蓝=正常但值守未启用（区别于绿）
    "verifying": "#FFD60A",  # 黄=切换/验证中
    "switching": "#FFD60A",
    "degraded": "#FF3B30",  # 红=异常
}
_TEXT = {
    "normal": "正常",
    "verifying": "验证中",
    "switching": "切换中",
    "degraded": "降级",
    "disabled": "未启用",
}


def _icon_path() -> str:
    """项目图标路径：安装包（PyInstaller datas）优先，仓库 assets/ 兜底。"""
    candidates = []
    if getattr(sys, "frozen", False):  # PyInstaller 打包环境
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
        candidates.append(os.path.join(base, "assets", "ghlink-icon.png"))
    # 仓库/开发环境
    candidates.append(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "assets",
            "ghlink-icon.png",
        )
    )
    for p in candidates:
        if os.path.exists(p):
            return p
    return ""


def _state_path() -> str:
    """状态文件路径：优先从 config.json 的 state_file 字段读取，否则默认 ghlink_status.json。"""
    cfg_path = service._config_path()
    st_path = "ghlink_status.json"
    if os.path.exists(cfg_path):
        try:
            import json as _json

            with open(cfg_path, encoding="utf-8") as f:
                cfg = _json.load(f)
            st_path = cfg.get("state_file", "ghlink_status.json")
        except Exception:
            pass
    return st_path


def _load_state() -> Dict[str, Any]:
    p = _state_path()
    return state.load(p) if os.path.exists(p) else {}


def _status_text() -> str:
    st = _load_state()
    s = st.get("state", "normal")
    watching = service._is_enabled()
    txt = _TEXT.get(s, s)
    watch = "值守已启用" if watching else "值守未启用"
    return f"状态: {txt} ｜ {watch}"


def _make_icon(color: str, size: int = 64):
    """生成托盘图标：项目图标 + 右下角状态色徽章；无图标资源时回退纯色圆角。"""
    img = None
    path = _icon_path()
    if path:
        try:
            icon = Image.open(path).convert("RGBA")
            # contain 居中：保持比例不变形（横版图标贴入方形画布）
            icon.thumbnail((size, size), Image.LANCZOS)
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            img.paste(
                icon,
                ((size - icon.width) // 2, (size - icon.height) // 2),
                icon,
            )
        except Exception:
            img = None
    if img is None:  # 回退：纯色圆角 + 中心 G
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((4, 4, size - 4, size - 4), fill=color)
        d.text((size * 0.35, size * 0.28), "G", fill="white")
    # 右下角状态徽章（带白边，深浅底色都清晰）
    d = ImageDraw.Draw(img)
    r = max(size // 8, 6)
    cx, cy = size - r - 2, size - r - 2
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill="white")
    d.ellipse((cx - r + 2, cy - r + 2, cx + r - 2, cy + r - 2), fill=color)
    return img


def _refresh(icon: Any) -> None:
    """定时刷新：状态文件 → 图标颜色 + 菜单文字。

    状态灯四色（李工 13:03 定规）：红=异常 > 黄=切换中 > 绿=值守启用 > 蓝=正常未启用。
    """
    st = _load_state()
    s = st.get("state", "normal")
    watching = service._is_enabled()
    if s in ("degraded",):
        color = _COLOR["degraded"]  # 异常红（最高优先）
    elif s in ("verifying", "switching"):
        color = _COLOR["verifying"]  # 切换/验证中黄
    elif watching:
        color = _COLOR["normal"]  # 值守启用且正常绿
    else:
        color = _COLOR["idle"]  # 正常但值守未启用蓝
    try:
        icon.icon = _make_icon(color)
        icon.title = _status_text()
        # 重建菜单（值守开关状态同步）
        icon.menu = _build_menu()
        icon.update_menu()
    except Exception:
        pass


def _cli_command(subcmd: str) -> list:
    """构造 CLI 子命令（enable/disable）完整命令（P1-1：必须走 CLI 入口）。

    - frozen（PyInstaller）：ghlink.exe enable/disable（ghlink.exe 是 console CLI 入口）
    - dev：python -m ghlink.main enable/disable
    不能用托盘入口（ghlink-tray.exe / tray.main）——托盘不解析 argv，拉起只会新开托盘实例。
    """
    if getattr(sys, "frozen", False):
        exe = os.path.join(os.path.dirname(sys.executable), "ghlink.exe")
        if os.path.exists(exe):
            return [exe, subcmd]
        # 兜底：frozen 但找不到 ghlink.exe（异常环境），退回当前解释器 + -m
    return [sys.executable, "-m", "ghlink.main", subcmd]


def _run_privileged(subcmd: str) -> bool:
    """以管理员身份运行 CLI 子命令（独立进程，托盘自身不退出）。

    避免直接调 service.enable/disable——ensure_privilege() 提权成功会 sys.exit(0)
    退出当前进程，托盘会被误杀。Windows 走 ShellExecuteW runas 提权；
    已具管理员权限时直接 subprocess 跑。
    """
    cmd = _cli_command(subcmd)
    try:
        if sys.platform == "win32" and not service._is_admin():
            import ctypes

            params = " ".join(f'"{a}"' for a in cmd)
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", cmd[0], params, None, 1
            )
            return ret > 32
        import subprocess

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        return r.returncode == 0
    except Exception:
        return False


def _quit_tray(icon: Any, item: Any) -> None:
    """退出托盘 = 停值守（方案 A 语义：托盘=值守总开关）。

    确认提示 → disable（独立提权进程，UAC 一次）→ 托盘退出。
    """
    try:
        import ctypes as _ct

        if sys.platform == "win32":
            ret = _ct.windll.user32.MessageBoxW(
                None,
                "退出托盘将同时停用 ghlink 值守。确定退出？",
                "ghlink",
                0x4 | 0x20,  # MB_YESNO | MB_ICONQUESTION
            )
            if ret != 6:  # IDYES
                return
        if service._is_enabled():
            _run_privileged("disable")
    except Exception:
        pass
    icon.stop()


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


def _hosts_ip() -> str:
    """hosts 里当前生效的 github.com IP（未切换时兜底显示，赛博 13:35 设计口径）。"""
    try:
        import platform as _platform

        hosts_path = "/etc/hosts"
        if _platform.system() == "Windows":
            import os as _os

            root = _os.environ.get("SYSTEMROOT", r"C:\Windows")
            hosts_path = _os.path.join(root, "System32", "drivers", "etc", "hosts")
        with open(hosts_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split()
                if len(parts) >= 2 and "github.com" in parts[1:]:
                    ip = parts[0]
                    if ip not in ("127.0.0.1", "::1", "0.0.0.0"):
                        return ip
    except Exception:
        pass
    return ""


def _current_ip() -> str:
    """当前生效 IP：state.current_ip 优先，history 兜底，再兜底 hosts 实际解析（赛博设计口径）。"""
    st = _load_state()
    ip = st.get("current_ip")
    if not ip and st.get("history"):
        last = st["history"][-1]
        ip = last.get("ip") if isinstance(last, dict) else last
    if not ip:
        ip = _hosts_ip()
    return ip or "—"


def _copy_ip(icon: Any, item: Any) -> None:
    """点击 IP 菜单项：复制当前 IP 到剪贴板（李工 13:09 需求）。"""
    import subprocess

    ip = _current_ip()
    if ip == "—":
        _notify(icon, "暂无可用 IP")
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["clip"], input=ip.encode("utf-8"), check=False)
        else:
            subprocess.run(["pbcopy"], input=ip.encode("utf-8"), check=False)
        _notify(icon, f"已复制: {ip}")
    except Exception as exc:  # pragma: no cover
        _notify(icon, f"复制失败: {exc}")


def _build_menu():
    watching = service._is_enabled()
    return pystray.Menu(
        pystray.MenuItem(lambda _: _status_text(), None, enabled=False),
        pystray.MenuItem(
            lambda _: f"当前 IP: {_current_ip()}（点击复制）",
            _copy_ip,
            default=True,  # 双击托盘图标默认动作 = 复制 IP
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "停用值守" if watching else "启用值守",
            _toggle_watch,
            checked=lambda _: watching,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出托盘（同时停值守）", _quit_tray),
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
    # 方案 A（李工 13:44 定调）：托盘=值守总开关——启动时若值守未启用则自动开启（幂等）
    try:
        if not service._is_enabled():
            ok = _run_privileged("enable")
            if ok:
                _notify(icon, "值守已自动启用")
    except Exception:
        pass
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
