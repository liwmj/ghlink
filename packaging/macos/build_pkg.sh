#!/bin/bash
# ghlink macOS pkg 构建（v0.4.12 Cask 方案）
#
# 设计口径：
#   D1 = cask 单轨（formula deprecate 退役）
#   D2 = .app 内嵌 CLI + /usr/local/bin/ghlink symlink（对齐 sudoers 放行，防提权断链）
#   D3 = uninstall 钩子调 ghlink uninstall（停任务+还原 hosts+删配置，彻底）
#
# 产物：dist/macos/ghlink-<VERSION>.pkg
# 内容：
#   - App: /Applications/ghlink.app（双击启动托盘，LOGO 图标，内嵌 CLI）
#   - CLI symlink: /usr/local/bin/ghlink -> /Applications/ghlink.app/Contents/MacOS/ghlink
#   - 配置模板: /usr/local/etc/ghlink/config.json
#
# 用法: bash packaging/macos/build_pkg.sh
# 依赖: pkgbuild + productbuild（macOS 自带）

set -e

# v0.4.15（验收发现 pkg 内版本标注硬编码）：版本号动态化——CI 取 GITHUB_REF_NAME（tag），
# 本地取最近 git tag，可被 VERSION 环境变量覆盖
VERSION="${VERSION:-$(git -C "$ROOT" describe --tags --abbrev=0 2>/dev/null | sed 's/^v//')}"
VERSION="${VERSION:-0.0.0}"
echo "==> 构建版本: $VERSION"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STAGE="$ROOT/build/macos-pkg"
PKG_OUT="$ROOT/dist/macos"
APP_NAME="ghlink.app"

echo "==> 清理旧构建"
rm -rf "$STAGE" "$PKG_OUT"
mkdir -p "$STAGE/root/usr/local/etc/ghlink" "$STAGE/root/Applications" "$PKG_OUT"

