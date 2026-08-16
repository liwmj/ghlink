"""ghlink 值守专用入口（windowed，无控制台窗口）。

由 ghlink-watch.exe（console=False）使用：静默跑单轮探测+自愈（供 schtasks 值守），
不弹命令行窗口（李工 13:34 反馈：值守弹窗反复）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from ghlink import main as ghlink_main  # noqa: E402

if __name__ == "__main__":
    cfg = sys.argv[1] if len(sys.argv) > 1 else "config.json"
    sys.exit(ghlink_main.run(cfg))
