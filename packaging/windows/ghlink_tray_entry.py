"""ghlink 托盘专用入口（windowed，无控制台窗口）。

由 ghlink-tray.exe（console=False）使用：直接进托盘，不显示命令行窗口。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from ghlink import tray  # noqa: E402

if __name__ == "__main__":
    sys.exit(tray.main())
