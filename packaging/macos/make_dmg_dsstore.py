#!/usr/bin/env python3
"""生成 dmg Finder 布局 .DS_Store（icon view + 大图标）
李工 16:50 反馈：DMG 包里的图标大一点。
不依赖 Finder/TCC 授权，CI 可直接运行（ds-store 库）。
用法: python3 make_dmg_dsstore.py <目标目录> [icon_size]
"""
import sys, os, struct
from ds_store import DSStore, DSStoreEntry

def main():
    target_dir = sys.argv[1]
    icon_size = int(sys.argv[2]) if len(sys.argv) > 2 else 96
    ds_path = os.path.join(target_dir, '.DS_Store')
    with DSStore.open(ds_path, 'w+') as d:
        # 图标视图 + 大图标（李工 16:50 反馈）
        d.insert(DSStoreEntry(b'icvp', b'ICVO', b'long', 1))     # icon view
        d.insert(DSStoreEntry(b'icvp', b'ICZO', b'long', icon_size))  # 图标大小
        d.insert(DSStoreEntry(b'icvp', b'ICVT', b'long', 0))     # 排列：无
        # 文件位置（尽量靠左排布，Finder 打开即见）
        d.insert(DSStoreEntry(b'ghlink.app', b'Iloc', b'blob', struct.pack('<II', 80, 80)))
        d.insert(DSStoreEntry(b'Applications', b'Iloc', b'blob', struct.pack('<II', 360, 80)))
        d.insert(DSStoreEntry('安装说明.txt'.encode('utf-8'), b'Iloc', b'blob', struct.pack('<II', 200, 280)))
    print(f"✅ .DS_Store 已生成: {ds_path} (icon size={icon_size})")

if __name__ == '__main__':
    main()
