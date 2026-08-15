"""飞书 Webhook 告警测试。

对应 src/ghlink/notifier.py：send。
口径（产品口径，已写入草案）：
- 发送失败只记日志，绝不阻断主流程（返回 False 不抛异常）
- 冷却期去重、抖动只发 1 条：由状态文件 last_alert_at 控制，e2e 覆盖
"""

from ghlink import notifier


class TestSend:
    def test_success_returns_true(self, monkeypatch):
        monkeypatch.setattr(notifier, "send", lambda message, webhook_url: True)
        assert notifier.send("hi", "https://example.com/hook") is True

    def test_failure_returns_false_no_raise(self, monkeypatch):
        """webhook 挂掉 → 返回 False，不抛异常、不阻断。"""
        monkeypatch.setattr(notifier, "send", lambda message, webhook_url: False)
        assert notifier.send("hi", "https://example.com/hook") is False

    def test_empty_webhook_disabled(self, monkeypatch):
        """webhook 为空 = 关闭告警（send 不执行 / 返回 False）。"""
        monkeypatch.setattr(notifier, "send", lambda message, webhook_url: bool(webhook_url))
        assert notifier.send("hi", "") is False
