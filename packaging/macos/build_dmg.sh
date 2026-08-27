#!/bin/bash
# ghlink macOS dmg 构建（v0.5.x：dmg+cask 混合方案，李工 14:36 收敛：只拖一个文件）
#
# 设计口径（v0.5.0 李工 13:45 拍板 dmg，拂晓 13:59 定格；v0.5.x 李工 14:36 收敛）：
#   D1 = dmg 管 app（拖入 /Applications 即用，无 postinstall/relocate/收据链）
#   D2 = 系统组件（LaunchDaemon + sudoers + /usr/local/bin/ghlink 软链）app 首启自装
#        （tray 启动检测缺失 → 弹管理员授权一次性安装，之后无感）
#   D3 = LaunchAgent 托盘自启（app 首次启动自动注册，KeepAlive 保活）
#   D4 = 割裂态防护（dmg 内 README 说明 + app 首启自动引导）
#
# 产物：dist/macos/ghlink-<VERSION>.dmg（只含 ghlink.app + README）
#
# 用法: bash packaging/macos/build_dmg.sh
# 依赖: hdiutil（macOS 自带）

set -e

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# v0.4.15：版本号动态化——CI 取 GITHUB_REF_NAME（tag），本地取最近 git tag，可被 VERSION 覆盖
VERSION="${VERSION:-$(git -C "$ROOT" describe --tags --abbrev=0 2>/dev/null | sed 's/^v//')}"
VERSION="${VERSION:-0.0.0}"
echo "==> 构建版本: $VERSION"
STAGE="$ROOT/build/macos-dmg"
OUT="$ROOT/dist/macos"
APP_NAME="ghlink.app"

echo "==> 清理旧构建"
rm -rf "$STAGE" "$OUT"
mkdir -p "$STAGE" "$OUT"

