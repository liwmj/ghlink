"""定时任务注册/管理服务（v0.2 阶段 1）。

提供 ghlink enable / disable / status 三命令的跨平台实现：
- enable：注册 1 小时粒度定时任务（v0.2.18 起）（systemd timer / crontab / LaunchDaemon / schtasks）
- disable：移除定时任务
- status：显示当前状态 + 值守状态

权限约定（v0.2 草案）：
- 默认不自启：安装只部署文件，enable 才注册定时任务
- Linux/macOS 需 root；Windows 用 /RL HIGHEST /RU SYSTEM 绕 UAC
- 提权/注册失败：明确报错退出码 2，不静默
"""

import os
import shutil
import sys
import time

from . import platform_adapter, state
from .lock import _pid_alive  # v0.2.17 ⑤：PID 文件兜底存活判定


def _python_cmd() -> str:
    """值守执行入口：优先 wrapper（带 PYTHONPATH），回退裸 python -m。

    2026-08-17 Bug A 修复（拂晓/顾笙双端实锤）：plist/systemd 裸调
    sys.executable -m ghlink.main 无 PYTHONPATH → ModuleNotFoundError，
    值守 enable 了等于没 enable。wrapper（/usr/local/bin/ghlink 或
    /usr/bin/ghlink）自带 PYTHONPATH，必须优先使用。
    """
    # Windows frozen：windowed 入口静默跑（李工 13:34 定）
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        watch = os.path.join(exe_dir, "ghlink-watch.exe")
        if os.path.exists(watch):
            return f'"{watch}"'
    # 优先 PATH 里的 wrapper（brew/deb 安装均落 bin）
    for cand in (shutil.which("ghlink"), "/usr/local/bin/ghlink", "/usr/bin/ghlink"):
        if cand and os.path.exists(cand):
            return f'"{cand}"'
    # 开发模式回退：裸 python -m（源码/venv 环境 PYTHONPATH 天然可用）
    py = sys.executable or "python3"
    return f'"{py}" -m ghlink.main'


def _install_prefix() -> str:
    """配置文件默认位置（/etc/ghlink 或用户目录）。"""
    if os.name == "posix" and os.geteuid() == 0:
        return "/etc/ghlink"
    return os.path.join(os.path.expanduser("~"), ".ghlink")


def _config_path() -> str:
    """配置文件路径：优先用户/系统默认位置，普通用户回退读系统级配置。

    2026-08-17 Bug D（赛博定案）：_install_prefix() 按 euid 分叉，普通用户
    只找 ~/.ghlink/config.json（通常不存在）→ _state_path() 退化到用户目录
    相对路径，读不到 /var/lib/ghlink/ 的绝对路径状态文件 → 心跳恒判不新鲜
    → 误报「值守: 异常（僵尸）」。修复：非 root 且用户 config 不存在时，
    按可读性回退系统级 config（/usr/local/etc/ghlink → /etc/ghlink），
    让普通用户 status/tray 能拿到绝对路径 state_file（Bug C 的 chmod 0644
    在此链路下才能真正闭环）。

    2026-08-21 v0.2.19（李工 8 条②）：补 /opt/homebrew 候选——Apple Silicon
    brew 前缀是 /opt/homebrew，_ensure_config() 模板候选也同步补。
    """
    primary = os.path.join(_install_prefix(), "config.json")
    if os.path.exists(primary):
        return primary
    if os.name == "posix" and os.geteuid() != 0:
        for cand in (
            "/opt/homebrew/etc/ghlink/config.json",
            "/usr/local/etc/ghlink/config.json",
            "/etc/ghlink/config.json",
        ):
            if os.path.exists(cand) and os.access(cand, os.R_OK):
                return cand
    return primary


def _service_name() -> str:
    return "ghlink"


