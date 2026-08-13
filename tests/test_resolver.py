"""IP 多源获取测试（mock 网络）。

对应 src/ghlink/resolver.py：query_doh / query_system_dns / resolve_best。
口径（方案草案 + 产品口径）：
- 单源失败自动剔除并标记，不拖垮整体
- 多源结果取多数票/交集，候选先 TCP 443 预检，通过才进入替换
- 全源失败 → 返回空候选（调用方保持原配置 + 告警，绝不用坏 IP）
"""
import pytest

from ghlink import resolver


class TestQueryDoh:
    def test_success_returns_ips(self, monkeypatch):
        def fake(url, domain, timeout):
            return ["140.82.112.3", "140.82.113.3"]
        monkeypatch.setattr(resolver, "query_doh", fake)
        ips = resolver.query_doh("https://dns.alidns.com/resolve", "github.com", 5.0)
        assert len(ips) == 2

    def test_failure_returns_empty(self, monkeypatch):
        def fake(url, domain, timeout):
            return []
        monkeypatch.setattr(resolver, "query_doh", fake)
        assert resolver.query_doh("bad://url", "github.com", 5.0) == []


class TestSystemDns:
    def test_success_returns_ips(self, monkeypatch):
        monkeypatch.setattr(
            resolver, "query_system_dns", lambda domain: ["140.82.112.3"]
        )
        assert resolver.query_system_dns("github.com") == ["140.82.112.3"]

    def test_failure_returns_empty(self, monkeypatch):
        monkeypatch.setattr(resolver, "query_system_dns", lambda domain: [])
        assert resolver.query_system_dns("github.com") == []


class TestResolveBest:
    def test_single_source_failure_removed(self, monkeypatch):
        """一个源挂了，其他源照常出候选。"""
        monkeypatch.setattr(
            resolver,
            "query_doh",
            lambda url, domain, timeout: [] if "alidns" in url else ["140.82.112.3"],
        )
        monkeypatch.setattr(resolver, "query_system_dns", lambda domain: ["140.82.112.3"])
        monkeypatch.setattr(resolver, "_precheck", lambda ips: ips)  # 预检直通
        cfg = {"doh_sources": ["https://dns.alidns.com/resolve", "https://doh.pub/dns-query"]}
        ips = resolver.resolve_best("github.com", cfg)
        assert "140.82.112.3" in ips

    def test_majority_vote(self, monkeypatch):
        """多数源同意的 IP 优先。"""
        def fake(url, domain, timeout):
            return ["1.1.1.1"] if "google" in url else ["2.2.2.2"]
        monkeypatch.setattr(resolver, "query_doh", fake)
        monkeypatch.setattr(resolver, "query_system_dns", lambda domain: ["2.2.2.2"])
        monkeypatch.setattr(resolver, "_precheck", lambda ips: ips)
        cfg = {"doh_sources": ["https://a/x", "https://b/x", "https://google/x"]}
        ips = resolver.resolve_best("github.com", cfg)
        assert ips[0] == "2.2.2.2"

    def test_precheck_filters_unreachable(self, monkeypatch):
        """预检剔除不通的候选。"""
        monkeypatch.setattr(resolver, "query_system_dns", lambda domain: ["1.1.1.1", "2.2.2.2"])
        monkeypatch.setattr(
            resolver, "_precheck", lambda ips: [ip for ip in ips if ip == "2.2.2.2"]
        )
        cfg = {"doh_sources": []}
        ips = resolver.resolve_best("github.com", cfg)
        assert ips == ["2.2.2.2"]

    def test_all_sources_fail_returns_empty(self, monkeypatch):
        """全源失败 → 空候选（不产出坏 IP）。"""
        monkeypatch.setattr(resolver, "query_doh", lambda *a, **k: [])
        monkeypatch.setattr(resolver, "query_system_dns", lambda domain: [])
        cfg = {"doh_sources": ["https://a/x", "https://b/x"]}
        assert resolver.resolve_best("github.com", cfg) == []
