#!/bin/bash
# ghlink macOS pkg 构建（v0.4.12 Cask 方案，李工 2026-08-23 17:30 拍板 C）
#
# 产物：dist/macos/ghlink-<VERSION>.pkg
# 内容：
#   - CLI: /usr/local/bin/ghlink（brew 兼容 wrapper 或直接 bin）
#   - App: /Applications/ghlink.app（双击启动托盘，带 LOGO 图标）
#   - 配置模板: /usr/local/etc/ghlink/config.json（首次安装默认）
#
# 用法: bash packaging/macos/build_pkg.sh
# 依赖: pkgbuild + productbuild（macOS 自带，无需第三方）

set -e

VERSION="0.4.12"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STAGE="$ROOT/build/macos-pkg"
PKG_OUT="$ROOT/dist/macos"
APP_NAME="ghlink.app"

echo "==> 清理旧构建"
rm -rf "$STAGE" "$PKG_OUT"
mkdir -p "$STAGE/root/usr/local/bin" "$STAGE/root/usr/local/etc/ghlink" "$STAGE/root/Applications" "$PKG_OUT"

echo "==> 组装 CLI wrapper（brew 同款：PYTHONPATH 注入 libexec + vendor）"
mkdir -p "$STAGE/root/usr/local/libexec/ghlink" "$STAGE/root/usr/local/libexec/assets" "$STAGE/root/usr/local/libexec/vendor"
cp "$ROOT"/src/ghlink/*.py "$STAGE/root/usr/local/libexec/ghlink/"
cp "$ROOT/config.example.json" "$STAGE/root/usr/local/libexec/"
cp "$ROOT/assets/ghlink-icon.png" "$STAGE/root/usr/local/libexec/assets/"

# vendor 依赖（托盘 pystray + Pillow）注入安装包，核心零依赖
PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" -m pip install --target "$STAGE/root/usr/local/libexec/vendor" --quiet pystray Pillow

cat > "$STAGE/root/usr/local/bin/ghlink" <<EOF
#!/bin/bash
export PYTHONPATH="/usr/local/libexec:/usr/local/libexec/vendor"
exec "/usr/local/bin/python3" -m ghlink.main "\$@"
EOF
chmod 0755 "$STAGE/root/usr/local/bin/ghlink"

echo "==> 组装 .app（双击启动托盘）"
mkdir -p "$STAGE/root/Applications/$APP_NAME/Contents/MacOS" "$STAGE/root/Applications/$APP_NAME/Contents/Resources"
cat > "$STAGE/root/Applications/$APP_NAME/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>ghlink</string>
  <key>CFBundleDisplayName</key><string>ghlink</string>
  <key>CFBundleIdentifier</key><string>com.ghlink.tray</string>
  <key>CFBundleVersion</key><string>0.4.12</string>
  <key>CFBundleShortVersionString</key><string>0.4.12</string>
  <key>CFBundleExecutable</key><string>ghlink-tray</string>
  <key>CFBundleIconFile</key><string>ghlink-icon</string>
  <key>LSMinimumSystemVersion</key><string>10.15</string>
</dict></plist>
PLIST
cat > "$STAGE/root/Applications/$APP_NAME/Contents/MacOS/ghlink-tray" <<EOF
#!/bin/bash
export PYTHONPATH="/usr/local/libexec:/usr/local/libexec/vendor"
exec "/usr/local/bin/python3" -m ghlink.main tray "\$@"
EOF
chmod 0755 "$STAGE/root/Applications/$APP_NAME/Contents/MacOS/ghlink-tray"

# 图标：png → icns（sips + iconutil）
ICON_PNG="$ROOT/assets/ghlink-icon.png"
ICONSET="$STAGE/ghlink.iconset"
mkdir -p "$ICONSET"
for sz in 16 32 128 256 512; do
  sips -z $sz $sz "$ICON_PNG" --out "$ICONSET/icon_${sz}x${sz}.png" >/dev/null 2>&1
  sips -z $((sz*2)) $((sz*2)) "$ICON_PNG" --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null 2>&1
done
iconutil -c icns "$ICONSET" -o "$STAGE/root/Applications/$APP_NAME/Contents/Resources/ghlink-icon.icns"

echo "==> 配置模板（首次安装默认，enable 时自动生成）"
cp "$ROOT/config.example.json" "$STAGE/root/usr/local/etc/ghlink/config.json"

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
