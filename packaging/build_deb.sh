#!/bin/bash
# build_deb.sh — ghlink .deb 打包脚本（v0.2.0）
# 用法: bash packaging/build_deb.sh  （在仓库根目录执行）
# 输出: dist/ghlink_0.2.10-1_all.deb
# 说明: 复用拂晓 Ubuntu 验证过的构建树（DEBIAN/ + usr/bin + usr/lib + etc + usr/share）

set -e
cd "$(dirname "$0")/.."

VERSION="0.4.19"
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
# v0.4.2 修复（跟进人 Linux 回归发现，2026-08-22）：control 里 Version 写死 0.2.1-1
# 与资产名不一致 → apt 升级比版本号错乱。构建时动态注入 ${VERSION}-1
sed -i "s/^Version: .*/Version: ${VERSION}-1/" "$BUILD_DIR/DEBIAN/control"
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
# v0.4.2.1（拂晓 Linux 严格测试 #3）：config.example.json 为平台无关相对路径
# （修 Windows 模板），deb 安装必须用绝对路径模板——否则状态文件从 /var/lib/ghlink/
# 漂移到 /etc/ghlink/，0.2.17→0.4.2 升级用户"失忆"（旧心跳/历史全断）。
# 这里复制后用 sed 把相对路径字段改写为 /var/lib/ghlink/ 绝对路径。
cp config.example.json "$BUILD_DIR/usr/share/ghlink/config.example.json"
_abs_template() {
  sed -e 's|"state_file": "ghlink_status.json"|"state_file": "/var/lib/ghlink/ghlink_status.json"|' \
      -e 's|"lock_file": "ghlink.lock"|"lock_file": "/var/lib/ghlink/ghlink.lock"|' \
      -e 's|"hosts_backup_dir": "backup"|"hosts_backup_dir": "/var/lib/ghlink/backup"|'
}
if [ ! -f "$BUILD_DIR/etc/ghlink/config.json" ]; then
    _abs_template < config.example.json > "$BUILD_DIR/etc/ghlink/config.json"
fi

# 5. 打包（拂晓复测 P1: 不用管道吞错误，失败必须退出非 0，CI 才能正确报错）
OUT_DEB="dist/ghlink_${VERSION}-1_all.deb"
if ! dpkg-deb --build "$BUILD_DIR" "$OUT_DEB" 2>&1; then
    echo "❌ dpkg-deb 构建失败" >&2
    exit 1
fi
echo "✅ 构建完成: $OUT_DEB"

