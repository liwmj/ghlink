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


import glob

# v0.5.12（李工 18:14 真机实锤「修复版仍无托盘」真根因）：PyInstaller 6.22.2
# 在 CI 的 python.org framework Python 上隔离模式 hook 收集 PyObjC 动态库静默
# 失败 → 产物缺 AppKit/objc/Foundation/Quartz 的 .so → HAS_NATIVE=False → 回退
# pystray → ARM 原生不渲染 → 无托盘（本机 bincache 缓存兜底掩盖了 hook 失效）。
# 修复：不依赖 hook，glob site-packages 下 PyObjC 核心包的全部 .so 显式进
# binaries（排除 PIL——PIL 由 PyInstaller 自带 hook-PIL 收集，避免 bincache 冲突）。
def _pyobjc_binaries():
    bins = []
    try:
        import objc  # noqa: F401
        sp = Path(objc.__file__).resolve().parent.parent  # site-packages 根
        for pkg in ("objc", "AppKit", "Foundation", "Quartz", "CoreFoundation"):
            base = sp / pkg
            for so in sorted(glob.glob(str(base / "**" / "*.so"), recursive=True)):
                if ".dSYM" in so:
                    continue
                # PyInstaller binaries 的 dest 是「目标目录」（相对 app 根），
                # 不是完整文件路径——写完整路径会被当目录创建导致同名冲突
                # （IsADirectoryError，6.22.2 bincache 实测踩坑）
                rel_dir = str(Path(so).parent.relative_to(sp))
                bins.append((so, rel_dir))
        print(f"PyObjC binaries collected: {len(bins)}")
    except Exception as e:
        print(f"WARN: pyobjc binaries collect failed: {e}")
    return bins

PYOBJC_BINARIES = _pyobjc_binaries()

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
    noarchive=True,
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
    noarchive=True,
)

pyz = PYZ(a.pure)
pyz_tray = PYZ(a_tray.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ghlink",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # v0.5.12（李工 22:11 traceback 实锤）：upx=True 压缩 arm64 onefile 归档
    # 导致 zlib.error: Error -3 (incorrect header check)——构建"成功"但运行必崩
    # （连 main.py 都进不去）。必须禁用 UPX，PyObjC/PIL 均已显式收集无需压缩。
    upx=False,
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
    [],
    exclude_binaries=True,
    name="ghlink-tray",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # v0.5.12（李工 22:11 traceback 实锤）：upx=True 压缩 arm64 onefile 归档
    # 导致 zlib.error: Error -3 (incorrect header check)——构建"成功"但运行必崩
    # （连 main.py 都进不去）。必须禁用 UPX，PyObjC/PIL 均已显式收集无需压缩。
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 托盘 windowed：无终端窗口（菜单栏常驻）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)


# v0.5.13（onedir 改造）：onefile 式 EXE 带 binaries/datas -> 大 CArchive ->
# zlib.error Error -3（PyInstaller 6.22.2 + CI 环境必现，wrapper 不走归档正常）。
# 转 onedir：EXE 只留 bootloader（exclude_binaries=True）+ COLLECT 合并依赖，
# 产物 dist/macos-pyi/ghlink/{ghlink, ghlink-tray, _internal/}，绕开 CArchive。
coll = COLLECT(
    exe,
    exe_tray,
    a.binaries,
    a_tray.binaries,
    a.datas,
    a_tray.datas,
    name="ghlink",
)
