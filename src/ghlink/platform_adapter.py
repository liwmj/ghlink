"""平台适配层：所有平台差异收敛于此，业务代码不写平台分支。

接口：
- get_hosts_path() -> str
- ensure_privilege() -> bool        # 提权失败返回 False（走降级路径）
- flush_dns() -> bool               # 刷新 DNS 缓存
- backup_hosts() -> str             # 返回备份路径
- restore_hosts(backup_path) -> bool
"""
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def get_hosts_path() -> str:
    """返回当前平台 hosts 文件路径。"""
    if sys.platform == "win32":
        return r"C:\Windows\System32\drivers\etc\hosts"
    return "/etc/hosts"


def _is_admin() -> bool:
    """当前进程是否具备管理员/root 权限。"""
    if sys.platform == "win32":
        try:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    return os.geteuid() == 0


def ensure_privilege() -> bool:
    """检查/提升权限；失败返回 False，调用方走降级路径（只告警不替换）。

    - Windows：非管理员时尝试 ShellExecuteW runas 提权重跑自身；提权进程启动成功后
      旧进程立即退出（避免继续以非管理员身份执行造成虚假告警）；失败返回 False
    - Linux/macOS：非 root 时提示 sudo 重跑，返回 False
    """
    if _is_admin():
        return True
    if sys.platform == "win32":
        try:
            import ctypes
            params = " ".join(f'"{a}"' for a in sys.argv)
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, params, None, 1
            )
            if ret > 32:
                # P1-1: 提权进程已启动，旧进程立即退出，避免双跑/虚假 degraded 告警
                sys.exit(0)
            return False
        except Exception:
            return False
    # Linux/macOS：无法自动提权（sudo 需要交互），返回 False 走降级
    print("[ghlink] 需要 root 权限运行：请使用 sudo 重新执行", file=sys.stderr)
    return False


def _run_cmd(args, timeout: int = 15) -> bool:
    """执行命令，成功（returncode==0）返回 True。"""
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


def flush_dns() -> bool:
    """刷新 DNS 缓存；失败返回 False（记录日志，不阻断）。

    - Windows：ipconfig /flushdns
    - macOS：dscacheutil -flushcache; killall -HUP mDNSResponder
    - Linux：resolvectl flush-caches → 回退 systemd-resolve → nscd
    """
    if sys.platform == "win32":
        return _run_cmd(["ipconfig", "/flushdns"])
    if sys.platform == "darwin":
        ok = _run_cmd(["dscacheutil", "-flushcache"])
        _run_cmd(["killall", "-HUP", "mDNSResponder"])
        return ok
    # Linux 回退链
    for args in (
        ["resolvectl", "flush-caches"],
        ["systemd-resolve", "--flush-caches"],
        ["nscd", "-i", "hosts"],
    ):
        if _run_cmd(args):
            return True
    return False


def backup_hosts(backup_dir: str = "backup") -> str:
    """写入前备份 hosts，返回备份文件路径；失败返回空字符串。"""
    hosts = get_hosts_path()
    if not os.path.exists(hosts):
        return ""
    try:
        Path(backup_dir).mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d%H%M%S")
        backup_path = os.path.join(backup_dir, f"hosts.{ts}.bak")
        # P2: 同秒备份去重：若已存在则追加毫秒后缀
        n = 0
        while os.path.exists(backup_path):
            n += 1
            backup_path = os.path.join(backup_dir, f"hosts.{ts}.{n}.bak")
        shutil.copy2(hosts, backup_path)
        return backup_path
    except OSError:
        return ""


def restore_hosts(backup_path: str) -> bool:
    """回滚 hosts 到备份版本。"""
    if not backup_path or not os.path.exists(backup_path):
        return False
    try:
        hosts = get_hosts_path()
        if not _is_admin():
            return False
        shutil.copy2(backup_path, hosts)
        return True
    except OSError:
        return False
