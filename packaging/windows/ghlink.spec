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
        (str(ROOT / "assets" / "ghlink-icon.png"), "assets"),
    ],
    hiddenimports=["pystray", "PIL"],  # 托盘依赖（仅安装包注入，核心零依赖；Pillow 其余由 PyInstaller hook 收集）
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests"],
    noarchive=False,
)

# 托盘专用 Analysis（windowed 入口，复用同一份依赖收集）
a_tray = Analysis(
    [str(ROOT / "packaging" / "windows" / "ghlink_tray_entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[
        (str(ROOT / "config.example.json"), "."),
        (str(ROOT / "assets" / "ghlink-icon.png"), "assets"),
    ],
    hiddenimports=["pystray", "PIL"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests"],
    noarchive=False,
)

pyz = PYZ(a.pure)
pyz_tray = PYZ(a_tray.pure)

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
    icon=str(ROOT / "assets" / "ghlink-icon.ico"),
)

# 值守专用 exe：windowed 无控制台窗口（李工 13:34 反馈：schtasks 弹窗反复）
a_watch = Analysis(
    [str(ROOT / "packaging" / "windows" / "ghlink_watch_entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[
        (str(ROOT / "config.example.json"), "."),
        (str(ROOT / "assets" / "ghlink-icon.png"), "assets"),
    ],
    hiddenimports=["pystray", "PIL"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests"],
    noarchive=False,
)

pyz_watch = PYZ(a_watch.pure)

exe_watch = EXE(
    pyz_watch,
    a_watch.scripts,
    a_watch.binaries,
    a_watch.datas,
    [],
    name="ghlink-watch",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,         # windowed：值守静默运行不弹窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "ghlink-icon.ico"),
)

# 托盘专用 exe：windowed 无控制台窗口（李工 12:52 需求：托盘常驻不弹命令行）
exe_tray = EXE(
    pyz_tray,
    a_tray.scripts,
    a_tray.binaries,
    a_tray.datas,
    [],
    name="ghlink-tray",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,         # windowed：无命令行窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "ghlink-icon.ico"),
)
