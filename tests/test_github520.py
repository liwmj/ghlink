"""GitHub520 hosts 段集成测试（v0.2.18）。"""

from ghlink import github520 as g520


def test_parse_hosts_basic():
    """标准 hosts 文本 → 结构化 entries（仅收 GitHub 生态域名）。"""
    text = """# GitHub520 Hosts Start
140.82.112.3 github.com
185.199.108.133 raw.githubusercontent.com
1.2.3.4 example.com
# comment
"""
    entries = g520.parse_hosts(text)
    assert "github.com" in entries
    assert "raw.githubusercontent.com" in entries
    assert "example.com" not in entries  # 非 GitHub 生态域名剔除
    assert entries["github.com"] == ["140.82.112.3"]


def test_parse_hosts_multi_ip_dedup():
    """同域名多 IP 收集 + 去重。"""
    text = """140.82.112.3 github.com
140.82.113.4 github.com
140.82.112.3 github.com
"""
    entries = g520.parse_hosts(text)
    assert entries["github.com"] == ["140.82.112.3", "140.82.113.4"]


def test_parse_hosts_skip_bad_lines():
    """坏行/空行/单列行跳过。"""
    entries = g520.parse_hosts("  \nnot-a-valid-line\n140.82.112.3\n# x\n")
    assert entries == {}


def test_cache_roundtrip(tmp_path):
    """缓存写读往返。"""
    entries = {"raw.githubusercontent.com": ["185.199.108.133"]}
    g520.save_cache(entries, str(tmp_path))
    loaded = g520.load_cached({}, str(tmp_path))
    assert loaded == entries


def test_sync_core_excluded(monkeypatch, tmp_path):
    """核心域名（github.com/api.github.com）不写死社区 IP——自愈优先。"""
    cfg = {
        "github520": {"enabled": True, "url": "http://x/hosts", "timeout_sec": 5},
        "probe": {"core_targets": ["github.com", "api.github.com"]},
    }
    text = """140.82.112.3 github.com
185.199.108.133 raw.githubusercontent.com
"""

    def _fake_fetch(url, timeout_sec):
        return text

    def _fake_reachable(ip, domain, timeout_sec=5):
        return True

    monkeypatch.setattr(g520, "fetch_hosts", _fake_fetch)
    monkeypatch.setattr(g520, "_ip_reachable", _fake_reachable)
    result = g520.sync_github520(cfg, str(tmp_path))
    assert "github.com" not in result  # 核心域名排除
    assert "raw.githubusercontent.com" in result


def test_sync_fetch_fail_fallback_cache(monkeypatch, tmp_path):
    """拉取失败 → 缓存兜底。"""
    cfg = {
        "github520": {"enabled": True, "url": "http://x/hosts", "timeout_sec": 5},
        "probe": {"core_targets": ["github.com"]},
    }
    # 预置缓存（非核心域名）
    g520.save_cache({"raw.githubusercontent.com": ["185.199.108.133"]}, str(tmp_path))

    def _fake_fetch(url, timeout_sec):
        raise OSError("network down")

    monkeypatch.setattr(g520, "fetch_hosts", _fake_fetch)
    result = g520.sync_github520(cfg, str(tmp_path))
    assert result.get("raw.githubusercontent.com") == ["185.199.108.133"]


def test_disabled_returns_empty():
    """github520.enabled=false → 不集成。"""
    cfg = {"github520": {"enabled": False}}
    assert g520.sync_github520(cfg, "") == {}
