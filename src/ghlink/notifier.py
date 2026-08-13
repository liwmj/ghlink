"""飞书 Webhook 告警。

约束（产品口径）：
- 发送失败只记日志，绝不阻断主流程（try/except 全包）
- 冷却期去重：切换防抖期间不重复发（状态文件 last_alert_at 控制）
- 标准库 urllib 实现
"""
import json
import sys
import time
import urllib.request
from typing import Optional


def send(message: str, webhook_url: str) -> bool:
    """发送飞书消息；失败返回 False（调用方仅记日志）。"""
    if not webhook_url:
        return False
    try:
        payload = json.dumps({"msg_type": "text", "content": {"text": message}}).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
            return data.get("code") == 0
    except Exception as exc:
        print(f"[ghlink] notify failed: {exc}", file=sys.stderr)
        return False


def should_alert(state: dict, cooldown_sec: int) -> bool:
    """冷却期去重：距上次告警超过 cooldown_sec 才允许再次告警。"""
    last = state.get("last_alert_at")
    if not last:
        return True
    try:
        return (time.time() - float(last)) >= cooldown_sec
    except (TypeError, ValueError):
        return True


def mark_alerted(state: dict) -> None:
    """记录本次告警时间。"""
    state["last_alert_at"] = time.time()
