"""hosts 段落式管理：写入/备份/回滚/自检。

约定（借鉴 GitHub520 思路，全新实现）：
- hosts 段落标记：# ghlink Start / # ghlink End，段落可重复安全更新
- 写入前 backup_hosts()，写入后立即自检（probe 替换域名），失败 restore_hosts()
- 自检失败 → 回滚 + degraded 状态 + 告警，坏配置绝不留场
"""
import os
from typing import Dict, List

from . import platform_adapter

START_MARK = "# ghlink Start"
END_MARK = "# ghlink End"


def build_block(entries: Dict[str, List[str]]) -> str:
    """由 {domain: [ips]} 生成 hosts 段落文本（含 Start/End 标记）。"""
    lines = [START_MARK]
    for domain, ips in entries.items():
        for ip in ips:
            lines.append(f"{ip} {domain}")
    lines.append(END_MARK)
    return "\n".join(lines) + "\n"


def _read_hosts(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _write_hosts(path: str, content: str) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return True
    except OSError:
        return False


def apply_block(block: str, backup_dir: str = "backup") -> tuple:
    """写入 hosts（替换旧段落）；提权/写入失败返回 (False, "")。

    返回 (ok, backup_path)：ok=False 表示写入失败；ok=True 时 backup_path
    为本次写入前的备份文件路径（供自检失败回滚使用）。
    """
    if not platform_adapter.ensure_privilege():
        return False, ""
    path = platform_adapter.get_hosts_path()
    content = _read_hosts(path)

    # 替换旧段落（幂等：无论旧段落是否存在）
    start = content.find(START_MARK)
    end = content.find(END_MARK)
    if start != -1 and end != -1 and end > start:
        before = content[:start]
        after = content[end + len(END_MARK):]
        content = before + block + after
    elif start == -1 and end == -1:
        content = content.rstrip("\n") + "\n" + block
    else:
        # 段落标记不完整，视为异常：整体重建安全内容
        return False, ""

    backup = platform_adapter.backup_hosts(backup_dir)
    if not backup:
        return False, ""
    if not _write_hosts(path, content):
        platform_adapter.restore_hosts(backup)
        return False, ""
    platform_adapter.flush_dns()
    return True, backup


def verify_after_apply(targets: List[str], timeout_sec: float) -> bool:
    """写入后立即自检：新 IP 下全部目标连通才算成功。"""
    from . import probe
    results = probe.probe_all(targets, timeout_sec)
    return probe.round_ok(results)


def rollback(backup_path: str) -> bool:
    """回滚 hosts 到备份版本。"""
    return platform_adapter.restore_hosts(backup_path)
