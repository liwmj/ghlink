"""ghlink_status.json 读写（schema v1，见方案草案第 7 节）。

字段严禁私自改，改 schema 必须走评审。读写均在防重入锁内完成。
"""
import json
import os
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
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default_state()
    return default_state()


def save(path: str, state: Dict[str, Any]) -> None:
    """原子写：先写临时文件再 rename，避免半截文件。"""
    # TODO(顾笙)
    pass
