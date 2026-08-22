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

from .builtin_github520 import BUILTIN_GITHUB520_HOSTS  # v0.4.0：首装断网/拉取失败兜底

# 拉取状态缓存文件（放 state 同目录）
_CACHE_NAME = "ghlink520_cache.json"


def _cache_path(state_dir: str = "") -> str:
    """缓存文件路径：优先 state 目录，兜底用户目录。"""
    if state_dir:
        return os.path.join(state_dir, _CACHE_NAME)
    return os.path.join(os.path.expanduser("~"), ".ghlink", _CACHE_NAME)


def fetch_hosts(url: str, timeout_sec: float = 30) -> str:
    """拉取 GitHub520 hosts 文本（失败抛异常，由调用方降级）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "ghlink/0.4.0"})
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
    """日常轮次：拉取 + 解析 + 抽检 + 缓存；失败回退缓存/内置快照。

    返回非核心域名的社区 IP（核心域名由动态自愈优先，不写死静态 IP）。
    """
    return _sync(cfg, state_dir, include_core=False)


def initial_entries(cfg: Dict[str, Any], state_dir: str = "") -> Dict[str, List[str]]:
    """首装全量兜底（v0.4.0 新增，李工 12:35 点 1）：含全部域名（含核心），
    预检过的 IP 排前、未预检的排后——首装/动态失败时 hosts 必有可用条目。
    """
    return _sync(cfg, state_dir, include_core=True, full_write=True)


def _sync(
    cfg: Dict[str, Any],
    state_dir: str = "",
    include_core: bool = False,
    full_write: bool = False,
) -> Dict[str, List[str]]:
    """核心同步逻辑。include_core=保留核心域名；full_write=全量写（预检过排前）。"""
    g = cfg.get("github520", {})
    if not g.get("enabled", True):
        return {}
    url = g.get("url", "https://raw.hellogithub.com/hosts")
    timeout_sec = float(g.get("timeout_sec", 30))
    core = set(cfg.get("probe", {}).get("core_targets", ["github.com", "api.github.com"]))

    try:
        text = fetch_hosts(url, timeout_sec)
        entries = parse_hosts(text)
        if not include_core:
            entries = {d: ips for d, ips in entries.items() if d not in core}
        if full_write:
            entries = _sort_prechecked_first(entries, min(timeout_sec, 5.0))
        else:
            entries = filter_reachable(entries)
        if entries:
            save_cache(entries, state_dir)
            return entries
    except Exception:
        pass
    # 拉取失败 → 缓存兜底（缓存已抽检过）
    cached = load_cached(state_dir)
    if cached:
        return {d: ips for d, ips in cached.items() if d not in core}
    # 内置快照兜底（防首装断网尴尬）
    builtin = parse_hosts(BUILTIN_GITHUB520_HOSTS)
    if not include_core:
        builtin = {d: ips for d, ips in builtin.items() if d not in core}
    if full_write:
        builtin = _sort_prechecked_first(builtin, min(timeout_sec, 5.0))
    else:
        builtin = filter_reachable(builtin)
    if builtin:
        save_cache(builtin, state_dir)
        return builtin
    return {}


def _sort_prechecked_first(
    entries: Dict[str, List[str]], timeout_sec: float = 5.0
) -> Dict[str, List[str]]:
    """v0.4.0：全量写入时预检过的 IP 排前、未预检的排后（hosts 取首个命中）。"""
    out: Dict[str, List[str]] = {}
    for domain, ips in entries.items():
        ok_ips = [ip for ip in ips if _ip_reachable(ip, timeout_sec)]
        rest = [ip for ip in ips if ip not in ok_ips]
        out[domain] = ok_ips + rest
    return out
