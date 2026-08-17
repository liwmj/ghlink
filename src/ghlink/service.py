"""定时任务注册/管理服务（v0.2 阶段 1）。

提供 ghlink enable / disable / status 三命令的跨平台实现：
- enable：注册 1 分钟粒度定时任务（systemd timer / crontab / LaunchDaemon / schtasks）
- disable：移除定时任务
- status：显示当前状态 + 值守状态

权限约定（v0.2 草案）：
- 默认不自启：安装只部署文件，enable 才注册定时任务
- Linux/macOS 需 root；Windows 用 /RL HIGHEST /RU SYSTEM 绕 UAC
- 提权/注册失败：明确报错退出码 2，不静默
"""

import os
import sys
import time

from . import platform_adapter, state


def _python_cmd() -> str:
    """当前解释器路径 + ghlink 模块入口。"""
    py = sys.executable or "python3"
    return f'"{py}" -m ghlink.main'


def _install_prefix() -> str:
    """配置文件默认位置（/etc/ghlink 或用户目录）。"""
    if os.name == "posix" and os.geteuid() == 0:
        return "/etc/ghlink"
    return os.path.join(os.path.expanduser("~"), ".ghlink")


def _config_path() -> str:
    return os.path.join(_install_prefix(), "config.json")


def _service_name() -> str:
    return "ghlink"


def _state_path() -> str:
    """状态文件路径：优先从 config.json 的 state_file 字段读取，否则默认 ghlink_status.json。

    与 tray.py 同模式（赛博 08:51 复核指出：config.json 本身无 timestamp 字段，
    直接读 config 会把配置当状态文件，导致心跳恒判不新鲜）。
    """
    cfg_path = _config_path()
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


def enable() -> int:
    """注册定时任务。返回退出码（0=成功，2=权限/错误）。"""
    if not platform_adapter.ensure_privilege():
        print(
            "[ghlink] 错误：需要管理员/root 权限。"
            "请运行 sudo ghlink enable（Linux/macOS）或管理员命令行（Windows）",
            file=sys.stderr,
        )
        return 2
    try:
        if sys.platform == "win32":
            return _enable_windows()
        elif sys.platform == "darwin":
            return _enable_macos()
        else:
            return _enable_linux()
    except Exception as exc:
        print(f"[ghlink] enable 失败: {exc}", file=sys.stderr)
        return 2


def disable() -> int:
    """移除定时任务。返回退出码（0=成功，2=权限/错误）。"""
    if not platform_adapter.ensure_privilege():
        print(
            "[ghlink] 错误：需要管理员/root 权限。"
            "请运行 sudo ghlink disable（Linux/macOS）或管理员命令行（Windows）",
            file=sys.stderr,
        )
        return 2
    try:
        if sys.platform == "win32":
            return _disable_windows()
        elif sys.platform == "darwin":
            return _disable_macos()
        else:
            return _disable_linux()
    except Exception as exc:
        print(f"[ghlink] disable 失败: {exc}", file=sys.stderr)
        return 2


def status() -> int:
    """显示当前状态 + 值守状态。始终返回 0。"""
    st = state.load(_state_path())
    cur_ip = st.get("current_ip") or _hosts_github_ip() or _dns_github_ip()
    print("=== ghlink status ===")
    print(f"状态: {st.get('state', 'normal')}")
    print(f"当前IP: {cur_ip or '-'}")
    print(f"失败计数: {st.get('probe', {}).get('consecutive_failures', 0)}")
    print(f"最近错误: {st.get('last_error') or '-'}")
    switched = st.get("switched_at")
    print(
        "上次切换: "
        + (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(switched)) if switched else "-")
    )
    # 值守判断（双确认，2026-08-17 赛博复核口径）：
    # macOS/Windows 主判据=托盘进程存活；Linux 主判据=平台任务注册；次判据=心跳新鲜
    print(f"值守: {_watch_status_text()}")
    history = st.get("history") or []
    if history:
        print("最近记录:")
        for h in history[-5:]:
            print(
                f"  - {h.get('time', '')} {h.get('domain', '')} → {h.get('ip', '')} "
                f"({h.get('trigger', '')})"
            )
    return 0


