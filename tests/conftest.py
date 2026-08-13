"""ghlink 测试公共设施：把 src/ 加入 import 路径，提供共享 fixture。"""
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


@pytest.fixture
def example_config(tmp_path):
    """从仓库 config.example.json 拷贝一份可写副本。"""
    src = Path(__file__).resolve().parents[1] / "config.example.json"
    dst = tmp_path / "config.json"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return str(dst)


@pytest.fixture
def empty_config(tmp_path):
    """只含部分字段的配置，用于验证缺省回退。"""
    p = tmp_path / "partial.json"
    p.write_text('{"trigger": {"cooldown_min": 5}}', encoding="utf-8")
    return str(p)
