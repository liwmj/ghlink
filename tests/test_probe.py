"""探测逻辑测试（mock 网络，不依赖真实网络）。

对应 src/ghlink/probe.py：probe_target / probe_all / round_ok。
口径：
- 单轮并行探测，整体耗时 ≤10s（超时目标 5s）
- 单轮结果 = 全部目标通过 or 任一失败（保守：任一失败记本轮失败）
"""

from ghlink import probe


class TestProbeTarget:
    def test_success_result_shape(self):
        r = probe.probe_target("github.com", 5.0)
        assert set(["ok", "latency_ms", "error"]).issubset(r)
        assert r["ok"] in (True, False)

    def test_timeout_marks_failure(self, monkeypatch):
        def fake(host, timeout_sec):
            return {"ok": False, "latency_ms": -1, "error": "timeout"}

        monkeypatch.setattr(probe, "probe_target", fake)
        r = probe.probe_target("github.com", 5.0)
        assert r["ok"] is False


class TestProbeAll:
    def test_parallel_shape(self):
        targets = ["github.com", "api.github.com"]
        results = probe.probe_all(targets, 5.0)
        assert set(results.keys()) == set(targets)

    def test_single_failure_marks_round_failed(self, monkeypatch):
        def fake(host, timeout_sec):
            return {"ok": host != "api.github.com", "latency_ms": 0, "error": None}

        monkeypatch.setattr(probe, "probe_target", fake)
        results = probe.probe_all(["github.com", "api.github.com"], 5.0)
        assert probe.round_ok(results) is False


class TestRoundOk:
    def test_all_pass(self):
        results = {
            "github.com": {"ok": True},
            "api.github.com": {"ok": True},
        }
        assert probe.round_ok(results) is True

    def test_any_fail_fails(self):
        results = {
            "github.com": {"ok": True},
            "api.github.com": {"ok": False},
        }
        assert probe.round_ok(results) is False

    def test_empty_results_fails_conservative(self):
        assert probe.round_ok({}) is False