def _state_path() -> str:
    """状态文件路径：优先从 config.json 的 state_file 字段读取，否则默认 ghlink_status.json。

    与 tray.py 同模式（赛博 08:51 复核指出：config.json 本身无 timestamp 字段，
    直接读 config 会把配置当状态文件，导致心跳恒判不新鲜）。
    2026-08-17 赛博补强（Bug B 根治）：相对路径→相对 config.json 目录解析，
    避免 systemd/LaunchDaemon（cwd=/）与 CLI（cwd=用户目录）读写不一致。
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
    # 相对路径 → 相对 config.json 所在目录；绝对路径原样返回
    if st_path and not os.path.isabs(st_path):
        st_path = os.path.join(os.path.dirname(os.path.abspath(cfg_path)), st_path)
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
    # 2026-08-17 Bug B 修复：enable 前确保 config 落位（/etc/ghlink/config.json 不存在则复制）
    _ensure_config()
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


def _ensure_config() -> None:
    """确保配置文件存在：目标 _config_path()，缺失则从 config.example.json 复制。

    2026-08-17 拂晓/顾笙双端实锤 Bug B：deb/brew 装完 /etc/ghlink/config.json
    可能不存在（brew 不落配置、deb 路径不一致），LaunchDaemon/systemd 裸跑
    直接读不到 → 值守僵尸。enable 前兜底复制，保证注册即能跑。
    2026-08-17 Bug D（赛博定案）：复制后 chmod 0644 + 目录 0755，
    让普通用户 status/tray 可读系统级 config（配合 _config_path() fallback），
    Bug C 的 0644 状态文件才能真正闭环。
    """
    import shutil as _shutil

    cfg_path = _config_path()
    if not os.path.exists(cfg_path):
        # 候选模板：当前目录 / 仓库 / 安装包 libexec / 系统 share（v0.2.19 补 /opt/homebrew）
        candidates = [
            os.path.join(os.getcwd(), "config.example.json"),
            os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config.example.json",
            ),
            "/opt/homebrew/Cellar/ghlink/libexec/config.example.json",
            "/usr/local/Cellar/ghlink/libexec/config.example.json",
            "/usr/share/ghlink/config.example.json",
            "/usr/lib/ghlink/config.example.json",
        ]
        for src in candidates:
            if os.path.exists(src):
                os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
                _shutil.copy(src, cfg_path)
                print(f"[ghlink] 已生成默认配置: {cfg_path}")
                break
        else:
            print(
                f"[ghlink] 警告：找不到 config.example.json 模板，跳过配置落位（{cfg_path}）",
                file=sys.stderr,
            )
    # Bug D：配置与目录权限对普通用户可读（enable 以 root 跑，落位后放开读权限）
    if os.path.exists(cfg_path):
        try:
            os.chmod(cfg_path, 0o644)  # NOSONAR
            os.chmod(os.path.dirname(cfg_path), 0o755)  # NOSONAR
        except OSError:
            pass


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
    switched = st.get("last_switched_at") or st.get("switched_at")  # v0.2.18 方案④：兼容旧字段
    print(
        "上次切换: "
        + (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(switched)) if switched else "-")
    )
    # 值守判断（2026-08-17 李工新口径：值守独立于托盘）
    # 主判据=平台任务注册；次判据=心跳新鲜；托盘进程仅展示层
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
    """值守状态细分文本（2026-08-17 李工新口径：值守独立于托盘）。

    主判据=平台任务注册；次判据=心跳新鲜。
    注册+心跳新=已启用；注册但心跳停=僵尸（异常）；未注册=未启用。
    托盘进程降为展示层，附「托盘: 运行中/未运行」辅助行。
    """
    try:
        registered = _is_registered()
        hb = _heartbeat_fresh()
        tray = _tray_alive()
        if registered and hb:
            base = "已启用（值守注册 + 心跳正常）"
        elif registered and not hb:
            base = "异常（值守已注册但心跳已停，疑似僵尸）"
        else:
            base = "未启用（运行 ghlink enable 开启值守）"
        return f"{base}｜托盘: {'运行中' if tray else '未运行'}"
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

            # 赛博 09:56 问题 A：plist 照写（自启动注册必须成功），
            # 已在跑时不重复 load（避免多实例），靠单实例锁兜底
            if not _tray_alive(exclude_pid=os.getpid()):
                _sp.run(["launchctl", "load", plist], check=False)
            else:
                print("[ghlink] 托盘已在运行，仅注册自启动（不重复拉起）")
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


def _tray_single_instance() -> bool:
    """单实例锁：已有托盘实例则返回 True（本次应退出）。

    - Windows：命名互斥体（CreateMutex）——PyInstaller onefile 下 exe 运行时
      是「引导进程 + Python 子进程」两个同名进程，tasklist 排除自身 PID 仍会
      误判引导进程为已有实例（李工 14:40 反馈：Windows 托盘闪退根因）。
      命名互斥体是 Windows 标准单实例方案，onefile 下可靠。
    - macOS/Linux：pgrep/tasklist 排除自身 PID。
    """
    if sys.platform == "win32":
        try:
            import ctypes

            global _TRAY_MUTEX_HANDLE
            # Global\ 前缀：跨会话可见（防快速用户切换/RDP 双会话单实例失效）
            handle = ctypes.windll.kernel32.CreateMutexW(
                None, False, "Global\\ghlink-tray-singleton"
            )
            if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
                return True
            # 模块级持有句柄（防 GC 释放互斥体导致锁失效）
            _TRAY_MUTEX_HANDLE = handle
            return False
        except Exception:
            return False
    return _tray_alive(exclude_pid=os.getpid())


# Windows 命名互斥体句柄（模块级持有，防 GC）
_TRAY_MUTEX_HANDLE = None


def _tray_pid_file() -> str:
    """托盘 PID 文件路径（v0.2.17 ⑤，赛博定案：PID 文件兜底）。

    macOS 上 pgrep 正则会因 detach 后命令行形态（-m ghlink.main tray）
    匹配不一致导致误判「托盘未运行」——改用 PID 文件：托盘启动时写
    自己的 PID，_tray_alive() 优先读 PID 文件 + 进程存活检查，pgrep 降为兜底。
    普通用户进程 → 放用户主目录（/var/lib/ghlink 属 root 不可写）。
    """
    return os.path.join(os.path.expanduser("~"), ".ghlink", "ghlink-tray.pid")


def _write_tray_pid() -> None:
    """托盘启动时写入自身 PID（⑤ 配套）。"""
    try:
        pid_file = _tray_pid_file()
        os.makedirs(os.path.dirname(pid_file), exist_ok=True)
        with open(pid_file, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass


def _tray_alive(exclude_pid: int = 0) -> bool:
    """托盘进程是否存活（展示层用）。

    macOS 优先 PID 文件（v0.2.17 ⑤，detach 后 pgrep 正则不可靠），
    pgrep 兜底；Windows tasklist；Linux 无托盘。
    """
    try:
        if sys.platform == "win32":
            out = platform_adapter._run_cmd_output(
                ["tasklist", "/FO", "CSV", "/FI", "IMAGENAME eq ghlink-tray.exe"]
            )
            if "ghlink-tray.exe" not in (out or ""):
                return False
            if exclude_pid:
                import csv as _csv
                import io as _io

                for row in _csv.reader(_io.StringIO(out or "")):
                    if len(row) >= 2 and row[0].strip('"') == "ghlink-tray.exe":
                        try:
                            if int(row[1]) != exclude_pid:
                                return True
                        except ValueError:
                            pass
                return False
            return True
        elif sys.platform == "darwin":
            # v0.2.17 ⑤：PID 文件优先（detach 后 pgrep -f 正则与命令行形态不一致）
            pid_file = _tray_pid_file()
            try:
                if os.path.exists(pid_file):
                    with open(pid_file, encoding="utf-8") as f:
                        pid = int(f.read().strip())
                    if pid > 0 and pid != exclude_pid and _pid_alive(pid):
                        return True
            except (OSError, ValueError):
                pass
            # 兜底：pgrep（PID 文件缺失/损坏时）
            out = platform_adapter._run_cmd_output(["pgrep", "-f", "ghlink.*tray"])
            pids = [int(x) for x in (out or "").split() if x.strip().isdigit()]
            if exclude_pid:
                pids = [p for p in pids if p != exclude_pid]
            return bool(pids)
        return False
    except Exception:
        return False


def _heartbeat_fresh(max_age_sec: int = 5400) -> bool:
    """状态文件心跳是否新鲜（探测 1 小时粒度，v0.2.18 宽限 90 分钟）。

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
    """值守是否真正在跑（2026-08-17 李工新口径：值守独立于托盘）。

    - 主判据：平台任务注册（enable 即值守，全平台统一）
    - 次判据：状态文件心跳新鲜（≤3 分钟）
    - 注册但心跳停 = 僵尸（异常，不算启用）
    - 托盘进程降为展示层（托盘在但未注册=提示启动值守）
    """
    try:
        if not _is_registered():
            return False
        return _heartbeat_fresh()
    except Exception:
        return False


