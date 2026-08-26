cask "ghlink" do
  version "0.5.0"
  sha256 "REPLACE_WITH_DMG_SHA256"

  # v0.5.0（李工 13:45 拍板 dmg 路线恢复，拂晓 13:59 定格）：dmg+cask 混合方案
  # - dmg 管 app：拖入 /Applications 即用，无 postinstall/relocate/收据链（Code 112 类问题根治）
  # - 系统组件（LaunchDaemon + sudoers + CLI 软链）走一次性小 pkg：ghlink-#{version}-system.pkg
  # - 割裂态防护：README 引导装系统组件小 pkg
  url "https://github.com/liwmj/ghlink/releases/download/v#{version}/ghlink-#{version}.dmg",
      verified: "github.com/liwmj/ghlink/"
  name "ghlink"
  desc "GitHub 链路自愈工具：主动监控连通性，异常时自动换 IP 写 hosts，自检回滚 + 多渠道告警"
  homepage "https://github.com/liwmj/ghlink"

  # app 拖入 /Applications（cask 原生 dmg 支持：下载→挂载→拷 .app→Caskroom 记账）
  app "ghlink.app"

  # v0.5.x（李工 14:36「装两个文件离谱」收敛）：不再单独打 system.pkg——
  # 系统组件（LaunchDaemon + sudoers + /usr/local/bin/ghlink 软链）改 app 首启自装
  # （tray 启动检测缺失 → 弹管理员授权一次性安装），用户全程只拖一个文件。

  # vendor 以 python@3.14 编译，运行时锁定同版本（二进制扩展 ABI 兼容）
  depends_on formula: "python@3.14"

  # 卸载：ghlink uninstall 彻底清理（停任务 + 还原 hosts + 删配置）
  uninstall script: {
             executable: "/usr/local/bin/ghlink",
             args:       ["uninstall"],
             sudo:       false,
           }

  # zap：彻底清理残留（brew uninstall --zap ghlink 时执行，二次兜底）
  zap trash: [
    "/usr/local/etc/ghlink",
    "/opt/homebrew/etc/ghlink",
    "/var/lib/ghlink",
    "~/.ghlink",
    "~/Library/LaunchAgents/com.ghlink.tray.plist",
    "~/Library/Application Support/ghlink",
  ]
end
