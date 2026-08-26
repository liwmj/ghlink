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

# pystray 依赖可选：核心零依赖，安装包内注入（PyInstaller datas / brew deps）
from typing import Any as _Any

from . import __version__, platform_adapter, service, state

HAS_TRAY = False
try:
    import pystray as _pystray
    from PIL import Image as _PILImage
    from PIL import ImageDraw as _ImageDraw

    pystray: _Any = _pystray
    Image: _Any = _PILImage
    ImageDraw: _Any = _ImageDraw
    HAS_TRAY = True
except Exception:  # pragma: no cover - 未装依赖时
    pystray = None
    Image = None
    ImageDraw = None
    HAS_TRAY = False

# 状态 → 图标颜色（v0.4.8 李工 20:28 拍板）
# 灰=值守未启用（最高优先）｜值守启用时：红=degraded、黄=切换验证中、蓝=正常未自启动、绿=正常+自启动
_COLOR = {
    "normal": "#34C759",  # 绿=值守启用且正常+开机自启动
    "idle": "#007AFF",  # 蓝=值守启用且正常，但未开机自启动
    "disabled": "#8E8E93",  # 灰=值守未启用（停用态，最高优先）
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
    """项目图标路径：安装包（PyInstaller datas）优先，仓库/brew 兜底。"""
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
    # brew 安装环境（v0.2.17 补装：libexec/assets/，李工规格必须 LOGO）
    candidates.append(
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "assets",
            "ghlink-icon.png",
        )
    )
    # v0.4.3（李工 8 bug 点④ macOS 真机验收发现）：venv/pip 安装（package-data 进包）——
    # 图标随包在 site-packages/ghlink/assets/，_icon_path 必须命中否则回退纯色非 LOGO
    candidates.append(
        os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
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


def _hide_dock_icon() -> None:
    """macOS：隐藏 Dock 图标（LSUIElement 等效，托盘常驻不占 Dock）。

    2026-08-17 v0.2.16（李工 18:43 反馈）：托盘用 python 进程跑，
    Dock 一直显示 Python 图标。用 Objective-C runtime 设置
    NSApplicationActivationPolicyAccessory（=1）隐藏 Dock，零依赖
    （不引 pyobjc，ctypes 直接调 objc runtime）。
    """
    if sys.platform != "darwin":  # pragma: no cover - 仅 macOS
        return
    try:  # pragma: no cover - 依赖 macOS 运行时
        import ctypes
        import ctypes.util

        objc_lib = ctypes.util.find_library("objc")
        if not objc_lib:
            return
        objc = ctypes.cdll.LoadLibrary(objc_lib)
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.restype = ctypes.c_void_p
        objc.objc_msgSend.restype = ctypes.c_void_p
        objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        cls = objc.objc_getClass(b"NSApplication")
        sel = objc.sel_registerName(b"sharedApplication")
        app = objc.objc_msgSend(cls, sel)
        # setActivationPolicy: 0=Regular(显示Dock) 1=Accessory(隐藏Dock) 2=Prohibited
        objc.objc_msgSend(app, objc.sel_registerName(b"setActivationPolicy:"), ctypes.c_int(1))
    except Exception:
        pass  # 隐藏失败不阻塞托盘（仅 Dock 图标可见性）


def _status_text() -> str:
    st = _load_state()
    s = st.get("state", "normal")
    txt = _TEXT.get(s, s)
    # v0.4.10（李工 22:53 需求）：第一行状态文本加版本号，一眼确认版本
    return f"ghlink v{__version__} ｜ 状态: {txt} ｜ {service._watch_status_text()}"


def _make_icon(color: str, size: int = 64):
    """生成托盘图标：LOGO 主体 + 右下角状态色角标（v0.2.17 李工规格）。

    李工 21:47 定：托盘图标必须用 LOGO（ghlink-icon.png）本体，
    状态只用角标色标注（绿/黄/红/灰），不再回退纯色圆角+G。
    图标资源缺失时也不回退纯色——直接返回 None 由调用方兜底
    （HAS_TRAY 已保证依赖存在，LOGO 缺失属打包问题应暴露）。
    """
    img = None
    path = _icon_path()
    if path:
        try:
            icon = Image.open(path).convert("RGBA")
            # contain 居中：保持比例不变形（横版图标贴入方形画布）
            icon.thumbnail((size, size), Image.Resampling.LANCZOS)
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            img.paste(
                icon,
                ((size - icon.width) // 2, (size - icon.height) // 2),
                icon,
            )
        except Exception:
            img = None
    if img is None:  # LOGO 缺失：暴露问题而非纯色回退（李工规格：必须 LOGO）
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse((4, 4, size - 4, size - 4), fill="#8E8E93")
        d.text((size * 0.35, size * 0.28), "G", fill="white")
    # 右下角状态色角标（带白边，深浅底色都清晰）
    d = ImageDraw.Draw(img)
    r = max(size // 8, 6)
    cx, cy = size - r - 2, size - r - 2
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill="white")
    d.ellipse((cx - r + 2, cy - r + 2, cx + r - 2, cy + r - 2), fill=color)
    return img


def _state_color() -> str:
    """状态灯判定（v0.4.8 李工 20:28 拍板）：灰=值守未启用（最高优先）；
    值守启用时：红=degraded > 黄=切换中 > 蓝=正常未自启动 > 绿=正常+自启动。

    v0.2.19（李工 8 条④）：初始图标与 _refresh 共用此判定，不再各自为政——
    修复 Windows 托盘启动瞬间「绿角标+菜单未运行」不匹配（原 main() 只看 state
    映射，没判断值守是否启用）。
    v0.4.8（李工 20:28）：值守未启用时优先显示灰（停用态），不显示状态残留；
    蓝/绿区分自启动——蓝=值守正常但未开机自启动，绿=值守正常+开机自启动。
    """
    st = _load_state()
    s = st.get("state", "normal")
    watching = service._is_enabled()
    if not watching:
        return _COLOR["disabled"]  # 灰=值守未启用（最高优先，不看状态残留）
    if s in ("degraded",):
        return _COLOR["degraded"]  # 异常红
    if s in ("verifying", "switching"):
        return _COLOR["verifying"]  # 切换/验证中黄
    if service._is_autostart():
        return _COLOR["normal"]  # 值守正常+开机自启动绿
    return _COLOR["idle"]  # 值守正常但未开机自启动蓝


def _refresh(icon: Any) -> None:
    """定时刷新：状态文件 → 图标颜色 + 菜单文字。

    状态灯四色（李工 13:03 定规）：红=异常 > 黄=切换中 > 绿=值守启用 > 蓝=正常未启用。

    v0.4.17（李工 Windows 反馈「菜单热点丢失」）：原实现每 5 秒全量重建菜单
    （icon.menu = _build_menu() + update_menu()），鼠标悬停时菜单被重建 →
    热点消失，看似崩溃。改为：仅在状态（颜色/值守/自启/文案）变化时才重建。
    """
    try:
        color = _state_color()
        watching = service._is_enabled()
        autostart = service._is_autostart()
        status = _status_text()
        key = (color, watching, autostart, status)
        if getattr(icon, "_ghlink_menu_key", None) == key:
            return  # 状态无变化：不重建菜单，保住热点
        icon.icon = _make_icon(color)
        icon.title = status
        icon.menu = _build_menu()
        icon.update_menu()
        icon._ghlink_menu_key = key
    except Exception:
        pass


def _cli_command(subcmd: str) -> list:
    """构造 CLI 子命令（enable/disable）完整命令（P1-1：必须走 CLI 入口）。

    - frozen（PyInstaller）：ghlink.exe enable/disable（ghlink.exe 是 console CLI 入口）
    - dev：优先用已安装的 ghlink 可执行文件（venv/bin/ghlink 或 PATH 命中），
      与 sudoers NOPASSWD 窄放行路径对齐；找不到才退回 python -m ghlink.main
      （v0.4.6：顾笙 macOS 实测发现 sudoers 放行 ghlink 可执行文件，但托盘走
      python -m 不匹配 → sudo 要密码 → 提权静默失败 → 菜单点了没反应）
    不能用托盘入口（ghlink-tray.exe / tray.main）——托盘不解析 argv，拉起只会新开托盘实例。
    """
    if getattr(sys, "frozen", False):
        exe = os.path.join(os.path.dirname(sys.executable), "ghlink.exe")
        if os.path.exists(exe):
            return [exe, subcmd]
        # 兜底：frozen 但找不到 ghlink.exe（异常环境），退回当前解释器 + -m
    # v0.4.6（顾笙 13:57 根因）：非 frozen 环境优先找 ghlink 可执行文件，
    # 对齐 sudoers 放行路径（macOS venv: .venv/bin/ghlink；Windows: Scripts/ghlink.exe）
    # v0.4.23（赛博根因 2026-08-26）：GUI 应用（Finder/LaunchServices 双击）PATH 只有
    # /usr/bin:/bin:/usr/sbin:/sbin，shutil.which 找不到 /usr/local/bin/ghlink → 退回
    # python -m 不匹配 sudoers NOPASSWD → 提权失败（与 08-25 卸载弹密码同病根）。
    # v0.4.25：统一复用 service._find_wrapper()（含 .app 绝对路径，SonarCloud 消重）。
    # ⚠️ v0.4.27 回归修复（李工 13:40 实测：值守关不掉/权限不足）：_find_wrapper()
    # 优先 .app 内 wrapper（/Applications/ghlink.app/...），但 sudoers NOPASSWD 放行
    # 的是 /usr/local/bin/ghlink——提权走 .app 路径不匹配白名单 → sudo 要密码 →
    # 权限不足。提权命令必须与 sudoers 放行路径对齐（/usr/local/bin 优先），
    # .app wrapper 只用于 LaunchAgent 启动（_enable_autostart），两职责分离。
    import shutil as _shutil

    # 提权路径（sudoers NOPASSWD 放行）优先：/usr/local/bin → /opt/homebrew/bin
    for cand in (
        "/usr/local/bin/ghlink",  # Intel macOS 安装位（sudoers 放行路径）
        "/opt/homebrew/bin/ghlink",  # Apple Silicon macOS 安装位
        os.path.join(os.path.dirname(sys.executable), "ghlink"),  # venv/bin/ghlink
        os.path.join(os.path.dirname(sys.executable), "ghlink.exe"),  # Windows Scripts
    ):
        if os.path.exists(cand):
            return [cand, subcmd]
    which = _shutil.which("ghlink")
    if which:
        return [which, subcmd]
    return [sys.executable, "-m", "ghlink.main", subcmd]


def _run_privileged(subcmd: str) -> bool:
    """以管理员身份运行 CLI 子命令（独立进程，托盘自身不退出）。

    避免直接调 service.enable/disable——ensure_privilege() 提权成功会 sys.exit(0)
    退出当前进程，托盘会被误杀。Windows 走 ShellExecuteW runas 提权；
    macOS/Linux 非 root 接 sudo -n（v0.4.5：复用李工已配的 sudoers NOPASSWD
    窄放行）；已具权限时直接 subprocess 跑。
    """
    cmd = _cli_command(subcmd)
    try:
        if sys.platform == "win32" and not platform_adapter._is_admin():
            import ctypes

            # v0.4.17（李工 Windows 反馈「无法开启值守」）：ShellExecuteW 的
            # lpParameters 只能传参数，不能包含 exe 本身——原实现把整个 cmd
            # （含 ghlink.exe 路径）拼进 params，提权进程 argv 变成
            # [ghlink.exe, <exe路径>, "enable"] → main.py 把 exe 路径当 config
            # 解析 → enable 根本没执行。只传 cmd[1:]（子命令部分）。
            params = " ".join(f'"{a}"' for a in cmd[1:])
            ret = ctypes.windll.shell32.ShellExecuteW(None, "runas", cmd[0], params, None, 1)
            return ret > 32
        import os as _os

        # v0.4.5（李工拍板决策点 2）：macOS/Linux 托盘是用户态进程，
        # enable/disable 需要 root——非 root 时前置 sudo -n（已配 NOPASSWD，
        # 不弹密码；未配置时静默失败由调用方提示）。
        if sys.platform != "win32" and hasattr(_os, "geteuid") and _os.geteuid() != 0:
            cmd = ["sudo", "-n"] + cmd
        import subprocess

        kwargs: dict = {}
        if sys.platform == "win32":  # v0.2.16：提权已具备时不弹命令窗
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, **kwargs)
        return r.returncode == 0
    except Exception:
        return False


def _quit_tray(icon: Any, item: Any) -> None:
    """退出托盘（v0.4.5 李工拍板）：通用=仅退出 UI，Windows 特例=同时停值守。

    - macOS/Linux：退出托盘不再停值守（值守状态由菜单「启用值守/关闭值守」显式控制）
    - Windows（李工特例）：保持原方案 A 语义——退出托盘 = 停值守，确认提示后 disable
    """
    try:
        if sys.platform == "win32":
            import ctypes as _ct

            ret = _ct.windll.user32.MessageBoxW(
                None,
                "退出托盘将同时停用 ghlink 值守。确定退出？",
                "ghlink",
                0x4 | 0x20,  # MB_YESNO | MB_ICONQUESTION
            )
            if ret != 6:  # IDYES
                return
            # 2026-08-17 口径对齐：退出=停值守，查平台任务注册（残留清理语义）
            # 不能用新 _is_enabled()（托盘进程+心跳双确认）——僵尸场景会漏清
            if service._is_registered():
                _run_privileged("disable")
    except Exception:
        pass
    icon.stop()
    # v0.5.3（顾笙 19:41 定位：退出托盘后 PID 锁残留死 PID，干扰下次双击重定向）：
    # 退出时清理 PID 文件（~/.ghlink/ghlink-tray.pid），避免残留锁误导单实例判定
    try:
        _pid_file = service._tray_pid_file()
        if os.path.exists(_pid_file):
            os.unlink(_pid_file)
    except Exception:
        pass


def _toggle_autostart(icon: Any, item: Any) -> None:
    """开机自启动开关（李工 13:56 拍板：开自启动=开值守，自启动=值守总开关）。

    开启：注册登录自启 + 立即 enable 值守（不等下次登录）→ 角标转绿；
    此时「关闭值守」菜单项灰掉（锁定开启态），避免自启动与值守状态矛盾。
    关闭：移除登录自启，值守保持当前状态（可由菜单/命令行自由开关）。
    """
    try:
        if service._is_autostart():
            ok = service._disable_autostart()
            msg = "开机自启动已关闭" if ok else "关闭失败"
        else:
            ok = service._enable_autostart()
            if ok:
                # 开自启动 = 同时启值守（李工语义），立即生效不等下次登录
                if service._is_enabled():
                    msg = "开机自启动已开启，值守运行中"
                elif _run_privileged("enable"):
                    for _ in range(10):
                        if service._is_enabled():
                            break
                        time.sleep(0.5)
                    if service._is_enabled():
                        msg = "开机自启动已开启，值守已启用"
                    else:
                        # v0.4.19（李工 21:52 拍板）：值守没真正起来 → 回滚自启动，
                        # 不留「自启动开、值守没开」的半开假状态
                        service._disable_autostart()
                        msg = "开机自启动开启失败：值守无法启用（权限不足？）"
                else:
                    # v0.4.19（李工 21:52 拍板）：enable 提权失败 → 整体回滚
                    service._disable_autostart()
                    msg = "开机自启动开启失败：值守无法启用（权限不足？）"
            else:
                msg = "开启失败"
        _notify(icon, msg)
    except Exception as exc:  # pragma: no cover
        _notify(icon, f"操作失败: {exc}")
    finally:
        _refresh(icon)


def _enable_watch(icon: Any, item: Any) -> None:
    """启用值守（v0.4.5 李工需求：托盘菜单直接操作）。

    提权独立进程执行 enable（Windows 弹 UAC / macOS sudo -n），
    成功后刷新菜单与图标角标（蓝→绿）。
    """
    try:
        if service._is_enabled():
            _notify(icon, "值守已在运行")
            return
        if _run_privileged("enable"):
            # ShellExecuteW runas 异步启动提权进程：给 enable 落盘一点时间
            for _ in range(10):
                if service._is_enabled():
                    break
                time.sleep(0.5)
            if service._is_enabled():
                _notify(icon, "值守已启用")
            else:
                _notify(icon, "启用指令已发出，等待生效")
        else:
            _notify(icon, "启用失败（权限被拒？）")
    except Exception as exc:  # pragma: no cover
        _notify(icon, f"启用失败: {exc}")
    finally:
        _refresh(icon)


def _disable_watch(icon: Any, item: Any) -> None:
    """关闭值守（v0.4.5 李工需求：托盘菜单直接操作）。

    提权独立进程执行 disable（保留 hosts 与配置，李工 2026-08-22 19:31 终裁语义），
    成功后刷新菜单与图标角标（绿→蓝）。
    """
    try:
        if not service._is_enabled():
            _notify(icon, "值守本就未运行")
            return
        if _run_privileged("disable"):
            for _ in range(10):
                if not service._is_enabled():
                    break
                time.sleep(0.5)
            if not service._is_enabled():
                _notify(icon, "值守已停用")
            else:
                _notify(icon, "停用指令已发出，等待生效")
        else:
            _notify(icon, "停用失败（权限被拒？）")
    except Exception as exc:  # pragma: no cover
        _notify(icon, f"停用失败: {exc}")
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
    """当前生效 IP：current_ip → history → hosts → 系统 DNS（v0.2.9 兜底链补全）。"""
    st = _load_state()
    ip = st.get("current_ip")
    if not ip and st.get("history"):
        last = st["history"][-1]
        ip = last.get("ip") if isinstance(last, dict) else last
    if not ip:
        ip = _hosts_ip()
    if not ip:
        try:
            import socket as _socket

            infos = _socket.getaddrinfo("github.com", None, _socket.AF_INET)
            ip = infos[0][4][0]
        except Exception:
            pass
    return str(ip) if ip else "—"


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
    autostart = service._is_autostart()
    # v0.4.6（李工 13:56 拍板）：自启动=值守总开关——
    # 自启动开启时值守锁定为启用态，「关闭值守」置灰（不可点），
    # 「启用值守」勾选显示开启；自启动关闭时菜单自由操作。
    # v0.4.19（李工 21:52 拍板）：locked_on 需值守真实在跑——自启动开但
    # 值守没起来（enable 失败回滚场景）时菜单显示真实状态、可手动重试，不假绿
    locked_on = autostart and watching
    return pystray.Menu(
        pystray.MenuItem(lambda _: _status_text(), None, enabled=False),
        pystray.MenuItem(
            lambda _: f"当前 IP: {_current_ip()}（点击复制）",
            _copy_ip,
            # v0.2.17（李工 21:45 反馈）：去掉 default——左键右键都弹菜单，
            # 只有点击菜单里的 IP 项才复制（default=True 时左键单击直接复制）
        ),
        # v0.4.8（李工 20:21）：删独立值守行——第一行综合状态已含值守信息，不重复
        pystray.Menu.SEPARATOR,
        # v0.4.5（李工需求）：托盘菜单直接操作值守开关。
        # v0.4.6（李工拍板）：自启动开启时「关闭值守」置灰+「启用值守」勾选，
        # 值守锁定开启态；未启用时「关闭值守」灰，操作意图一目了然。
        pystray.MenuItem(
            "启用值守",
            _enable_watch,
            checked=lambda _: watching or locked_on,
            enabled=lambda _: not (watching or locked_on),
        ),
        # v0.4.8（李工 20:21）：补 checked 互斥勾选——值守停止时「关闭值守」打勾
        pystray.MenuItem(
            "关闭值守",
            _disable_watch,
            checked=lambda _: not (watching or locked_on),
            enabled=lambda _: watching and not locked_on,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "开机自启动（随登录启动托盘+值守）",
            _toggle_autostart,
            checked=lambda _: autostart,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出托盘", _quit_tray),
    )


def _detach_if_terminal() -> bool:
    """⑤ 托盘 detach 常驻（v0.2.16，李工/赛博 20:33 批准）。

    从终端手动启动托盘（ghlink tray）时，自动脱离终端会话独立常驻——
    关闭终端窗口不影响托盘运行（否则终端关闭 → SIGHUP → 托盘退出）。
    返回 True 表示已 detach（本进程应退出，托盘已在后台常驻）。
    """
    try:
        if not sys.stdin.isatty():  # 非终端启动（自启动/双击）无需 detach
            return False
    except Exception:
        return False
    try:
        import subprocess as _sp

        # 构造重新拉起自己的命令（与入口一致）
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "tray"]
        else:
            cmd = [sys.executable, "-m", "ghlink.main", "tray"]
        kwargs: dict = {
            "stdin": _sp.DEVNULL,
            "stdout": _sp.DEVNULL,
            "stderr": _sp.DEVNULL,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(_sp, "DETACHED_PROCESS", 0) | getattr(
                _sp, "CREATE_NEW_PROCESS_GROUP", 0
            )
        else:
            kwargs["start_new_session"] = True
        _sp.Popen(cmd, **kwargs)
        return True
    except Exception:
        return False


def _ensure_enabled_sync() -> bool:
    """① 同步确保值守已启用（v0.2.18 修正：失败不阻断托盘启动）。

    李工 23:21 批评：v0.2.17 按 A 语义（提权失败 → return 2 托盘直接退出）
    → Windows UAC 取消/弹窗异常时托盘完全起不来。修正为 B 语义：
    enable 提权仍前置同步执行（detach 前、icon.run 前），但失败时
    **托盘照常启动、状态如实显示未启用**——用户可能只想开托盘看状态。
    """
    try:
        if service._is_enabled():
            return True
        if not _run_privileged("enable"):
            return False
        # ShellExecuteW runas 异步启动提权进程：给 enable 落盘一点时间
        for _ in range(10):
            if service._is_enabled():
                return True
            time.sleep(0.5)
        return service._is_enabled()
    except Exception:
        return False


def main() -> int:
    """托盘入口：ghlink tray。仅 Windows/macOS；Linux 提示纯 CLI。"""
    if sys.platform == "linux":
        print(
            "[ghlink] Linux 为纯 CLI 设计，不提供托盘。值守请用 ghlink enable/disable/status",
            file=sys.stderr,
        )
        return 0

    # ① v0.2.18（李工 23:21 批评修正）：enable 提权前置同步执行，
    # 但失败**不阻断托盘启动**（B 语义）——UAC 取消时托盘照常启动、
    # 状态显示未启用；避免「值守没开 → 托盘也起不来」的体验倒退
    if sys.platform == "win32":
        try:
            _ensure_enabled_sync()
        except Exception:
            pass

    # ⑤ 托盘 detach 常驻（v0.2.16）：从终端启动 → 脱离终端会话后台常驻
    try:
        if _detach_if_terminal():
            print("[ghlink] 托盘已在后台常驻运行（已脱离终端）", file=sys.stderr)
            return 0
    except Exception:
        pass

    # v0.4.24（李工 03:41 实测定案）：macOS 弃用 pystray——0.19.5（2023-09 停更）
    # 在 macOS 26.6.2 上进程存活但 NSStatusItem 菜单栏图标不渲染；改用 vendored
    # PyObjC 原生渲染（tray_macos.py）。Windows 保持 pystray（正常）。
    # v0.4.25（顾笙 11:39 A/B 实锤推翻）：0.4.24 tray_macos.py 有实现 bug——
    # 菜单栏项创建了但图标坐标 (-1,1087) 屏幕外；0.4.23 pystray 反而正常 (940,3)，
    # 纯 PyObjC 最小渲染测试也成功 → 是 tray_macos.py 实现问题，非 pystray/环境。
    # 回退：darwin 也走下方公共 pystray 路径（0.4.23 验证正常），
    # tray_macos.py 保留文件但本轮不启用，修复单独排期。
    # darwin 额外：⑤ v0.4.25（李工 11:34 反馈：退出后双击 APP 起不来）——
    # 托盘自启 = LaunchAgent（用户会话），安装器不注册 → 进程退出后无机制拉起。
    # 启动时若未注册则自动注册（幂等），保证「双击 APP 启动过 → 下次登录自启」。
    if sys.platform == "darwin":
        try:
            # v0.5.2（拂晓 16:50 五刀②）：用户显式取消过自启 → 不再自动注册（意愿持久化标记）
            if not service._is_autostart() and not service._autostart_disabled():
                service._enable_autostart()
        except Exception:
            pass
        # v0.5.x（李工 14:36「装两个文件离谱」收敛）：手动安装 = 拖一个 dmg/app，
        # 系统组件（软链 + sudoers + LaunchDaemon 模板）首启自动引导安装（一次性授权）。
        try:
            service._ensure_macos_system_components()
        except Exception:
            pass
        # v0.5.2 刀① + v0.5.3 双击兜底（李工 19:11/19:35 实测：退出托盘→双击 / 取消自启→双击
        # 都启动不了）：LaunchServices 双击链路图标落屏幕外 (-1,1108)；LaunchAgent 脚本路径
        # 渲染正常 (901,3)。双击启动（非 LaunchAgent 拉起）→ 无条件确保 LaunchAgent 在跑：
        #   ① 标记不拦手动打开：未注册时直接注册（哪怕用户取消过自启，双击=本次要托盘）
        #   ② la_pid in (None, 0) 都走 bootstrap 兜底
        #   ③ bootstrap 失败（exit 5: job 已加载但 not running）→ 先 bootout 清残留定义再 bootstrap
        #   ④ kickstart -k 拉起后验证 LaunchAgent 真的在跑；失败不 return 0 → 直接前台渲染保底
        try:
            import subprocess as _sp
            import time as _time

            la_pid = service._launchagent_pid()
            if la_pid != os.getpid():
                plist = os.path.expanduser("~/Library/LaunchAgents/com.ghlink.tray.plist")
                # ① 无条件注册（双击主动打开：标记只管开机自启，不管手动打开）
                if not os.path.exists(plist):
                    service._write_tray_plist()
                # ①.5 v0.5.3 实测补刀（20:35 顾笙机器侧实测）：用户取消过自启 →
                # launchctl disable 状态残留，bootstrap/kickstart 对 disabled job 拉不起
                # （双击进程直接落前台渲染 = LaunchServices 屏幕外 -1,1108，等于没兜住）。
                # 双击主动打开必须反 disabled（enable 幂等）才能 bootstrap/kickstart。
                _sp.run(
                    ["launchctl", "enable", f"gui/{os.getuid()}/com.ghlink.tray"],
                    check=False,
                    timeout=10,
                )
                # ①.6 v0.5.4（李工 20:58 实测定案：卸载全清→装 0.5.3→首启成功→点退出→
                # 再双击无反应）：写 redirecting 标记（自身 pid）——kickstart 拉起的新实例
                # 做单实例检查时跳过该 pid，避免 A/B 并存期互判自杀（B 被误判已有实例即退）。
                try:
                    _rd = os.path.expanduser("~/.ghlink/redirecting.pid")
                    os.makedirs(os.path.dirname(_rd), exist_ok=True)
                    with open(_rd, "w", encoding="utf-8") as _f:
                        _f.write(str(os.getpid()))
                except Exception:
                    pass
                # ② la_pid in (None, 0) 都走 bootstrap 兜底（0 值别漏过）
                if la_pid in (None, 0):
                    r = _sp.run(
                        ["launchctl", "bootstrap", f"gui/{os.getuid()}", plist],
                        capture_output=True,
                        text=True,
                        timeout=10,
                    )
                    # ③ bootstrap 失败（exit 5: job 已加载但 not running）→ bootout 清残留定义再试
                    if r.returncode != 0:
                        _sp.run(
                            ["launchctl", "bootout", f"gui/{os.getuid()}/com.ghlink.tray"],
                            check=False,
                            timeout=10,
                        )
                        _sp.run(
                            ["launchctl", "bootstrap", f"gui/{os.getuid()}", plist],
                            check=False,
                            timeout=10,
                        )
                _sp.run(
                    [
                        "launchctl",
                        "kickstart",
                        "-k",
                        f"gui/{os.getuid()}/com.ghlink.tray",
                    ],
                    check=False,
                    timeout=10,
                )
                # ④ 验证 LaunchAgent 真的拉起（bootstrap/kickstart 异步，最多等 ~5s）；
                # 失败 → 前台渲染保底（托盘必出）
                _la_ok = False
                for _ in range(10):
                    _time.sleep(0.5)
                    _p = service._launchagent_pid()
                    if _p not in (None, 0, os.getpid()):
                        _la_ok = True
                        break
                # 清 redirecting 标记（无论成败，A 使命结束）
                try:
                    _rd = os.path.expanduser("~/.ghlink/redirecting.pid")
                    if os.path.exists(_rd):
                        os.unlink(_rd)
                except OSError:
                    pass
                if _la_ok:
                    # ④.5 双击拉起成功但用户取消过自启 → 恢复 disable + 删 plist（意愿保留）：
                    # 本次托盘继续跑（KeepAlive 不中断），下次登录不自启（标记只管开机自启）
                    if service._autostart_disabled():
                        _sp.run(
                            ["launchctl", "disable", f"gui/{os.getuid()}/com.ghlink.tray"],
                            check=False,
                            timeout=10,
                        )
                        try:
                            if os.path.exists(plist):
                                os.unlink(plist)
                        except OSError:
                            pass
                    print(
                        "[ghlink] 双击启动 → 已重定向 LaunchAgent（脚本路径渲染）",
                        file=sys.stderr,
                    )
                    return 0
                print(
                    "[ghlink] LaunchAgent 拉起失败，本次前台渲染（保底）",
                    file=sys.stderr,
                )
        except Exception:
            pass

    if not HAS_TRAY:  # pragma: no cover
        print(
            "[ghlink] 缺少托盘依赖（pystray/Pillow）。"
            "请使用安装包版本，或 pip install pystray pillow",
            file=sys.stderr,
        )
        return 2

    # ③ 单实例锁（李工 09:49 反馈：自启动后多一个托盘）：已有托盘进程则提示退出
    # 李工 14:40 反馈 Windows 托盘闪退根因：PyInstaller onefile 下 exe 运行时
    # 是「引导进程 + Python 子进程」两个同名进程，tasklist 排除自身 PID 仍会
    # 误判引导进程为已有实例 → 托盘启动即退出。改用 _tray_single_instance()
    # （Windows 命名互斥体，macOS/Linux 保留 pgrep 排除自身）
    try:
        if service._tray_single_instance():
            print(
                "[ghlink] 托盘已在运行（单实例），本次启动退出。如需重启托盘请先退出旧实例。",
                file=sys.stderr,
            )
            return 0
    except Exception:
        pass

    # ③ macOS Dock 隐藏（v0.2.16，李工 18:43 反馈：程序坞一直显示 Python）
    _hide_dock_icon()

    # ⑤ v0.2.17：写托盘 PID 文件（存活判定用，detach 后 pgrep 不可靠）
    service._write_tray_pid()

    # v0.2.19（李工 8 条④）：初始图标与 _refresh 同款判定
    # （值守未启用→蓝，修复绿角标+菜单未运行不匹配）
    color = _state_color()
    icon = pystray.Icon(
        "ghlink",
        _make_icon(color),
        _status_text(),
        menu=_build_menu(),
    )

    # 方案 A（李工 13:44 定调）：托盘=值守总开关——启动时若值守未启用则自动开启（幂等）
    # P2-①：enable 的 UAC 弹窗与 icon.run() 前移可能被吞，通知延迟到 run 后线程（避免无效 notify）
    def _auto_enable():
        time.sleep(3)  # 等托盘 UI 就绪
        try:
            if not service._is_enabled() and _run_privileged("enable"):
                try:
                    icon.notify("值守已自动启用", "ghlink")
                except Exception:
                    pass
        except Exception:
            pass

    try:
        if not service._is_enabled():
            threading.Thread(target=_auto_enable, daemon=True).start()
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
