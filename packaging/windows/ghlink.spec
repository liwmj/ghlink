# PyInstaller spec: ghlink Windows exe（v0.2.0）
# 构建: pyinstaller packaging/windows/ghlink.spec
# 参考: v0.2 安装包技术方案草案（exe 线：PyInstaller + Inno Setup 安装向导）

a = Analysis(
    ["src/ghlink/main.py"],
    pathex=["src"],
    binaries=[],
    datas=[
        ("config.example.json", "."),
    ],
    hiddenimports=[],
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
