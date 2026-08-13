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
        "current_ip": None,
        "history": [],
        "last_error": None,
    }


def load(path: str) -> Dict[str, Any]:
    """读取状态文件；不存在或损坏回退默认值。"""
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return default_state()


def save(path: str, state: Dict[str, Any]) -> None:
    """原子写：先写临时文件再 rename，避免半截文件。"""
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
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
