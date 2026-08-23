cask "ghlink" do
  version "0.4.12"
  sha256 "REPLACE_WITH_PKG_SHA256"

  url "https://github.com/liwmj/ghlink/releases/download/v#{version}/ghlink-#{version}.pkg",
      verified: "github.com/liwmj/ghlink/"
  name "ghlink"
  desc "GitHub 链路自愈工具：主动监控连通性，异常时自动换 IP 写 hosts，自检回滚 + 多渠道告警"
  homepage "https://github.com/liwmj/ghlink"

  # v0.4.12（李工 2026-08-23 17:30 拍板 C 方案）：Cask 带卸载钩子，
  # 解决 formula 无 uninstall 机制的问题——卸载自动清理配置与 LaunchDaemon。
  pkg "ghlink-#{version}.pkg"

  # 卸载钩子：删除 pkg 安装的文件
  uninstall pkgutil: "com.ghlink.pkg"

  # zap：彻底清理用户配置（brew uninstall --zap ghlink 时执行）
  zap trash: [
    "/usr/local/etc/ghlink",
    "/var/lib/ghlink",
    "~/Library/LaunchAgents/com.ghlink.tray.plist",
    "~/Library/Application Support/ghlink",
  ]

  caveats <<~EOS
    ghlink 已安装（Cask 版）。使用步骤：
      1. 启用值守: sudo ghlink enable   （注册系统 LaunchDaemon，需 root 写 hosts）
      2. 查看状态: ghlink status
      3. 停用值守: sudo ghlink disable  （保留 hosts 与配置）
      4. 彻底卸载: brew uninstall --zap ghlink（自动清理配置与 LaunchDaemon）
    双击 /Applications/ghlink.app 可启动托盘（随登录自启可开「开机自启动」）。
    值守需 root 权限（写 /etc/hosts），请用 sudo ghlink enable。
  EOS
end
