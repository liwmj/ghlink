"""PyInstaller 薄入口：解决相对导入问题（main.py 用 from . import）。"""

import sys

from ghlink.main import main

if __name__ == "__main__":
    sys.exit(main())