def _watch_status_text() -> str:
    """值守状态细分文本（双确认口径，2026-08-17 赛博复核）。

    macOS/Windows 载体=托盘进程；Linux 载体=平台任务注册；次判据=心跳新鲜。
    载体在+心跳新=已启用；载体在但心跳停=僵尸（异常）；载体不在=未启用。
    """
    try:
        if sys.platform in ("win32", "darwin"):
            carrier = _tray_alive()
        else:
            carrier = _is_registered()
        hb = _heartbeat_fresh()
        if carrier and hb:
            return "已启用（载体在 + 心跳正常）"
        if carrier and not hb:
            return "异常（载体在但心跳已停，疑似僵尸进程）"
        return "未启用（运行 ghlink enable 或启动托盘开启）"
    except Exception:
        return "未知"


def _dns_github_ip() -> str:
    """系统 DNS 解析 github.com 的 IP（hosts 无条目时兜底，v0.2.9 补全链路）。"""
    try:
        import socket

        infos = socket.getaddrinfo("github.com", None, socket.AF_INET)
        return str(infos[0][4][0])
    except Exception:
        return ""


def _hosts_github_ip() -> str:
    """hosts 里当前生效的 github.com IP（未切换时兜底显示，与托盘口径一致 v0.2.8）。"""
    try:
        import os as _os

        if sys.platform == "win32":
            root = _os.environ.get("SYSTEMROOT", r"C:\Windows")
            hosts_path = _os.path.join(root, "System32", "drivers", "etc", "hosts")
        else:
            hosts_path = "/etc/hosts"
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


def _is_autostart() -> bool:
    """检测开机自启动是否已注册（Windows Run key / macOS LaunchAgent / Linux .desktop）。"""
    try:
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
            ) as key:
                winreg.QueryValueEx(key, "ghlink-tray")
                return True
        elif sys.platform == "darwin":
            return os.path.exists(
                os.path.expanduser("~/Library/LaunchAgents/com.ghlink.tray.plist")
            )
        else:
            return os.path.exists(os.path.expanduser("~/.config/autostart/ghlink-tray.desktop"))
    except Exception:
        return False


