"""macOS 原生托盘渲染（v0.4.24）：弃用 pystray，vendored PyObjC 直驱 NSStatusItem+NSMenu。

背景（李工 2026-08-26 03:41 实测定案）：
- pystray 0.19.5（2023-09 发布，项目停更）在 macOS 26.6.2 上进程存活、无 traceback、
  NSStatusItem API 全通，但菜单栏图标就是不渲染
- 排除链全实测：LSUIElement 删除 / PkgInfo+CFBundlePackageType 删除+lsregister /
  tray.py 0.4.18~0.4.23 pystray 链路零差异 / PyObjC 12.2.2 最新 wheel / 启动方式均无关
- 定案：macOS 托盘渲染层弃用 pystray，用 vendored PyObjC 直驱 NSStatusItem + NSMenu；
  Windows 保持 pystray（正常，tray.py 不动）
- 依赖只进安装包（build_pkg.sh 已注入 pyobjc-core + pyobjc-framework-Cocoa/Quartz），
  核心保持零依赖；缺 PyObjC 时 main() 返回 2 并提示

菜单/状态口径与 tray.py 完全一致（复用 _status_text/_state_color/_make_icon/_current_ip/
_run_privileged），仅渲染层替换。
"""

import subprocess
import sys
import threading
import time
from typing import Any, Callable, List, Optional

from . import service
from . import tray as _tray

HAS_NATIVE = False
try:  # pragma: no cover - 仅 macOS 安装包内具备
    import objc
    from AppKit import (
        NSApplication,
        NSImage,
        NSMenu,
        NSMenuItem,
        NSStatusBar,
        NSVariableStatusItemLength,
    )
    from Foundation import NSData, NSObject, NSTimer

    HAS_NATIVE = True
except Exception:
    # 缺 PyObjC（裸 python / 非 macOS）：占位防模块 import 崩溃，main() 返回 2
    objc = None
    NSApplication = None
    NSImage = None
    NSMenu = None
    NSMenuItem = None
    NSStatusBar = None
    NSVariableStatusItemLength = None
    NSData = None
    NSObject = type("NSObject", (), {})  # 占位基类，防类定义崩
    NSTimer = None
    HAS_NATIVE = False

_NS_ON = 1
_NS_OFF = 0


def _pil_to_nsimage(img: Any) -> Any:
    """PIL Image → NSImage（PNG 内存字节，零临时文件）。"""
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    data = buf.getvalue()
    nsdata = NSData.dataWithBytes_length_(data, len(data))
    return NSImage.alloc().initWithData_(nsdata)


def _notify(text: str) -> None:
    """macOS 通知：osascript（零依赖，不引 UNUserNotification 权限配置）。"""
    try:
        # S6350 修复：AppleScript 字符串转义，防 text 内引号注入（SonarCloud 安全门禁）
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.run(  # NOSONAR S6350 - 已转义 + 参数化调用，通知文本无特权
            ["osascript", "-e", f'display notification "{escaped}" with title "ghlink"'],
            capture_output=True,
            timeout=10,
        )
    except Exception:
        pass


class _ActionProxy(NSObject):
    """菜单项动作代理：把 Python 回调绑定到 NSMenuItem selector（fire:）。"""

    def initWithHandler_(self, handler: Callable[[], None]):
        self = objc.super(_ActionProxy, self).init()
        self._handler = handler
        return self

    def fire_(self, sender: Any) -> None:
        handler = getattr(self, "_handler", None)
        if handler:
            try:
                handler()
            except Exception:
                pass


