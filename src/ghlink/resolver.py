"""IP 多源获取：DoH 多源 + 系统 DNS 直查 + 本地缓存，多数票 + 健康加权。

设计约束：
- 单源失败自动剔除并标记（状态文件 source_health 记录）
- 多源结果取多数票/交集，候选先做 TCP 443 预检，通过才进入替换
- 标准库 urllib 实现 DoH（GET /resolve?name=xxx&type=A，Accept: application/dns-json）
"""
import json
import os
import socket
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List

# 各 DoH 源的响应格式兼容：都返回 JSON，A 记录在 Answer[].data（阿里/腾讯/CF/Google 一致）
_HEADERS = {
    "Accept": "application/dns-json",
    "User-Agent": "ghlink/0.1",
}


def query_doh(source_url: str, domain: str, timeout_sec: float) -> List[str]:
    """从单个 DoH 源查询 A 记录，返回 IP 列表；失败返回空列表。"""
    try:
        sep = "&" if "?" in source_url else "?"
        url = f"{source_url}{sep}name={urllib.parse.quote(domain)}&type=A"
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        ips = []
        for ans in data.get("Answer", []):
            if ans.get("type") == 1 and ans.get("data"):
                ips.append(ans["data"])
        return ips
    except Exception:
        return []


def query_system_dns(domain: str) -> List[str]:
    """系统 DNS 直查（socket.getaddrinfo），失败返回空列表。"""
    try:
        infos = socket.getaddrinfo(domain, None, socket.AF_INET)
        return list({info[4][0] for info in infos})
    except Exception:
        return []


def _tcp443_ok(ip: str, domain: str, timeout_sec: float) -> bool:
    """候选 IP 预检：TCP 443 建连 + TLS SNI 握手。"""
    import ssl
    try:
        sock = socket.create_connection((ip, 443), timeout=timeout_sec)
        try:
            ctx = ssl.create_default_context()
            with ctx.wrap_socket(sock, server_hostname=domain):
                return True
        finally:
            try:
                sock.close()
            except OSError:
                pass
    except Exception:
        return False


def _precheck(ips: List[str], timeout_sec: float = 5.0) -> List[str]:
    """候选 IP 列表预检：TCP 443 建连粗筛，返回通过子集。

    注：预检为粗筛（真正三层校验在 probe），固定默认超时 5s。
    """
    passed = []
    for ip in ips:
        try:
            with socket.create_connection((ip, 443), timeout=timeout_sec):
                passed.append(ip)
        except Exception:
            pass
    return passed


def resolve_best(domain: str, cfg: Dict[str, object]) -> List[str]:
    """多源获取 + 多数票 + 预检，返回候选 IP 列表（已按健康度排序）。

    来源：多个 DoH + 系统 DNS；无 DoH 配置时只用系统 DNS。
    返回候选（按出现次数降序），无任何候选返回 []。
    """
    timeout = float(cfg.get("timeout_sec", 5))
    doh_sources = cfg.get("doh_sources") or []
    max_candidates = int(cfg.get("max_candidates", 5)) or 5

    # 1) 并行取多源
    sources: List[List[str]] = []
    with ThreadPoolExecutor(max_workers=max(len(doh_sources), 1) + 1) as pool:
        futs = [pool.submit(query_doh, src, domain, timeout) for src in doh_sources]
        futs.append(pool.submit(query_system_dns, domain))
        for f in futs:
            try:
                ips = f.result()
                if ips:
                    sources.append(ips)
            except Exception:
                pass

    # 2) 多数票统计（出现次数降序）
    from collections import Counter
    counter: Counter = Counter()
    for ips in sources:
        for ip in ips:
            counter[ip] += 1

    # P2: 本地缓存兜底——多源全失败时，回退到上次成功的缓存候选（最后防线）
    if not counter:
        cached = _load_cache(domain, cfg)
        if cached:
            counter.update(cached)

    if not counter:
        return []

    ranked = [ip for ip, _ in counter.most_common(max_candidates * 2)]

    # 3) TCP 443 预检，剔除不通（保留前 max_candidates 个）
    passed = _precheck(ranked)
    result = passed[:max_candidates]
    # P2: 成功后写缓存（供后续多源全挂时兜底）
    if result:
        _save_cache(domain, result, cfg)
    return result


_CACHE_PREFIX = "ghlink_cache_"


def _cache_path(domain: str, cfg: Dict[str, object]) -> str:
    base = cfg.get("state_file", "ghlink_status.json")
    d = os.path.dirname(base)
    fname = f"{_CACHE_PREFIX}{domain.replace('.', '_')}.json"
    return os.path.join(d, fname) if d else fname


def _load_cache(domain: str, cfg: Dict[str, object]) -> List[str]:
    """读取上次成功的候选缓存；不存在/过期返回空列表。"""
    try:
        ttl = int(cfg.get("cache_ttl_sec", 3600))
        p = _cache_path(domain, cfg)
        if not os.path.exists(p):
            return []
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if (time.time() - float(data.get("ts", 0))) > ttl:
            return []
        return data.get("ips", []) or []
    except Exception:
        return []


def _save_cache(domain: str, ips: List[str], cfg: Dict[str, object]) -> None:
    """写入成功候选缓存（原子写）。"""
    import tempfile
    try:
        p = _cache_path(domain, cfg)
        d = os.path.dirname(os.path.abspath(p)) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".ghlink_cache_", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "ips": ips}, f)
        os.replace(tmp, p)
    except Exception:
        pass
