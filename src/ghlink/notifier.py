"""飞书 Webhook 告警。

约束（产品口径）：
- 发送失败只记日志，绝不阻断主流程（try/except 全包）
- 冷却期去重：切换防抖期间不重复发（状态文件 last_alert_at 控制）
- 标准库 urllib 实现
"""
from typing import Optional


def send(message: str, webhook_url: str) -> bool:
    """发送飞书消息；失败返回 False（调用方仅记日志）。"""
    # TODO(顾笙): urllib POST interactive/text 卡片
    # TODO(顾笙): 失败/超时静默返回 False
    return True