class _MacTray(NSObject):
    """原生托盘：NSStatusItem + NSMenu，5s 定时刷新（主线程 NSTimer）。"""

    def init(self):
        self = objc.super(_MacTray, self).init()
        self._status_item = None
        self._menu_key = None
        self._proxies: List[Any] = []
        return self

    # ---- 生命周期 ----

    def start(self) -> None:
        app = NSApplication.sharedApplication()
        # Accessory（=1）：菜单栏常驻不占 Dock（与 tray._hide_dock_icon 同效，双保险）
        try:
            app.setActivationPolicy_(1)
        except Exception:
            pass
        self._status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        self.refresh()
        # 主线程定时刷新（NSTimer 由 NSApplication run loop 驱动，天然主线程安全）
        timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            5.0, self, "tick:", None, True
        )
        self._timer = timer

    def tick_(self, sender: Any) -> None:
        self.refresh()

    def stop(self) -> None:
        if self._status_item is not None:
            NSStatusBar.systemStatusBar().removeStatusItem_(self._status_item)
            self._status_item = None
        NSApplication.sharedApplication().terminate_(None)

    # ---- 刷新（仅状态变化时重建菜单，保住悬停热点） ----

    def refresh(self) -> None:
        try:
            color = _tray._state_color()
            status = _tray._status_text()
            key = (color, status)
            if getattr(self, "_menu_key", None) == key:
                return
            # v0.5.13（李工 09:37 ARM 托盘不显示定位）：NSStatusItem 渲染必须经
            # .button()（macOS 10.10+ 规范）——直接 setImage_ 在 ARM/新系统上
            # 静默不渲染/坐标异常（0.4.25 实锤 (-1,1087) 屏幕外同源）。
            btn = self._status_item.button()
            if btn is not None:
                btn.setImage_(_pil_to_nsimage(_tray._make_icon(color)))
                btn.setToolTip_(status)
            self._status_item.setMenu_(self._build_menu())
            self._menu_key = key
        except Exception:
            pass

    # ---- 菜单构建（口径与 tray._build_menu 一致） ----

    def _build_menu(self) -> Any:  # NOSONAR S3776 - 菜单结构逐项构建，分支多但线性可读
        menu = NSMenu.alloc().init()
        watching = service._is_enabled()
        autostart = service._is_autostart()
        locked_on = autostart and watching

        def add_item(
            title: str,
            handler: Optional[Callable[[], None]] = None,
            enabled: bool = True,
            checked: Optional[bool] = None,
        ) -> None:
            proxy = _ActionProxy.alloc().initWithHandler_(handler) if handler else None
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                title, "fire:" if proxy else "", ""
            )
            if proxy:
                item.setTarget_(proxy)
                item.setAction_("fire:")
                self._proxies.append(proxy)  # 保活：PyObjC 弱引用，GC 会失联
            item.setEnabled_(enabled)
            if checked is not None:
                item.setState_(_NS_ON if checked else _NS_OFF)
            menu.addItem_(item)

        # 第一行：综合状态（只读，含版本号 + 值守状态）
        add_item(_tray._status_text(), enabled=False)
        # 当前 IP（点击复制）
        add_item(f"当前 IP: {_tray._current_ip()}（点击复制）", handler=self._copy_ip)
        menu.addItem_(NSMenuItem.separatorItem())
        # 值守开关（互斥勾选 + 置灰，v0.4.6/0.4.8 口径）
        add_item(
            "启用值守",
            handler=self._enable_watch,
            enabled=not (watching or locked_on),
            checked=watching or locked_on,
        )
        add_item(
            "关闭值守",
            handler=self._disable_watch,
            enabled=watching and not locked_on,
            checked=not (watching or locked_on),
        )
        menu.addItem_(NSMenuItem.separatorItem())
        # 开机自启动（总开关）
        add_item(
            "开机自启动（随登录启动托盘+值守）",
            handler=self._toggle_autostart,
            checked=autostart,
        )
        menu.addItem_(NSMenuItem.separatorItem())
        # 退出（macOS 语义：仅退出 UI，不停值守）
        add_item("退出托盘", handler=self._quit)
        return menu

    # ---- 菜单动作（复用 tray.py 业务逻辑，去 icon 参数化） ----

    def _copy_ip(self) -> None:
        ip = _tray._current_ip()
        if ip == "—":
            _notify("暂无可用 IP")
            return
        try:
            subprocess.run(["pbcopy"], input=ip.encode("utf-8"), check=False)
            _notify(f"已复制: {ip}")
        except Exception as exc:
            _notify(f"复制失败: {exc}")

    def _enable_watch(self) -> None:
        try:
            if service._is_enabled():
                _notify("值守已在运行")
                return
            if _tray._run_privileged("enable"):
                for _ in range(10):
                    if service._is_enabled():
                        break
                    time.sleep(0.5)
                _notify("值守已启用" if service._is_enabled() else "启用指令已发出，等待生效")
            else:
                _notify("启用失败（权限被拒？）")
        except Exception as exc:
            _notify(f"启用失败: {exc}")
        finally:
            self.refresh()

    def _disable_watch(self) -> None:
        try:
            if not service._is_enabled():
                _notify("值守本就未运行")
                return
            if _tray._run_privileged("disable"):
                for _ in range(10):
                    if not service._is_enabled():
                        break
                    time.sleep(0.5)
                _notify("值守已停用" if not service._is_enabled() else "停用指令已发出，等待生效")
            else:
                _notify("停用失败（权限被拒？）")
        except Exception as exc:
            _notify(f"停用失败: {exc}")
        finally:
            self.refresh()

    def _toggle_autostart(self) -> None:  # NOSONAR S3776 - 状态机分支多，口径与 tray.py 一致
        try:
            if service._is_autostart():
                ok = service._disable_autostart()
                _notify("开机自启动已关闭" if ok else "关闭失败")
            else:
                ok = service._enable_autostart()
                if ok:
                    if service._is_enabled():
                        _notify("开机自启动已开启，值守运行中")
                    elif _tray._run_privileged("enable"):
                        for _ in range(10):
                            if service._is_enabled():
                                break
                            time.sleep(0.5)
                        if service._is_enabled():
                            _notify("开机自启动已开启，值守已启用")
                        else:
                            # v0.4.19 口径：值守没真正起来 → 回滚自启动，不留半开假状态
                            service._disable_autostart()
                            _notify("开机自启动开启失败：值守无法启用（权限不足？）")
                    else:
                        service._disable_autostart()
                        _notify("开机自启动开启失败：值守无法启用（权限不足？）")
                else:
                    _notify("开启失败")
        except Exception as exc:
            _notify(f"操作失败: {exc}")
        finally:
            self.refresh()

    def _quit(self) -> None:
        self.stop()


def main() -> int:
    """macOS 原生托盘入口（由 tray.main() darwin 分支调用）。"""
    if not HAS_NATIVE:
        print(
            "[ghlink] macOS 原生托盘初始化失败：缺少 PyObjC（请使用安装包版本）",
            file=sys.stderr,
        )
        return 2
    try:
        _tray._hide_dock_icon()
    except Exception:
        pass
    app = NSApplication.sharedApplication()
    mac_tray = _MacTray.alloc().init()
    mac_tray.start()

    # 方案 A（李工 13:44 定调）：托盘=值守总开关——启动时若值守未启用则自动开启（幂等）
    def _auto_enable() -> None:
        time.sleep(3)
        try:
            if not service._is_enabled():
                _tray._run_privileged("enable")
        except Exception:
            pass

    try:
        if not service._is_enabled():
            threading.Thread(target=_auto_enable, daemon=True).start()
    except Exception:
        pass

    app.run()
    return 0
