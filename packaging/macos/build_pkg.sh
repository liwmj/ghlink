#!/bin/bash
# ghlink macOS pkg 构建（v0.4.12 Cask 方案，李工 2026-08-24 01:03 终裁 D1/D2/D3）
#
# 终裁口径：
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

VERSION="0.4.12"
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
"$PYTHON_BIN" -m pip install --target "$APP/Contents/libexec/vendor" --quiet pystray Pillow

# CLI 可执行（内嵌 .app，sudoers 放行此路径）
cat > "$APP/Contents/MacOS/ghlink" <<EOF
#!/bin/bash
export PYTHONPATH="$APP/Contents/libexec:$APP/Contents/libexec/vendor"
exec "/usr/local/bin/python3" -m ghlink.main "\$@"
EOF
chmod 0755 "$APP/Contents/MacOS/ghlink"

# 托盘入口（双击启动）
cat > "$APP/Contents/MacOS/ghlink-tray" <<EOF
#!/bin/bash
export PYTHONPATH="$APP/Contents/libexec:$APP/Contents/libexec/vendor"
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
  <key>CFBundleVersion</key><string>0.4.12</string>
  <key>CFBundleShortVersionString</key><string>0.4.12</string>
  <key>CFBundleExecutable</key><string>ghlink-tray</string>
  <key>CFBundleIconFile</key><string>ghlink-icon</string>
  <key>LSMinimumSystemVersion</key><string>10.15</string>
</dict></plist>
PLIST

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