def _enable_autostart() -> bool:
    """注册开机自启动（托盘随登录启动）。用户级，无需提权。"""
    try:
        if sys.platform == "win32":
            import winreg

            exe = os.path.join(os.path.dirname(sys.executable), "ghlink-tray.exe")
            if not os.path.exists(exe):
                exe = sys.executable
            with winreg.CreateKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
            ) as key:
                winreg.SetValueEx(key, "ghlink-tray", 0, winreg.REG_SZ, f'"{exe}"')
            return True
        elif sys.platform == "darwin":
            import shutil

            plist_dir = os.path.expanduser("~/Library/LaunchAgents")
            os.makedirs(plist_dir, exist_ok=True)
            plist = os.path.join(plist_dir, "com.ghlink.tray.plist")
            # P1（赛博 23:54 复核）：brew 安装无 ghlink-tray 二进制，用 PATH 里的 ghlink wrapper
            exe = shutil.which("ghlink") or sys.executable
            with open(plist, "w", encoding="utf-8") as f:
                f.write(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.ghlink.tray</string>
  <key>ProgramArguments</key><array><string>{exe}</string><string>tray</string></array>
  <key>RunAtLoad</key><true/>
</dict></plist>
""")
            import subprocess as _sp

            _sp.run(["launchctl", "load", plist], check=False)
            return True
        else:
            autostart_dir = os.path.expanduser("~/.config/autostart")
            os.makedirs(autostart_dir, exist_ok=True)
            desktop = os.path.join(autostart_dir, "ghlink-tray.desktop")
            with open(desktop, "w", encoding="utf-8") as f:
                f.write("[Desktop Entry]\nType=Application\nName=ghlink tray\nExec=ghlink tray\n")
            return True
    except Exception:
        return False


def _disable_autostart() -> bool:
    """移除开机自启动。"""
    try:
        if sys.platform == "win32":
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                winreg.DeleteValue(key, "ghlink-tray")
            return True
        elif sys.platform == "darwin":
            plist = os.path.expanduser("~/Library/LaunchAgents/com.ghlink.tray.plist")
            import subprocess as _sp

            _sp.run(["launchctl", "unload", plist], check=False)
            if os.path.exists(plist):
                os.remove(plist)
            return True
        else:
            desktop = os.path.expanduser("~/.config/autostart/ghlink-tray.desktop")
            if os.path.exists(desktop):
                os.remove(desktop)
            return True
    except Exception:
        return False


def _is_registered() -> bool:
    """平台定时任务是否已注册（enable/disable 幂等判断用，旧口径）。"""
    try:
        if sys.platform == "win32":
            r = platform_adapter._run_cmd(["schtasks", "/Query", "/TN", _service_name()])
            return r
        elif sys.platform == "darwin":
            return os.path.exists("/Library/LaunchDaemons/com.ghlink.plist")
        else:
            # Linux: systemd timer 或 crontab
            if os.path.exists("/etc/systemd/system/ghlink.timer"):
                return True
            out = platform_adapter._run_cmd_output(["crontab", "-l"])
            return "ghlink.main" in (out or "")
    except Exception:
        return False


def _tray_alive() -> bool:
    """值守载体（托盘进程）是否存活。macOS/Windows 托盘=总开关；Linux 纯 CLI 无托盘。"""
    try:
        if sys.platform == "win32":
            out = platform_adapter._run_cmd_output(
                ["tasklist", "/FI", "IMAGENAME eq ghlink-tray.exe"]
            )
            return "ghlink-tray.exe" in (out or "")
        elif sys.platform == "darwin":
            out = platform_adapter._run_cmd_output(["pgrep", "-f", "ghlink.*tray"])
            return bool(out and out.strip())
        return False
    except Exception:
        return False


def _heartbeat_fresh(max_age_sec: int = 180) -> bool:
    """状态文件心跳是否新鲜（探测 1 分钟粒度，默认 3 分钟宽限）。

    状态文件由 run() 每轮探测后 save 更新 timestamp；心跳停 = 探测循环没在跑。
    """
    try:
        st = state.load(_state_path())
        ts = st.get("timestamp") or ""
        if not ts:
            return False
        t = time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
        age = time.time() - time.mktime(t)
        return 0 <= age <= max_age_sec
    except Exception:
        return False


def _is_enabled() -> bool:
    """值守是否真正在跑（双确认，2026-08-17 赛博复核口径）。

    - 主判据：值守载体存活（macOS/Windows 托盘进程；Linux 平台任务注册）
    - 次判据：状态文件心跳新鲜（≤3 分钟）
    - 载体在但心跳停 = 僵尸（异常，不算启用）
    """
    try:
        if sys.platform in ("win32", "darwin"):
            if not _tray_alive():
                return False
        else:
            if not _is_registered():
                return False
        return _heartbeat_fresh()
    except Exception:
        return False


def _enable_linux() -> int:
    """Linux：优先 systemd timer，回退 crontab。"""
    if os.path.exists("/run/systemd/system"):
        unit = f"""[Unit]
Description=ghlink GitHub connectivity self-healing
After=network.target

[Service]
Type=oneshot
ExecStart={_python_cmd()} {_config_path()}

[Install]
WantedBy=multi-user.target
"""
        timer = """[Unit]
Description=ghlink hourly? no, 1-minute timer

[Timer]
OnCalendar=*-*-* *:*:00
AccuracySec=5s

[Install]
WantedBy=timers.target
"""
        with open("/etc/systemd/system/ghlink.service", "w", encoding="utf-8") as f:
            f.write(unit)
        with open("/etc/systemd/system/ghlink.timer", "w", encoding="utf-8") as f:
            f.write(timer)
        if not platform_adapter._run_cmd(["systemctl", "daemon-reload"]):
            return 2
        if not platform_adapter._run_cmd(["systemctl", "enable", "--now", "ghlink.timer"]):
            return 2
        print("[ghlink] 已启用值守（systemd timer，1 分钟粒度）")
        return 0
    # crontab 回退
    line = f"* * * * * {_python_cmd()} {_config_path()} >> /var/log/ghlink.log 2>&1"
    crontab = platform_adapter._run_cmd_output(["crontab", "-l"]) or ""
    if line not in crontab:
        new = crontab.rstrip("\n") + "\n" + line + "\n"
        tmp = "/tmp/ghlink.crontab"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new)
        if not platform_adapter._run_cmd(["crontab", tmp]):
            return 2
        os.unlink(tmp)
    print("[ghlink] 已启用值守（crontab，1 分钟粒度）")
    return 0


def _disable_linux() -> int:
    if os.path.exists("/etc/systemd/system/ghlink.timer"):
        platform_adapter._run_cmd(["systemctl", "disable", "--now", "ghlink.timer"])
        for p in ("/etc/systemd/system/ghlink.timer", "/etc/systemd/system/ghlink.service"):
            if os.path.exists(p):
                os.unlink(p)
        platform_adapter._run_cmd(["systemctl", "daemon-reload"])
        print("[ghlink] 已停用值守（systemd timer 移除）")
        return 0
    crontab = platform_adapter._run_cmd_output(["crontab", "-l"]) or ""
    lines = [ln for ln in crontab.splitlines() if "ghlink.main" not in ln]
    tmp = "/tmp/ghlink.crontab"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    if not platform_adapter._run_cmd(["crontab", tmp]):
        return 2
    os.unlink(tmp)
    print("[ghlink] 已停用值守（crontab 条目移除）")
    return 0


def _enable_macos() -> int:
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.ghlink.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>-m</string>
        <string>ghlink.main</string>
        <string>{_config_path()}</string>
    </array>
    <key>StartInterval</key><integer>60</integer>
    <key>RunAtLoad</key><true/>
    <key>StandardOutPath</key><string>/var/log/ghlink.log</string>
    <key>StandardErrorPath</key><string>/var/log/ghlink.log</string>
</dict>
</plist>
"""
    with open("/Library/LaunchDaemons/com.ghlink.plist", "w", encoding="utf-8") as f:
        f.write(plist)
    if not platform_adapter._run_cmd(
        ["launchctl", "load", "/Library/LaunchDaemons/com.ghlink.plist"]
    ):
        return 2
    print("[ghlink] 已启用值守（LaunchDaemon，1 分钟粒度）")
    return 0


def _disable_macos() -> int:
    p = "/Library/LaunchDaemons/com.ghlink.plist"
    if os.path.exists(p):
        platform_adapter._run_cmd(["launchctl", "unload", p])
        # 2026-08-17 赛博提醒：unload 可能残留已加载实例，remove 按 Label 彻底清
        platform_adapter._run_cmd(["launchctl", "remove", "com.ghlink.daemon"])
        os.unlink(p)
    print("[ghlink] 已停用值守（LaunchDaemon 移除）")
    return 0


def _enable_windows() -> int:
    # P1 修复（赛博 2026-08-14）：参数数组传递，不用 split() 拆命令串——
    # /TR 的引号参数（含空格路径）必须作为单个元素，split() 会拆坏导致 schtasks 注册失败
    # 弹窗修复（李工 13:34 反馈）：值守用 windowed 入口（ghlink-watch.exe）静默跑，不弹命令行
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        watch = os.path.join(exe_dir, "ghlink-watch.exe")
        tr = f'"{watch}" {_config_path()}'
    else:
        tr = f"{sys.executable} -m ghlink.main {_config_path()}"
    args = [
        "schtasks",
        "/Create",
        "/TN",
        "ghlink",
        "/SC",
        "MINUTE",
        "/MO",
        "1",
        "/TR",
        tr,
        "/RL",
        "HIGHEST",
        "/RU",
        "SYSTEM",
        "/F",
    ]
    if not platform_adapter._run_cmd(args):
        return 2
    print("[ghlink] 已启用值守（schtasks，1 分钟粒度，最高权限）")
    return 0


def _disable_windows() -> int:
    if not platform_adapter._run_cmd(["schtasks", "/Delete", "/TN", "ghlink", "/F"]):
        return 2
    print("[ghlink] 已停用值守（schtasks 移除）")
    return 0