echo "==> 组装 .app（内嵌 CLI + 托盘，D2）"
APP="$STAGE/root/Applications/$APP_NAME"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$APP/Contents/libexec/ghlink" "$APP/Contents/libexec/assets" "$APP/Contents/libexec/vendor"
cp "$ROOT"/src/ghlink/*.py "$APP/Contents/libexec/ghlink/"
cp "$ROOT/config.example.json" "$APP/Contents/libexec/"
cp "$ROOT/assets/ghlink-icon.png" "$APP/Contents/libexec/assets/"

# vendor 依赖（托盘 pystray + Pillow）注入 .app，核心零依赖
PYTHON_BIN="${PYTHON_BIN:-python3}"
# v0.4.15（李工 02:50 拍板双架构）：Pillow cp314 无 universal2 wheel → 分架构拉取 + lipo 合并 fat binary；
# pystray + PyObjC 框架为 universal2/纯 Python wheel（双架构通用，无需合并）
VENDOR="$APP/Contents/libexec/vendor"
WHEEL_DIR="$STAGE/wheels"
rm -rf "$WHEEL_DIR"
# 1) 通用依赖（pystray + PyObjC：universal2/纯 wheel，双架构通用）
mkdir -p "$WHEEL_DIR/universal"
"$PYTHON_BIN" -m pip download --only-binary=:all: --no-deps \
  -d "$WHEEL_DIR/universal" pystray six pyobjc-core pyobjc-framework-Cocoa pyobjc-framework-Quartz --quiet || exit 1
for W in "$WHEEL_DIR/universal"/*.whl; do unzip -qo "$W" -d "$WHEEL_DIR/universal/unpacked"; done
cp -R "$WHEEL_DIR/universal/unpacked/." "$VENDOR/"
# 2) Pillow 分架构拉取 → lipo 合并 C 扩展成 fat binary（单 pkg 通吃 Intel/Apple Silicon）
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
# v0.4.16（ARM 真机暴露）：lipo 必须覆盖 .dylib——Pillow 的 @loader_path/.dylibs/*.dylib
# 动态库只合了 x86_64，arm64 机器 dyld 加载 _imaging.so 时找不到 arm64 dylib → ImportError
find "$VENDOR" \( -name "*.so" -o -name "*.dylib" \) -type f | while read -r SO; do
  REL="${SO#"$VENDOR"/}"
  ARM_SO="$WHEEL_DIR/arm64/unpacked/$REL"
  if [ -f "$ARM_SO" ]; then
    lipo -create "$SO" "$ARM_SO" -output "$SO.tmp" && mv "$SO.tmp" "$SO"
    echo "    lipo merged: $REL"
  fi
done
rm -rf "$WHEEL_DIR"

# CLI 可执行（内嵌 .app，sudoers 放行此路径）
# 相对化：APP_DIR 动态推导（不写死构建机路径，v0.4.12 Bug 1 修复）
# python 版本锁定：优先 python@3.14（vendor 以 3.14 编译，Bug 2 修复）
cat > "$APP/Contents/MacOS/ghlink" <<EOF
#!/bin/bash
# symlink 解析链（v0.4.13 修复）：/usr/local/bin/ghlink 是 symlink，\$0 解析到
# symlink 路径导致 APP_DIR 推导错——先 readlink 循环解析到真实脚本路径再推导
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
for PY in /opt/homebrew/opt/python@3.14/bin/python3.14 /usr/local/opt/python@3.14/bin/python3.14; do
  [ -x "\$PY" ] && exec "\$PY" -m ghlink.main "\$@"
done
exec "/usr/local/bin/python3" -m ghlink.main "\$@"
EOF
chmod 0755 "$APP/Contents/MacOS/ghlink"

# 托盘入口（双击启动）
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
for PY in /opt/homebrew/opt/python@3.14/bin/python3.14 /usr/local/opt/python@3.14/bin/python3.14; do
  [ -x "\$PY" ] && exec "\$PY" -m ghlink.main tray "\$@"
done
exec "/usr/local/bin/python3" -m ghlink.main tray "\$@"
EOF
chmod 0755 "$APP/Contents/MacOS/ghlink-tray"

# Info.plist
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
  <key>CFBundleIconFile</key><string>ghlink-icon</string>
  <key>LSMinimumSystemVersion</key><string>10.15</string>
</dict></plist>
PLIST

# v0.4.15：plist 版本号随构建版本（动态化，防内版标注滞留在旧 tag）
sed -i '' "s/__VERSION__/$VERSION/g" "$APP/Contents/Info.plist"

# 图标：png → icns（sips + iconutil）
ICON_PNG="$ROOT/assets/ghlink-icon.png"
ICONSET="$STAGE/ghlink.iconset"
mkdir -p "$ICONSET"
for sz in 16 32 128 256 512; do
  sips -z $sz $sz "$ICON_PNG" --out "$ICONSET/icon_${sz}x${sz}.png" >/dev/null 2>&1
  sips -z $((sz*2)) $((sz*2)) "$ICON_PNG" --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null 2>&1
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/ghlink-icon.icns"

echo "==> CLI symlink（/usr/local/bin/ghlink -> .app 内 CLI，D2）"
mkdir -p "$STAGE/root/usr/local/bin"
ln -s "/Applications/$APP_NAME/Contents/MacOS/ghlink" "$STAGE/root/usr/local/bin/ghlink"

echo "==> 配置模板"
cp "$ROOT/config.example.json" "$STAGE/root/usr/local/etc/ghlink/config.json"

echo "==> 确保 postinstall 可执行（GitHub API 推送默认 644）"
chmod 0755 "$ROOT/packaging/macos/scripts/postinstall" 2>/dev/null || true

echo "==> pkgbuild（root payload）"
pkgbuild --root "$STAGE/root" \
  --identifier com.ghlink.pkg \
  --version "$VERSION" \
  --scripts "$ROOT/packaging/macos/scripts" \
  "$STAGE/ghlink-core.pkg"

echo "==> productbuild（分发包）"
productbuild --package "$STAGE/ghlink-core.pkg" \
  --version "$VERSION" \
  "$PKG_OUT/ghlink-${VERSION}.pkg"

echo "==> 完成: $PKG_OUT/ghlink-${VERSION}.pkg"
ls -la "$PKG_OUT/"
