# PyInstaller spec: ghlink Windows exe（v0.2.0）
# 构建: pyinstaller packaging/windows/ghlink.spec --distpath dist/windows --workpath build/windows
# 参考: v0.2 安装包技术方案草案（exe 线：PyInstaller + Inno Setup 安装向导）
#
# 修复记录（赛博实测反馈 2026-08-13）：
# 1) 相对导入问题：main.py 用 `from . import`，需薄入口脚本 ghlink_entry.py
# 2) script 路径：相对 spec 目录解析失败 → 用 SPECPATH 计算仓库根
# 3) datas 路径：同样相对解析失败 → 绝对路径

import sys
from pathlib import Path

# SPECPATH = packaging/windows/，仓库根 = 上两级
ROOT = Path(SPECPATH).resolve().parent.parent
SRC = ROOT / "src"

a = Analysis(
    [str(ROOT / "packaging" / "windows" / "ghlink_entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[
        (str(ROOT / "config.example.json"), "."),
    ],
    hiddenimports=["pystray", "PIL", "PIL._tkinter_finder"],  # 托盘依赖（仅安装包注入，核心零依赖）
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ghlink",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # CLI 工具保留控制台（status/日志可见）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,             # v0.2 可加 assets/ghlink.ico
)
