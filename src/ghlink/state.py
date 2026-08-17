"""ghlink_status.json 读写（schema v1，见方案草案第 7 节）。

字段严禁私自改，改 schema 必须走评审。读写均在防重入锁内完成。
"""

import json
import os
import tempfile
import time
from typing import Any, Dict


def default_state() -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "timestamp": "",
        "state": "normal",  # normal|switching|verifying|degraded|disabled
        "probe": {"targets": {}, "consecutive_failures": 0},
        # v0.2.8：resolver 源级健康记录 {source_url: {ok, last_error, last_ok_at}}
        "source_health": {},
        "current_ip": None,
        "history": [],
        "last_error": None,
    }


def load(path: str) -> Dict[str, Any]:
    """读取状态文件；不存在或损坏回退默认值。"""
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return default_state()


def save(path: str, state: Dict[str, Any]) -> None:
    """原子写：先写临时文件再 rename，避免半截文件。

    2026-08-17 v0.2.12 复测发现：值守进程以 root 写入 /var/lib/ghlink/ 后
    状态文件为 600，普通用户 ghlink status 读不到 → 心跳恒判不新鲜。
    写完后 chmod 644，保证 CLI 可读。
    """
    state["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(os.path.abspath(path)) or ".",
        prefix=".ghlink_state_",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        # NOSONAR:S2612 有意设计：root 值守进程写入后普通用户 CLI 需可读状态文件
        # （0644 仅读不可写，非 world-writable）
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
