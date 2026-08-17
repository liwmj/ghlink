"""配置加载/合并/缺省回退测试。

对应 src/ghlink/config.py：load_config / _deep_merge。
口径：无配置文件 → 全默认；部分覆盖 → 其余回退默认；非法输入 → 明确报错。
"""

import json

import pytest

from ghlink import config


class TestLoadDefaults:
    def test_missing_file_returns_defaults(self, tmp_path):
        cfg = config.load_config(str(tmp_path / "nope.json"))
        # 核心段齐全，值取 config.example.json 的默认
        assert set(["probe", "trigger", "resolver", "notify"]).issubset(cfg)
        assert cfg["trigger"]["consecutive_failures"] == 3
        # v0.2.18（李工 22:27 定：探测 1 小时）：cooldown 15→180min 按 1h 粒度核算
        assert cfg["trigger"]["cooldown_min"] == 180
        assert cfg["probe"]["timeout_sec"] == 15

    def test_alert_default_enabled(self, tmp_path):
        cfg = config.load_config(str(tmp_path / "nope.json"))
        assert cfg["notify"]["enabled"] is True

    def test_partial_override_falls_back(self, empty_config):
        cfg = config.load_config(empty_config)
        # 只覆盖了 cooldown_min，其余必须仍是默认值
        assert cfg["trigger"]["cooldown_min"] == 5
        assert cfg["trigger"]["consecutive_failures"] == 3
        assert cfg["notify"]["enabled"] is True


class TestLoadExample:
    def test_loads_example_values(self, example_config):
        cfg = config.load_config(example_config)
        assert cfg["probe"]["timeout_sec"] == 15
        assert cfg["resolver"]["cache_ttl_sec"] == 3600
        assert cfg["resolver"]["max_candidates"] == 5
        assert cfg["trigger"]["verify_success_rounds"] == 2

    def test_alert_toggle_honored(self, tmp_path):
        p = tmp_path / "c.json"
        p.write_text(json.dumps({"notify": {"enabled": False}}), encoding="utf-8")
        cfg = config.load_config(str(p))
        assert cfg["notify"]["enabled"] is False


class TestInvalidInput:
    def test_invalid_json_raises_clear_error(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{ not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            config.load_config(str(p))
