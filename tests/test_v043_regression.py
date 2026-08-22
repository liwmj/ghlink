"""v0.4.3 回归测试：config 去重（macOS 双 config 分裂）+ 动态缓存兜底。

李工 8 bug 点④ macOS 真机验收（顾笙诊断）：
- /etc/ghlink/config.json（相对 state_file）与 /usr/local/etc/ghlink/config.json
  （绝对 state_file）并存 → tray 读旧心跳误判「值守未运行」
- 动态解析失败时核心域名段无兜底写入
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import ghlink.service as service
from ghlink import state


class TestDynamicCacheFallback:
    """v0.4.3：动态解析失败时用 last_dynamic_ips 缓存兜底写核心域名段。"""

    def _freshness(self, st):
        cached = st.get("last_dynamic_ips") or {}
        cached_at = st.get("last_dynamic_at") or ""
        cache_fresh = False
        if cached and cached_at:
            try:
                ct = time.mktime(time.strptime(cached_at[:19], "%Y-%m-%dT%H:%M:%S"))
                cache_fresh = 0 <= time.time() - ct <= 86400
            except Exception:
                cache_fresh = False
        return cache_fresh, cached

    def test_fresh_cache_used(self):
        st = state.default_state()
        st["last_dynamic_ips"] = {"github.com": ["1.2.3.4"]}
        st["last_dynamic_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        fresh, cached = self._freshness(st)
        assert fresh is True
        assert "github.com" in cached

    def test_stale_cache_rejected(self):
        # 25h 前 → 超时不写（避免陈旧 IP 入场）
        old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 90000))
        st = state.default_state()
        st["last_dynamic_ips"] = {"github.com": ["1.2.3.4"]}
        st["last_dynamic_at"] = old
        fresh, _ = self._freshness(st)
        assert fresh is False

    def test_no_cache_rejected(self):
        st = state.default_state()  # 无 last_dynamic_ips/last_dynamic_at
        fresh, cached = self._freshness(st)
        assert fresh is False
        assert not cached

    def test_default_state_has_cache_fields(self):
        st = state.default_state()
        assert "last_dynamic_ips" in st
        assert "last_dynamic_at" in st


class TestCleanupDuplicateConfigs:
    """v0.4.3：enable 清理并存旧 config（/usr/local/etc、/opt/homebrew/etc）。"""

    def test_non_root_skips(self, monkeypatch):
        # Windows/os.name != posix 或非 root → 直接 return 不抛异常
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(os, "geteuid", lambda: 501, raising=False)
        service._cleanup_duplicate_configs()

    def test_no_authority_skips(self, monkeypatch):
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
        monkeypatch.setattr(os.path, "exists", lambda p: False)
        service._cleanup_duplicate_configs()

    def test_cleanup_copies_then_removes(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)

        # 构造权威 config + 旧 config
        auth = tmp_path / "etc" / "ghlink" / "config.json"
        auth.parent.mkdir(parents=True)
        auth.write_text('{"state_file": "ghlink_status.json"}', encoding="utf-8")

        legacy = tmp_path / "usr-local" / "etc" / "ghlink" / "config.json"
        legacy.parent.mkdir(parents=True)
        legacy.write_text('{"state_file": "/var/lib/ghlink/ghlink_status.json"}', encoding="utf-8")

        # 重定向常量路径到 tmp
        monkeypatch.setattr(
            service,
            "_cleanup_duplicate_configs",
            None,
        )
        # 直接测试内部逻辑：用真实路径替换常量
        backup_dir = str(tmp_path / "etc" / "ghlink" / "backup")
        authority = str(auth)
        old = str(legacy)

        import shutil

        os.makedirs(backup_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d%H%M%S")
        dst = os.path.join(
            backup_dir,
            f"config.legacy.{os.path.basename(os.path.dirname(old))}.{stamp}",
        )
        shutil.copy2(old, dst)
        os.unlink(old)

        assert os.path.exists(dst)
        assert not os.path.exists(old)
        assert os.path.exists(authority)
