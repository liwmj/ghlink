# PyInstaller spec: ghlink macOS（v0.5.12 内嵌运行时，V5）
# 构建: /tmp/ghlink-pyi-venv/bin/pyinstaller packaging/macos/ghlink.spec --distpath dist/macos-pyi --workpath build/macos-pyi
# 目标：dmg 内嵌 python 运行时（onedir），DMG 用户零依赖——不需要 brew python@3.14
# 参考: windows spec（薄入口避免相对导入问题）+ tray_macos.py（PyObjC 原生渲染）
#
# 产物：
#   dist/macos-pyi/ghlink/ghlink          —— CLI 入口（ghlink --version/status/enable/...）
#   dist/macos-pyi/ghlink-tray/ghlink-tray —— 托盘入口（windowed，双击/LaunchAgent 用）

import sys
from pathlib import Path

# SPECPATH = packaging/macos/，仓库根 = 上两级
ROOT = Path(SPECPATH).resolve().parent.parent
SRC = ROOT / "src"

# ---------- CLI 入口 ----------
a = Analysis(
    [str(ROOT / "packaging" / "macos" / "scripts" / "ghlink_entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[
        (str(ROOT / "config.example.json"), "."),
        (str(ROOT / "assets" / "ghlink-icon.png"), "assets"),
    ],
    # v0.5.12（ARM 托盘根因）：PyObjC 框架必须显式 hiddenimports——
    # tray_macos.py 动态 import AppKit/Foundation/Quartz，PyInstaller 静态分析抓不全
    # v0.5.12（李工 17:39 真机实锤）：CLI 入口也必须含 ghlink.tray_macos——
    # 否则 `ghlink tray` CLI 形态启动时 from . import tray_macos 失败 → 回退
    # pystray → ARM 不渲染 → 托盘不显示（与 ghlink-tray 入口对齐）
    hiddenimports=[
        "pystray",
        "PIL",
        "objc",
        "AppKit",
        "Foundation",
        "Quartz",
        "Cocoa",
        "ghlink.tray_macos",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "tests"],
    noarchive=False,
)

# ---------- 托盘入口（windowed，无控制台） ----------
a_tray = Analysis(
    [str(ROOT / "packaging" / "macos" / "scripts" / "ghlink_tray_entry.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=[
        (str(ROOT / "config.example.json"), "."),
        (str(ROOT / "assets" / "ghlink-icon.png"), "assets"),
    ],
    hiddenimports=[
        "pystray",
        "PIL",
        "objc",
        "AppKit",
        "Foundation",
        "Quartz",
        "Cocoa",
        "ghlink.tray_macos",
    ],
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
    console=True,  # CLI 保留控制台（status/日志可见）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

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
    console=False,  # 托盘 windowed：无终端窗口（菜单栏常驻）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
