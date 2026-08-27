"""PyInstaller 薄入口（macOS 托盘）：解决相对导入问题（tray.py 用 from . import）。"""

import sys

from ghlink import tray

if __name__ == "__main__":
    sys.exit(tray.main())
