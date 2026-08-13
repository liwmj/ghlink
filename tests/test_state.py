"""状态文件读写测试（schema v1）。

对应 src/ghlink/state.py：default_state / load / save。
口径：
- 不存在/损坏 → 回退默认值，不崩溃
- 计数跨运行累计（重启不丢）、成功一轮清零
- 原子写（临时文件 + rename，不留半截文件）
"""
import json

import pytest

from ghlink import state


class TestDefaultState:
    def test_schema_shape(self):
        s = state.default_state()
        assert s["schema_version"] == 1
        assert s["state"] in ("normal", "switching", "verifying", "degraded", "disabled")
        assert s["probe"]["consecutive_failures"] == 0
        assert s["current_ip"] is None
        assert isinstance(s["history"], list)


class TestLoad:
    def test_missing_file_returns_default(self, tmp_path):
        assert state.load(str(tmp_path / "nope.json")) == state.default_state()

    def test_corrupt_file_returns_default(self, tmp_path):
        p = tmp_path / "s.json"
        p.write_text("{not json", encoding="utf-8")
        assert state.load(str(p)) == state.default_state()

    def test_load_roundtrip(self, tmp_path):
        p = tmp_path / "s.json"
        s = state.default_state()
        s["probe"]["consecutive_failures"] = 3
        state.save(str(p), s)
        assert state.load(str(p)) == s


class TestCounter:
    def test_failure_counter_persists_across_runs(self, tmp_path):
        p = tmp_path / "s.json"
        # 第一轮失败
        s = state.load(str(p))
        s["probe"]["consecutive_failures"] += 1
        state.save(str(p), s)
        # 模拟重启：重新 load
        s2 = state.load(str(p))
        assert s2["probe"]["consecutive_failures"] == 1
        s2["probe"]["consecutive_failures"] += 1
        state.save(str(p), s2)
        assert state.load(str(p))["probe"]["consecutive_failures"] == 2

    def test_success_resets_counter(self, tmp_path):
        p = tmp_path / "s.json"
        s = state.default_state()
        s["probe"]["consecutive_failures"] = 3
        state.save(str(p), s)
        s = state.load(str(p))
        s["probe"]["consecutive_failures"] = 0
        state.save(str(p), s)
        assert state.load(str(p))["probe"]["consecutive_failures"] == 0


class TestAtomicWrite:
    def test_no_temp_leftover_after_save(self, tmp_path):
        p = tmp_path / "s.json"
        state.save(str(p), state.default_state())
        leftovers = [f.name for f in tmp_path.iterdir() if f.name != "s.json"]
        assert leftovers == []

    def test_saved_file_is_valid_json(self, tmp_path):
        p = tmp_path / "s.json"
        state.save(str(p), state.default_state())
        with open(p, encoding="utf-8") as f:
            assert json.load(f)["schema_version"] == 1
