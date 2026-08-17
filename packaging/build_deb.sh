#!/bin/bash
# build_deb.sh — ghlink .deb 打包脚本（v0.2.0）
# 用法: bash packaging/build_deb.sh  （在仓库根目录执行）
# 输出: dist/ghlink_0.2.10-1_all.deb
# 说明: 复用拂晓 Ubuntu 验证过的构建树（DEBIAN/ + usr/bin + usr/lib + etc + usr/share）

set -e
cd "$(dirname "$0")/.."

VERSION="0.2.12"
PKG_NAME="ghlink_${VERSION}-1_all"
BUILD_DIR="build/deb/${PKG_NAME}"

echo "=== 构建 .deb（${PKG_NAME}） ==="

# 0. 清理 + 建输出目录（拂晓复测 P1: 清理后必须重建 dist/）
rm -rf "$BUILD_DIR" "dist/ghlink_${VERSION}-1_all.deb" 2>/dev/null || true
mkdir -p dist
mkdir -p "$BUILD_DIR/DEBIAN" \
         "$BUILD_DIR/usr/bin" \
         "$BUILD_DIR/usr/lib/ghlink" \
         "$BUILD_DIR/etc/ghlink" \
         "$BUILD_DIR/usr/share/ghlink"

# 1. DEBIAN 控制文件
cp packaging/debian/control "$BUILD_DIR/DEBIAN/control"
cp packaging/debian/postinst "$BUILD_DIR/DEBIAN/postinst"
cp packaging/debian/prerm "$BUILD_DIR/DEBIAN/prerm"
chmod 0755 "$BUILD_DIR/DEBIAN/postinst" "$BUILD_DIR/DEBIAN/prerm"
mkdir -p "$BUILD_DIR/DEBIAN"
printf '/etc/ghlink/config.json\n' > "$BUILD_DIR/DEBIAN/conffiles"

# 2. 代码（保持包结构）
cp -r src/ghlink "$BUILD_DIR/usr/lib/ghlink/"
rm -rf "$BUILD_DIR/usr/lib/ghlink/ghlink/__pycache__"

# 3. bin 入口 wrapper
cat > "$BUILD_DIR/usr/bin/ghlink" << 'EOF'
#!/bin/sh
PYTHONPATH=/usr/lib/ghlink exec python3 -c "from ghlink.main import main; import sys; sys.exit(main())" "$@"
EOF
chmod 0755 "$BUILD_DIR/usr/bin/ghlink"

# 4. 默认配置 + 示例
cp config.example.json "$BUILD_DIR/usr/share/ghlink/config.example.json"
if [ ! -f "$BUILD_DIR/etc/ghlink/config.json" ]; then
    cp config.example.json "$BUILD_DIR/etc/ghlink/config.json"
fi

# 5. 打包（拂晓复测 P1: 不用管道吞错误，失败必须退出非 0，CI 才能正确报错）
OUT_DEB="dist/ghlink_${VERSION}-1_all.deb"
if ! dpkg-deb --build "$BUILD_DIR" "$OUT_DEB" 2>&1; then
    echo "❌ dpkg-deb 构建失败" >&2
    exit 1
fi
echo "✅ 构建完成: $OUT_DEB"