echo "==> 组装 .app（内嵌 CLI + 托盘 + vendor）"
APP="$STAGE/$APP_NAME"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$APP/Contents/libexec/ghlink" "$APP/Contents/libexec/assets" "$APP/Contents/libexec/vendor"
cp "$ROOT"/src/ghlink/*.py "$APP/Contents/libexec/ghlink/"
cp "$ROOT/config.example.json" "$APP/Contents/libexec/"
cp "$ROOT/assets/ghlink-icon.png" "$APP/Contents/libexec/assets/"

# v0.5.12（李工 10:34 拍板）：内嵌 python 运行时（PyInstaller 自包含）——
# DMG 用户零依赖（不需要 brew python@3.14）。USE_PYINSTALLER=1 且产物存在时
# 用 PyInstaller 产物（dist/macos-pyi/ghlink + ghlink-tray）替换 wrapper+vendor。
PYI_MODE=0
if [ "${USE_PYINSTALLER:-0}" = "1" ] && [ -x "$ROOT/dist/macos-pyi/ghlink" ] && [ -x "$ROOT/dist/macos-pyi/ghlink-tray" ]; then
  echo "==> PyInstaller 内嵌模式：复制自包含产物"
  cp "$ROOT/dist/macos-pyi/ghlink" "$APP/Contents/MacOS/ghlink"
  cp "$ROOT/dist/macos-pyi/ghlink-tray" "$APP/Contents/MacOS/ghlink-tray"
  chmod 0755 "$APP/Contents/MacOS/ghlink" "$APP/Contents/MacOS/ghlink-tray"
  PYI_MODE=1
else
  echo "==> 传统模式：wrapper + vendored 依赖"
fi

# vendor 依赖（托盘 pystray + Pillow）注入 .app，核心零依赖（仅传统模式）
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENDOR="$APP/Contents/libexec/vendor"
if [ "$PYI_MODE" = "1" ]; then
  # PyInstaller 模式：无 vendor 需要，占位保持目录结构
  :
else
WHEEL_DIR="$STAGE/wheels"
rm -rf "$WHEEL_DIR"
# 1) 通用依赖（pystray + PyObjC：universal2/纯 wheel，双架构通用）
mkdir -p "$WHEEL_DIR/universal"
"$PYTHON_BIN" -m pip download --only-binary=:all: --no-deps \
  -d "$WHEEL_DIR/universal" pystray six pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-Quartz --quiet || exit 1
for W in "$WHEEL_DIR/universal"/*.whl; do unzip -qo "$W" -d "$WHEEL_DIR/universal/unpacked"; done
cp -R "$WHEEL_DIR/universal/unpacked/." "$VENDOR/"
# 2) Pillow 分架构拉取 → lipo 合并 fat binary（单 dmg 通吃 Intel/Apple Silicon）
# v0.5.13（李工 ARM 托盘根因）：版本必须锁死——不锁时 x86_64/arm64 各拉最新版，
# 版本不一致 → dylib 文件名不同（libavif.16.3.0 vs 16.4.2、libz.1.3.1 vs libz.1.3.1.zlib-ng）
# → lipo 按路径匹配失败 → ARM 缺 dylib → _imaging.so 加载 ImportError → 托盘起不来。
PILLOW_VER="Pillow==11.3.0"
for ARCH in x86_64 arm64; do
  mkdir -p "$WHEEL_DIR/$ARCH"
  PLATFORM="macosx_10_13_${ARCH}"
  [ "$ARCH" = "arm64" ] && PLATFORM="macosx_11_0_${ARCH}"
  "$PYTHON_BIN" -m pip download --only-binary=:all: --no-deps \
    --platform "$PLATFORM" --python-version 3.14 --abi cp314 \
    -d "$WHEEL_DIR/$ARCH" "$PILLOW_VER" --quiet || exit 1
  for W in "$WHEEL_DIR/$ARCH"/*.whl; do unzip -qo "$W" -d "$WHEEL_DIR/$ARCH/unpacked"; done
done
cp -R "$WHEEL_DIR/x86_64/unpacked/." "$VENDOR/"
# v0.4.16：lipo 必须覆盖 .dylib——Pillow 的 @loader_path/.dylibs/*.dylib
# 动态库只合了 x86_64，arm64 机器 dyld 加载 _imaging.so 时找不到 arm64 dylib → ImportError
# v0.5.13 补强：同名 dylib lipo 合并 + arm64 独有 dylib 补拷（libz.1.3.1.zlib-ng 等
# 不同名文件必须直接拷入，否则 fat _imaging.so 的 arm64 slice 引用不到 → 加载失败）
find "$VENDOR" \( -name "*.so" -o -name "*.dylib" \) -type f | while read -r SO; do
  REL="${SO#"$VENDOR"/}"
  ARM_SO="$WHEEL_DIR/arm64/unpacked/$REL"
  if [ -f "$ARM_SO" ]; then
    lipo -create "$SO" "$ARM_SO" -output "$SO.tmp" && mv "$SO.tmp" "$SO"
    echo "    lipo merged: $REL"
  fi
done
# arm64 独有 dylib 补拷（不同名文件：libz.1.3.1.zlib-ng 等）
if [ -d "$WHEEL_DIR/arm64/unpacked/PIL/.dylibs" ]; then
  find "$WHEEL_DIR/arm64/unpacked/PIL/.dylibs" -name "*.dylib" | while read -r ARM_DYLIB; do
    REL="${ARM_DYLIB#"$WHEEL_DIR/arm64/unpacked/"}"
    DEST="$VENDOR/$REL"
    if [ ! -f "$DEST" ]; then
      cp "$ARM_DYLIB" "$DEST"
      echo "    copied arm64-only: $REL"
    fi
  done
fi
rm -rf "$WHEEL_DIR"
fi  # PYI_MODE else 闭合

# CLI 可执行（内嵌 .app；首启自装时软链 /usr/local/bin/ghlink 指向此路径）
# v0.5.12：PyInstaller 模式（PYI_MODE=1）已复制自包含产物，跳过 wrapper
if [ "$PYI_MODE" != "1" ]; then
cat > "$APP/Contents/MacOS/ghlink" <<'EOF'
#!/bin/bash
SELF="$0"
while [ -L "$SELF" ]; do
  LINK="$(readlink "$SELF")"
  case "$LINK" in
    /*) SELF="$LINK" ;;
    *) SELF="$(dirname "$SELF")/$LINK" ;;
  esac
done
APP_DIR="$(cd "$(dirname "$SELF")/.." && pwd)"
export PYTHONPATH="$APP_DIR/libexec:$APP_DIR/libexec/vendor"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# v0.5.13（李工 10:30 发布风险）：vendored 依赖按 cp314 ABI 编译，python 必须 3.14。
# 找不到/版本不符时给明确提示，不再静默报「缺少托盘依赖」假象。
_py() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 14) else 1)' 2>/dev/null
}
for PY in /opt/homebrew/opt/python@3.14/bin/python3.14 /usr/local/opt/python@3.14/bin/python3.14; do
  if [ -x "$PY" ] && _py "$PY"; then
    exec "$PY" -m ghlink.main "$@"
  fi
done
if command -v python3 >/dev/null 2>&1 && _py "$(command -v python3)"; then
  exec "$(command -v python3)" -m ghlink.main "$@"
fi
echo "[ghlink] 需要 Python 3.14（vendored 依赖按 cp314 ABI 编译）。请安装: brew install python@3.14" >&2
exit 2
EOF
chmod 0755 "$APP/Contents/MacOS/ghlink"
fi

# 托盘入口（双击启动；LaunchAgent 也走此路径，绕开 LaunchServices 双击链路）
if [ "$PYI_MODE" != "1" ]; then
cat > "$APP/Contents/MacOS/ghlink-tray" <<'EOF'
#!/bin/bash
SELF="$0"
while [ -L "$SELF" ]; do
  LINK="$(readlink "$SELF")"
  case "$LINK" in
    /*) SELF="$LINK" ;;
    *) SELF="$(dirname "$SELF")/$LINK" ;;
  esac
done
APP_DIR="$(cd "$(dirname "$SELF")/.." && pwd)"
export PYTHONPATH="$APP_DIR/libexec:$APP_DIR/libexec/vendor"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
# v0.5.13（李工 10:30 发布风险）：同 ghlink wrapper，python 必须 3.14
_py() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 14) else 1)' 2>/dev/null
}
for PY in /opt/homebrew/opt/python@3.14/bin/python3.14 /usr/local/opt/python@3.14/bin/python3.14; do
  if [ -x "$PY" ] && _py "$PY"; then
    exec "$PY" -m ghlink.main tray "$@"
  fi
done
if command -v python3 >/dev/null 2>&1 && _py "$(command -v python3)"; then
  exec "$(command -v python3)" -m ghlink.main tray "$@"
fi
echo "[ghlink] 需要 Python 3.14（vendored 依赖按 cp314 ABI 编译）。请安装: brew install python@3.14" >&2
exit 2
EOF
chmod 0755 "$APP/Contents/MacOS/ghlink-tray"
fi

# Info.plist（v0.4.22：PkgInfo + CFBundlePackageType + LSUIElement 菜单栏识别）
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>ghlink</string>
  <key>CFBundleDisplayName</key><string>ghlink</string>
  <key>CFBundleIdentifier</key><string>com.ghlink.tray</string>
  <key>CFBundleVersion</key><string>__VERSION__</string>
  <key>CFBundleShortVersionString</key><string>__VERSION__</string>
  <key>CFBundleExecutable</key><string>ghlink-tray</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleIconFile</key><string>ghlink-icon</string>
  <key>LSMinimumSystemVersion</key><string>10.15</string>
  <key>LSUIElement</key><true/>
</dict></plist>
PLIST
sed -i '' "s/__VERSION__/$VERSION/g" "$APP/Contents/Info.plist"
printf 'APPL????' > "$APP/Contents/PkgInfo"
chmod 0644 "$APP/Contents/PkgInfo"

# 图标：png → icns
ICON_PNG="$ROOT/assets/ghlink-icon.png"
ICONSET="$STAGE/ghlink.iconset"
mkdir -p "$ICONSET"
for sz in 16 32 128 256 512; do
  sips -z $sz $sz "$ICON_PNG" --out "$ICONSET/icon_${sz}x${sz}.png" >/dev/null 2>&1
  sips -z $((sz*2)) $((sz*2)) "$ICON_PNG" --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null 2>&1
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/ghlink-icon.icns"

echo "==> 打包 dmg（app + Applications 快捷方式，拖一个文件即用；系统组件 app 首启自装）"
# 标准 dmg 布局：ghlink.app + Applications 软链（用户直接拖上去）+ 安装说明
DMG_CONTENT="$STAGE/dmg-content"
mkdir -p "$DMG_CONTENT"
cp -R "$APP" "$DMG_CONTENT/"
ln -s /Applications "$DMG_CONTENT/Applications"

# ad-hoc 重签（v0.5.12 李工 13:05 报「ghlink.app 已损坏」根治）——
# PyInstaller 产物 adhoc 签名与资源不匹配，Gatekeeper 判定损坏；
# 打包前强制全量重签（app + 内部所有二进制），本地校验通过再进 dmg。
# 彻底无弹窗需 Developer ID + notarization（待李工 Apple 账号决策）。
if [ -d "$DMG_CONTENT/ghlink.app" ]; then
  echo "==> ad-hoc 重签 app（Gatekeeper 兼容）"
  codesign --force --deep --sign - "$DMG_CONTENT/ghlink.app" >/dev/null 2>&1 \
    && codesign --verify --deep --strict "$DMG_CONTENT/ghlink.app" >/dev/null 2>&1 \
    && echo "  OK: 签名验证通过" \
    || echo "  WARN: 重签/校验失败（dmg 仍生成，真机可能报 Gatekeeper 拦截）"
fi
# 安装说明（李工 14:36「装两个文件离谱」收敛：手动安装 = 拖一个文件，系统组件首启自装）
# v0.5.12 更新：内嵌运行时 + Gatekeeper 签名提示（李工 13:15 定）
cat > "$DMG_CONTENT/安装说明.txt" <<'EOF'
ghlink 安装说明（v0.5.12 dmg 版）

1. 把 ghlink.app 拖到 Applications 文件夹（或直接拖到左侧 Applications 快捷方式）
2. 首次打开：右键 ghlink.app → 打开 → 点「打开」（未公证 app 首次需确认）
   若提示「已损坏，无法打开」，在终端执行：
     xattr -dr com.apple.quarantine /Applications/ghlink.app
   然后重新打开即可（应用功能正常，仅系统签名校验拦截）
3. 本版已内嵌 Python 运行时，无需安装任何依赖，双击即用
4. 托盘启动时若检测到系统组件未装（值守 daemon/sudoers/CLI 软链缺失），
   会弹一次管理员授权自动安装（一次性），之后无感
5. 托盘图标出现即完成；值守可在托盘菜单「启用值守」开启
EOF

# Finder 窗口布局：默认不生成 .DS_Store（v0.5.13 李工 09:45 确认）——
# 自定义大图标布局（make_dmg_dsstore.py 的 ICVO/Iloc）在 ARM/新系统 Finder 上
# 渲染失败 → DiskImageMounter 挂载后显示空白（文件实际在）。删掉后 Finder 用
# 默认布局，ghlink.app 必显示。如需大图标，后续修 make_dmg_dsstore.py 兼容布局。
# 可选恢复：DMG_DSSTORE=1 bash build_dmg.sh（旧行为，仅供调试）
if [ -n "${DMG_DSSTORE:-}" ]; then
  echo "==> 生成 Finder 布局 .DS_Store（大图标，调试模式）"
  python3 "$ROOT/packaging/macos/make_dmg_dsstore.py" "$DMG_CONTENT" 96 2>/dev/null || echo "  WARN: .DS_Store 生成失败（dmg 仍可用，默认布局）"
fi

hdiutil create -volname "ghlink" -srcfolder "$DMG_CONTENT" -ov \
  -format UDZO "$OUT/ghlink-${VERSION}.dmg" >/dev/null

# v0.5.x（李工 14:36「装两个文件离谱」）：不再单独打 system.pkg——
# 系统组件（LaunchDaemon + sudoers + CLI 软链）改 app 首启自装（tray 启动检测
# 缺失 → 弹管理员授权一次性安装），dmg 只含 app，手动安装 = 拖一个文件。

echo "==> 完成:"
ls -la "$OUT/"
