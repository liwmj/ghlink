"""hosts 段落式管理：写入/备份/回滚/自检。

约定（借鉴 GitHub520 思路，全新实现）：
- hosts 段落标记：# ghlink Start / # ghlink End，段落可重复安全更新
- 写入前 backup_hosts()，写入后立即自检（probe 替换域名），失败 restore_hosts()
- 自检失败 → 回滚 + degraded 状态 + 告警，坏配置绝不留场
"""
from typing import Dict, List


def build_block(entries: Dict[str, List[str]]) -> str:
    """由 {domain: [ips]} 生成 hosts 段落文本（含 Start/End 标记）。"""
    # TODO(顾笙)
    return ""


def apply_block(block: str) -> bool:
    """写入 hosts（替换旧段落）；提权/写入失败返回 False。"""
    # TODO(顾笙): 调用 platform_adapter，先备份再写，防重入锁保护
    return True


def verify_after_apply(targets: List[str], timeout_sec: float) -> bool:
    """写入后立即自检：新 IP 下全部目标连通才算成功。"""
    # TODO(顾笙): 复用 probe.probe_all，但解析走 hosts 新条目
    return True
