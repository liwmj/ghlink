"""GitHub520 hosts 段集成（v0.2.18，赛博 22:20 两步走第二步，李工 23:21 并入）。

设计（赛博定案）：
- 周期拉取 https://raw.hellogithub.com/hosts（默认 1 小时，SwitchHosts 同款）
- 合入 ghlink 独立段落（# ghlink Start/End），核心域名 ghlink 自愈优先
- 写入前基础可达性抽检（坏 IP 不入场）
- 核心域名（github.com/api.github.com）仍走 ghlink 动态验证兜底，
  非核心域名才用 GitHub520 社区 IP——互补而不互相拖累
"""

import json
import os
import time
import urllib.request
from typing import Any, Dict, List

# 拉取状态缓存文件（放 state 同目录）
_CACHE_NAME = "ghlink520_cache.json"


def _cache_path(state_dir: str = "") -> str:
    """缓存文件路径：优先 state 目录，兜底用户目录。"""
    if state_dir:
        return os.path.join(state_dir, _CACHE_NAME)
    return os.path.join(os.path.expanduser("~"), ".ghlink", _CACHE_NAME)


def fetch_hosts(url: str, timeout_sec: float = 30) -> str:
    """拉取 GitHub520 hosts 文本（失败抛异常，由调用方降级）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "ghlink/0.2.18"})
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_hosts(text: str) -> Dict[str, List[str]]:
    """解析 hosts 文本 → {domain: [ips]}。跳过注释/空行/坏行。"""
    entries: Dict[str, List[str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        ip, domain = parts[0], parts[1].rstrip(".")
        # 只收 GitHub 生态域名（防社区列表混入无关项）
        if not domain.endswith(
            ("github.com", "githubusercontent.com", "githubassets.com", "fastly.net")
        ):
            continue
        entries.setdefault(domain, [])
        if ip not in entries[domain]:
            entries[domain].append(ip)
    return entries


def _safe_cache_path(state_dir: str = "") -> str:
    """校验并规范化缓存路径（SonarCloud S8707：防符号链接/路径逃逸）。

    要求：绝对路径 + realpath 解析符号链接 + 位于允许目录
    （用户主目录或系统临时目录）内。
    """
    import tempfile

    path = _cache_path(state_dir)
    resolved = os.path.realpath(path)
    if not os.path.isabs(resolved):
        raise ValueError(f"cache path must be absolute: {path}")
    allowed_roots = (os.path.expanduser("~"), tempfile.gettempdir())
    for root in allowed_roots:
        root = os.path.realpath(root)
        if resolved == root or resolved.startswith(root + os.sep):
            return resolved
    raise ValueError(f"cache path outside allowed dirs: {resolved}")


def _ip_reachable(ip: str, timeout_sec: float = 5) -> bool:
    """基础可达性抽检：TCP 443 连通即认为可用（防坏 IP 入场）。"""
    import socket

    try:
        sock = socket.create_connection((ip, 443), timeout=timeout_sec)
        sock.close()
        return True
    except OSError:
        return False


def filter_reachable(entries: Dict[str, List[str]], max_ips: int = 2) -> Dict[str, List[str]]:
    """抽检：每域名保留可达 IP（最多 max_ips 个），全不可达则剔除该域名。"""
    out: Dict[str, List[str]] = {}
    for domain, ips in entries.items():
        ok_ips = [ip for ip in ips[:5] if _ip_reachable(ip)]
        if ok_ips:
            out[domain] = ok_ips[:max_ips]
    return out


def load_cached(state_dir: str = "") -> Dict[str, List[str]]:
    """读本地缓存（拉取失败时兜底，防坏 IP 列表已抽检过）。"""
    try:
        path = _safe_cache_path(state_dir)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return dict(data.get("entries", {}))
    except (OSError, ValueError):
        pass
    return {}


def save_cache(entries: Dict[str, List[str]], state_dir: str = "") -> None:
    """保存抽检后的缓存（供下次拉取失败兜底）。"""
    try:
        path = _safe_cache_path(state_dir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "entries": entries}, f)
    except (OSError, ValueError):
        pass


def sync_github520(cfg: Dict[str, Any], state_dir: str = "") -> Dict[str, List[str]]:
    """拉取 + 解析 + 抽检 + 缓存；失败回退缓存。返回 {domain: [ips]}。

    返回值只含「非核心域名」的社区 IP——核心域名（github.com/api.github.com）
    永远由 ghlink 自愈动态验证兜底，不写死 GitHub520 静态 IP。
    """
    g = cfg.get("github520", {})
    if not g.get("enabled", True):
        return {}
    url = g.get("url", "https://raw.hellogithub.com/hosts")
    timeout_sec = float(g.get("timeout_sec", 30))
    # 核心域名排除（自愈优先）
    core = set(cfg.get("probe", {}).get("core_targets", ["github.com", "api.github.com"]))

    try:
        text = fetch_hosts(url, timeout_sec)
        entries = parse_hosts(text)
        entries = {d: ips for d, ips in entries.items() if d not in core}
        entries = filter_reachable(entries)
        if entries:
            save_cache(entries, state_dir)
            return entries
    except Exception:
        pass
    # 拉取失败 → 缓存兜底（缓存已抽检过）
    cached = load_cached(state_dir)
    return {d: ips for d, ips in cached.items() if d not in core}
