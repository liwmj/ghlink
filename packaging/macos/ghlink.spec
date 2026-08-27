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
import glob

# SPECPATH = packaging/macos/，仓库根 = 上两级
ROOT = Path(SPECPATH).resolve().parent.parent
SRC = ROOT / "src"

# v0.5.12（李工 18:14「修复版仍无托盘」真根因实锤）：CI 的 python.org framework
# Python 上 PyInstaller 隔离模式 hook 收集 PyObjC 动态库静默失败（本机 universal2
# 尝试同款 exit -6 SIGABRT）→ 产物缺 AppKit/objc/Foundation/Quartz 的 .so →
# HAS_NATIVE=False → 回退 pystray → ARM 原生不渲染 → 无托盘。
# 修复：不依赖 hook，直接 glob site-packages 下 PyObjC 相关包的全部 .so 显式进 binaries。
def _pyobjc_binaries():
    bins = []
    try:
        import objc  # noqa: F401
        sp = Path(objc.__file__).resolve().parent.parent  # site-packages 根
        # v0.5.13 修复：PIL 不属于 PyObjC——显式收集 PIL .so 会与 PyInstaller 自带
        # PIL hook 双收集冲突（bincache 目录/文件竞争 → IsADirectoryError）。
        # PIL 二进制由 hook 正常收集，这里只显式收集 PyObjC 5 包的 .so。
        for pkg in ("objc", "AppKit", "Foundation", "Quartz", "CoreFoundation"):
            base = sp / pkg
            for so in sorted(glob.glob(str(base / "**" / "*.so"), recursive=True)):
                if ".dSYM" in so:
                    continue
                rel = str(Path(so).relative_to(sp))
                bins.append((so, rel))
        print(f"PyObjC binaries collected: {len(bins)}")
    except Exception as e:
        print(f"WARN: pyobjc binaries collect failed: {e}")
    return bins

# v0.5.13（拂晓 21:1x 二次实锤）：手动 glob 显式收集嵌套路径 .so（如
        # Quartz/QuickLookUI/_QuickLookUI...so）会触发 PyInstaller bincache 冲突
        # （IsADirectoryError：缓存位置被目录占用）——单 spec 双 EXE 共享 bincache 时
        # 确定性复现（runner 全新也中招）。改回走 PyInstaller 自带 hook：非隔离模式
        # （build.yml PYINSTALLER_DISABLE_ISOLATED=1）下 hook 收集正常，
        # v0.5.12 缺 .so 真因是当时 pyobjc 安装被 --quiet 吞错（已修：去 --quiet + 验证 + set -e）。
PYOBJC_BINARIES = []  # 显式收集停用，PyObjC .so 由 hook 收集

# ---------- CLI 入口 ----------
a = Analysis(
    [str(ROOT / "packaging" / "macos" / "scripts" / "ghlink_entry.py")],
    pathex=[str(SRC)],
    binaries=PYOBJC_BINARIES,
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
    binaries=PYOBJC_BINARIES,
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
