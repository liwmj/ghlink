cask "ghlink" do
  version "0.4.13"
  sha256 "REPLACE_WITH_PKG_SHA256"

  url "https://github.com/liwmj/ghlink/releases/download/v#{version}/ghlink-#{version}.pkg",
      verified: "github.com/liwmj/ghlink/"
  name "ghlink"
  desc "GitHub 链路自愈工具：主动监控连通性，异常时自动换 IP 写 hosts，自检回滚 + 多渠道告警"
  homepage "https://github.com/liwmj/ghlink"

  # v0.4.12（李工 2026-08-24 01:03 终裁 D1=cask 单轨 / D2=.app 内嵌 CLI+symlink / D3=uninstall 调 ghlink uninstall 彻底）：
  # - D1：formula 已 deprecate 退役，cask 单轨（brew install --cask ghlink）
  # - D2：pkg 内含 ghlink.app（双击启动托盘）+ /usr/local/bin/ghlink symlink（对齐 sudoers 放行）
  # - D3：uninstall 钩子调 ghlink uninstall（停任务 + 还原 hosts + 删配置，彻底清理）
  pkg "ghlink-#{version}.pkg"

  # Bug 2 修复（v0.4.12）：vendor 以 python@3.14 编译，运行时锁定同版本（二进制扩展 ABI 兼容）
  depends_on formula: "python@3.14"

  # D3（李工终裁）：卸载时调 ghlink uninstall 彻底清理——停任务 + 还原 hosts + 删配置，
  # 比仅删文件更彻底（含 /etc/hosts 的 ghlink 段落还原、LaunchDaemon 移除、/var/lib/ghlink 清理）
  uninstall pkgutil: "com.ghlink.pkg",
            script:  {
              executable: "/usr/local/bin/ghlink",
              args:       ["uninstall"],
              sudo:       true,
            }

  # zap：彻底清理残留（brew uninstall --zap ghlink 时执行，二次兜底）
  zap trash: [
    "/usr/local/etc/ghlink",
    "/var/lib/ghlink",
    "~/Library/LaunchAgents/com.ghlink.tray.plist",
    "~/Library/Application Support/ghlink",
  ]

  caveats <<~EOS
    ghlink 已安装（Cask 版，v0.4.12 起 formula 退役、cask 单轨）。使用步骤：
      1. 启用值守: sudo ghlink enable   （注册系统 LaunchDaemon，需 root 写 hosts）
      2. 查看状态: ghlink status
      3. 停用值守: sudo ghlink disable  （保留 hosts 与配置）
      4. 彻底卸载: brew uninstall --cask ghlink（自动调 ghlink uninstall：停任务 + 还原 hosts + 删配置）
    双击 /Applications/ghlink.app 可启动托盘（随登录自启可开「开机自启动」）。
    值守需 root 权限（写 /etc/hosts），请用 sudo ghlink enable。
  EOS
end
