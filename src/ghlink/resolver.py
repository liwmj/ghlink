"""IP 多源获取：DoH 多源 + 系统 DNS 直查 + 本地缓存，多数票 + 健康加权。

设计约束：
- 单源失败自动剔除并标记（状态文件 source_health 记录）
- 多源结果取多数票/交集，候选先做 TCP 443 预检，通过才进入替换
- 标准库 urllib 实现 DoH（GET /resolve?name=xxx&type=A，Accept: application/dns-json）
"""
from typing import Dict, List, Optional


def query_doh(source_url: str, domain: str, timeout_sec: float) -> List[str]:
    """从单个 DoH 源查询 A 记录，返回 IP 列表；失败返回空列表。"""
    # TODO(顾笙): urllib GET + json 解析（阿里/腾讯/CF/Google 响应格式兼容）
    return []


def query_system_dns(domain: str) -> List[str]:
    """系统 DNS 直查（socket.getaddrinfo），失败返回空列表。"""
    # TODO(顾笙)
    return []


def resolve_best(domain: str, cfg: Dict[str, object]) -> List[str]:
    """多源获取 + 多数票 + 预检，返回候选 IP 列表（已按健康度排序）。"""
    # TODO(顾笙): 并行查多 DoH + 系统 DNS + 本地缓存(上次成功) + 历史交集
    # TODO(顾笙): 候选 TCP 443 预检，剔除不通；无候选时返回 []
    return []
