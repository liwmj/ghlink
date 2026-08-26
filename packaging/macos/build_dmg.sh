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

# vendor 依赖（托盘 pystray + Pillow）注入 .app，核心零依赖
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENDOR="$APP/Contents/libexec/vendor"
WHEEL_DIR="$STAGE/wheels"
rm -rf "$WHEEL_DIR"
# 1) 通用依赖（pystray + PyObjC：universal2/纯 wheel，双架构通用）
mkdir -p "$WHEEL_DIR/universal"
"$PYTHON_BIN" -m pip download --only-binary=:all: --no-deps \
  -d "$WHEEL_DIR/universal" pystray six pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-Quartz --quiet || exit 1
for W in "$WHEEL_DIR/universal"/*.whl; do unzip -qo "$W" -d "$WHEEL_DIR/universal/unpacked"; done
cp -R "$WHEEL_DIR/universal/unpacked/." "$VENDOR/"
# 2) Pillow 分架构拉取 → lipo 合并 fat binary（单 dmg 通吃 Intel/Apple Silicon）
for ARCH in x86_64 arm64; do
  mkdir -p "$WHEEL_DIR/$ARCH"
  PLATFORM="macosx_10_13_${ARCH}"
  [ "$ARCH" = "arm64" ] && PLATFORM="macosx_11_0_${ARCH}"
  "$PYTHON_BIN" -m pip download --only-binary=:all: --no-deps \
    --platform "$PLATFORM" --python-version 3.14 --abi cp314 \
    -d "$WHEEL_DIR/$ARCH" Pillow --quiet || exit 1
  for W in "$WHEEL_DIR/$ARCH"/*.whl; do unzip -qo "$W" -d "$WHEEL_DIR/$ARCH/unpacked"; done
done
cp -R "$WHEEL_DIR/x86_64/unpacked/." "$VENDOR/"
# v0.4.16：lipo 必须覆盖 .dylib——Pillow 的 @loader_path/.dylibs/*.dylib
find "$VENDOR" \( -name "*.so" -o -name "*.dylib" \) -type f | while read -r SO; do
  REL="${SO#"$VENDOR"/}"
  ARM_SO="$WHEEL_DIR/arm64/unpacked/$REL"
  if [ -f "$ARM_SO" ]; then
    lipo -create "$SO" "$ARM_SO" -output "$SO.tmp" && mv "$SO.tmp" "$SO"
    echo "    lipo merged: $REL"
  fi
done
rm -rf "$WHEEL_DIR"

# CLI 可执行（内嵌 .app；首启自装时软链 /usr/local/bin/ghlink 指向此路径）
cat > "$APP/Contents/MacOS/ghlink" <<EOF
#!/bin/bash
SELF="\$0"
while [ -L "\$SELF" ]; do
  LINK="\$(readlink "\$SELF")"
  case "\$LINK" in
    /*) SELF="\$LINK" ;;
    *) SELF="\$(dirname "\$SELF")/\$LINK" ;;
  esac
done
APP_DIR="\$(cd "\$(dirname "\$SELF")/.." && pwd)"
export PYTHONPATH="\$APP_DIR/libexec:\$APP_DIR/libexec/vendor"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
for PY in /opt/homebrew/opt/python@3.14/bin/python3.14 /usr/local/opt/python@3.14/bin/python3.14; do
  [ -x "\$PY" ] && exec "\$PY" -m ghlink.main "\$@"
done
exec "/usr/local/bin/python3" -m ghlink.main "\$@"
EOF
chmod 0755 "$APP/Contents/MacOS/ghlink"

# 托盘入口（双击启动；LaunchAgent 也走此路径，绕开 LaunchServices 双击链路）
cat > "$APP/Contents/MacOS/ghlink-tray" <<EOF
#!/bin/bash
SELF="\$0"
while [ -L "\$SELF" ]; do
  LINK="\$(readlink "\$SELF")"
  case "\$LINK" in
    /*) SELF="\$LINK" ;;
    *) SELF="\$(dirname "\$SELF")/\$LINK" ;;
  esac
done
APP_DIR="\$(cd "\$(dirname "\$SELF")/.." && pwd)"
export PYTHONPATH="\$APP_DIR/libexec:\$APP_DIR/libexec/vendor"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
for PY in /opt/homebrew/opt/python@3.14/bin/python3.14 /usr/local/opt/python@3.14/bin/python3.14; do
  [ -x "\$PY" ] && exec "\$PY" -m ghlink.main tray "\$@"
done
exec "/usr/local/bin/python3" -m ghlink.main tray "\$@"
EOF
chmod 0755 "$APP/Contents/MacOS/ghlink-tray"

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
# 安装说明（李工 14:36「装两个文件离谱」收敛：手动安装 = 拖一个文件，系统组件首启自装）
cat > "$DMG_CONTENT/安装说明.txt" <<'EOF'
ghlink 安装说明（v0.5.x dmg 版）
1. 把 ghlink.app 拖到 Applications 文件夹（或直接拖到左侧 Applications 快捷方式）
2. 首次运行 ghlink.app（右键 → 打开，未签名首次需授权）
3. 托盘启动时若检测到系统组件未装（值守 daemon/sudoers/CLI 软链缺失），
   会弹一次管理员授权自动安装（一次性），之后无感
4. 托盘图标出现即完成；值守可在托盘菜单「启用值守」开启
EOF

# Finder 窗口布局：.DS_Store 大图标（李工 16:50 反馈「DMG包里的图标大一点”）——
# 用 ds-store 库直接生成，不依赖 Finder/TCC 授权（osascript 方式 CI 上不可用）
echo "==> 生成 Finder 布局 .DS_Store（大图标）"
python3 "$ROOT/packaging/macos/make_dmg_dsstore.py" "$DMG_CONTENT" 96 2>/dev/null || echo "  WARN: .DS_Store 生成失败（dmg 仍可用，默认布局）"

hdiutil create -volname "ghlink" -srcfolder "$DMG_CONTENT" -ov \
  -format UDZO "$OUT/ghlink-${VERSION}.dmg" >/dev/null

# v0.5.x（李工 14:36「装两个文件离谱」）：不再单独打 system.pkg——
# 系统组件（LaunchDaemon + sudoers + CLI 软链）改 app 首启自装（tray 启动检测
# 缺失 → 弹管理员授权一次性安装），dmg 只含 app，手动安装 = 拖一个文件。

echo "==> 完成:"
ls -la "$OUT/"
