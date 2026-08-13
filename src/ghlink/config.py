"""配置加载：JSON 配置，零依赖。

config.example.json 为模板。字段：
- probe: targets(探测域名列表), timeout_sec, round_interval_min
- trigger: consecutive_failures(默认3), cooldown_min(默认15), verify_success_rounds(默认2)
- resolver: doh_sources, cache_ttl_sec, max_candidates
- notify: feishu_webhook(空=关闭), enabled(默认true)
- state_file, lock_file, hosts_backup_dir
"""
import json
import os
from typing import Any, Dict, List


DEFAULT_CONFIG: Dict[str, Any] = {
    "probe": {
        "targets": [
            "github.com",
            "api.github.com",
            "codeload.github.com",
            "github.global.ssl.fastly.net",
        ],
        "timeout_sec": 5,
    },
    "trigger": {
        "consecutive_failures": 3,
        "cooldown_min": 15,
        "verify_success_rounds": 2,
    },
    "resolver": {
        "doh_sources": [
            "https://dns.alidns.com/resolve",
            "https://doh.pub/dns-query",
            "https://cloudflare-dns.com/dns-query",
            "https://dns.google/resolve",
        ],
        "cache_ttl_sec": 3600,
        "max_candidates": 5,
    },
    "notify": {"enabled": True, "feishu_webhook": ""},
    "state_file": "ghlink_status.json",
    "lock_file": "ghlink.lock",
    "hosts_backup_dir": "backup",
}


def load_config(path: str) -> Dict[str, Any]:
    """加载配置，缺失字段回退默认值。"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    if path and os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        _deep_merge(cfg, user_cfg)
    return cfg


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> None:
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
