"""平台适配层：所有平台差异收敛于此，业务代码不写平台分支。

接口（顾笙实现，平台差异填 TODO）：
- get_hosts_path() -> str
- ensure_privilege() -> bool        # 提权失败返回 False（走降级路径）
- flush_dns() -> bool               # 刷新 DNS 缓存
- backup_hosts() -> str             # 返回备份路径
- restore_hosts(backup_path) -> bool
"""
import os
import sys


def get_hosts_path() -> str:
    """返回当前平台 hosts 文件路径。"""
    if sys.platform == "win32":
        return r"C:\Windows\System32\drivers\etc\hosts"
    return "/etc/hosts"


def ensure_privilege() -> bool:
    """检查/提升权限；失败返回 False，调用方走降级路径（只告警不替换）。"""
    # TODO(顾笙): Windows=ctypes ShellExecuteW UAC 提权 / IsUserAnAdmin 检查
    # TODO(顾笙): Linux/macOS=os.geteuid()==0 检查，非 root 提示 sudo 重跑
    return True


def flush_dns() -> bool:
    """刷新 DNS 缓存；失败返回 False（记录日志，不阻断）。"""
    # TODO(顾笙): Windows=ipconfig /flushdns
    # TODO(顾笙): macOS=dscacheutil -flushcache; killall -HUP mDNSResponder
    # TODO(顾笙): Linux=resolvectl flush-caches → 回退 systemd-resolve → nscd
    return True


def backup_hosts() -> str:
    """写入前备份 hosts，返回备份文件路径。"""
    # TODO(顾笙): 备份到配置的 hosts_backup_dir，带时间戳
    return ""


def restore_hosts(backup_path: str) -> bool:
    """回滚 hosts 到备份版本。"""
    # TODO(顾笙)
    return True
