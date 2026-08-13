"""监控探测：单轮内对多域名做 TCP 443 + TLS 握手 + HTTP HEAD 三层校验。

设计约束：
- 单轮并行探测，整体耗时 ≤10s（超时目标 5s）
- 单轮结果 = 全部目标通过 or 任一失败（保守：任一失败记本轮失败）
- 纯标准库 socket/ssl/urllib，可 mock（测试不依赖真实网络）
"""
import socket
import ssl
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List


def probe_target(host: str, timeout_sec: float) -> Dict[str, object]:
    """对单域名探测，返回 {"ok": bool, "latency_ms": int, "error": str|None}。

    三层校验：TCP 443 connect → TLS wrap(SNI=host) → HTTP HEAD 200。
    任一层失败即 ok=False；latency 取 TCP 建连耗时。
    """
    start = time.monotonic()
    try:
        # 1) TCP 443 建连
        sock = socket.create_connection((host, 443), timeout=timeout_sec)
        tcp_ms = int((time.monotonic() - start) * 1000)
        try:
            # 2) TLS 握手（SNI）
            ctx = ssl.create_default_context()
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                # 3) HTTP HEAD，接受 2xx/3xx
                req = urllib.request.Request(
                    f"https://{host}/",
                    method="HEAD",
                    headers={"User-Agent": "ghlink/0.1"},
                )
                with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                    ok = 200 <= resp.status < 400
                    return {"ok": ok, "latency_ms": tcp_ms, "error": None if ok else f"HTTP {resp.status}"}
        finally:
            try:
                sock.close()
            except OSError:
                pass
    except Exception as exc:  # 连接/超时/TLS/HTTP 任一失败
        ms = int((time.monotonic() - start) * 1000)
        return {"ok": False, "latency_ms": ms, "error": str(exc)[:120]}


def probe_all(targets: List[str], timeout_sec: float) -> Dict[str, Dict[str, object]]:
    """并行探测全部目标，返回 {host: 结果}。单轮成功 = 全部 ok。"""
    results: Dict[str, Dict[str, object]] = {}
    with ThreadPoolExecutor(max_workers=len(targets) or 1) as pool:
        futures = {pool.submit(probe_target, h, timeout_sec): h for h in targets}
        for fut in futures:
            host = futures[fut]
            try:
                results[host] = fut.result()
            except Exception as exc:
                results[host] = {"ok": False, "latency_ms": 0, "error": str(exc)[:120]}
    return results


def round_ok(results: Dict[str, Dict[str, object]]) -> bool:
    """本轮是否全部通过。"""
    return all(r.get("ok") for r in results.values())