def _enable_linux() -> int:
    """Linux：优先 systemd timer，回退 crontab。

    v0.2.19（李工 8 条②）：注册完成后立即触发第一轮——
    systemd 直接 start oneshot service；crontab 直接跑一次，不等整点。
    """
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
Description=ghlink hourly timer (v0.2.18: 探测 1 小时粒度)

[Timer]
OnCalendar=hourly
AccuracySec=30s

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
        # v0.2.19：注册完立即跑第一轮（不等 OnCalendar 整点）
        if platform_adapter._run_cmd(["systemctl", "start", "ghlink.service"]):
            print("[ghlink] 已启用值守并立即执行第一轮（systemd timer，1 小时粒度）")
        else:
            print("[ghlink] 已启用值守（systemd timer，1 小时粒度）；首轮手动启动失败，将等整点")
        return 0
    # crontab 回退
    line = f"0 * * * * {_python_cmd()} {_config_path()} >> /var/log/ghlink.log 2>&1"
    crontab = platform_adapter._run_cmd_output(["crontab", "-l"]) or ""
    if line not in crontab:
        new = crontab.rstrip("\n") + "\n" + line + "\n"
        tmp = "/tmp/ghlink.crontab"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(new)
        if not platform_adapter._run_cmd(["crontab", tmp]):
            return 2
        os.unlink(tmp)
    # v0.2.19：注册完立即跑第一轮（不等整点）
    import subprocess as _sp

    try:
        _sp.run(["/bin/sh", "-c", line], timeout=300, check=False)
    except Exception:
        pass
    print("[ghlink] 已启用值守并立即执行第一轮（crontab，1 小时粒度）")
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
        <string>{_python_cmd().strip(chr(34))}</string>
        <string>{_config_path()}</string>
    </array>
    <key>StartInterval</key><integer>3600</integer>
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
    # v0.2.19（李工 8 条②）：注册完立即触发第一轮（RunAtLoad 已保证，kickstart 兜底确保）
    platform_adapter._run_cmd(["launchctl", "kickstart", "-k", "system/com.ghlink.daemon"])
    print("[ghlink] 已启用值守并立即执行第一轮（LaunchDaemon，1 小时粒度）")
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
    # v0.2.19（李工 8 条⑤）：schtasks 失败必须输出真实报错（原 _run_cmd 吞掉 stderr，
    # 导致「值守未运行」无法定位）；注册成功立即 /Run 触发第一轮
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        watch = os.path.join(exe_dir, "ghlink-watch.exe")
        tr = f'"{watch}" {_config_path()}'
    else:
        # 2026-08-17 Bug A 修复：非 frozen 也用 wrapper 入口（带 PYTHONPATH）
        tr = f"{_python_cmd()} {_config_path()}"
    args = [
        "schtasks",
        "/Create",
        "/TN",
        "ghlink",
        "/SC",
        "HOURLY",
        "/TR",
        tr,
        "/RL",
        "HIGHEST",
        "/RU",
        "SYSTEM",
        "/F",
    ]
    if not platform_adapter._run_cmd(args):
        # 输出真实报错（schtasks stderr），帮助定位「值守未运行」根因
        err = platform_adapter._run_cmd_output_error(args)
        print(f"[ghlink] enable 失败：schtasks /Create 未成功。原始输出：{err or '(无输出)'}", file=sys.stderr)
        return 2
    # v0.2.19（李工 8 条②⑤）：注册成功立即触发第一轮 + 输出注册结果
    if platform_adapter._run_cmd(["schtasks", "/Run", "/TN", "ghlink"]):
        print("[ghlink] 已启用值守并立即执行第一轮（schtasks，1 小时粒度，最高权限）")
    else:
        print("[ghlink] 已启用值守（schtasks，1 小时粒度，最高权限）；首轮触发失败，将等整点")
    return 0


def _disable_windows() -> int:
    if not platform_adapter._run_cmd(["schtasks", "/Delete", "/TN", "ghlink", "/F"]):
        return 2
    print("[ghlink] 已停用值守（schtasks 移除）")
    return 0
