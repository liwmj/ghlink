"""监控探测：单轮内对多域名做 TCP 443 + TLS 握手 + HTTP HEAD 三层校验。

设计约束：
- 单轮并行探测，整体耗时 ≤10s（超时目标 5s）
- 单轮结果 = 全部目标通过 or 任一失败（保守：任一失败记本轮失败）
- 纯标准库 socket/ssl/urllib，可 mock（测试不依赖真实网络）
"""
from typing import Dict, List


def probe_target(host: str, timeout_sec: float) -> Dict[str, object]:
    """对单域名探测，返回 {"ok": bool, "latency_ms": int, "error": str|None}。"""
    # TODO(顾笙): TCP 443 connect → TLS wrap(SNI=host) → HTTP HEAD 200
    # 任一层失败即 ok=False；latency 取 TCP 建连耗时
    return {"ok": True, "latency_ms": 0, "error": None}


def probe_all(targets: List[str], timeout_sec: float) -> Dict[str, Dict[str, object]]:
    """并行探测全部目标，返回 {host: 结果}。单轮成功 = 全部 ok。"""
    results: Dict[str, Dict[str, object]] = {}
    for host in targets:
        results[host] = probe_target(host, timeout_sec)
    return results


def round_ok(results: Dict[str, Dict[str, object]]) -> bool:
    """本轮是否全部通过。"""
    return all(r.get("ok") for r in results.values())
