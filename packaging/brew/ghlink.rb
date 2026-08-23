# Homebrew Formula: ghlink
# 用法: brew install ghlink（本 formula 由 brew tap liwmj/ghlink 提供）
# 参考: v0.2 安装包技术方案草案（brew 线：libexec + bin 入口 + launchd 模板）
#
# 修复记录（赛博接口复核 2026-08-14）：
# P1: 相对导入入口 —— 安装保持包结构 libexec/ghlink/，bin wrapper 用绝对导入
# P2: service 块移除 —— 用户级 launchd 写 /etc/hosts 会失败，官方值守统一走 ghlink enable
#
# v0.4.7（赛博 2026-08-23，李工反馈 brew 停在 0.2.18）：
# P3: 版本同步发版 —— url/sha256 必须随每次发版 bump（build.yml 已加 tap 自动同步校验）
# P4: 卸载清理 —— def uninstall 钩子删 etc/ghlink 配置（brew 默认保留 etc 防误删，
#     这里显式清理；系统级残留 /var/lib/ghlink 由 ghlink uninstall 处理，见 caveats）

class Ghlink < Formula
  desc "GitHub 链路自愈工具：主动监控连通性，异常时自动换 IP 写 hosts，自检回滚 + 多渠道告警"
  homepage "https://github.com/liwmj/ghlink"
  url "https://github.com/liwmj/ghlink/archive/refs/tags/v0.4.6.tar.gz"
  sha256 "a0bf07fb3582dec37bb74b0dd9beb608c7ba0b0e43bc6c7640a3f9e14ab27968"
  license "MIT"
  head "https://github.com/liwmj/ghlink.git", branch: "master"

  depends_on "python@3.12"

  def install
    # 保持包结构安装到 libexec/ghlink/（解决 main.py 相对导入）
    (libexec/"ghlink").install Dir["src/ghlink/*.py"]
    libexec.install "config.example.json"

    # v0.2.17（李工 21:47 定）：托盘图标必须用 LOGO——补装 assets 图标
    # 到 libexec/assets/（_icon_path 已加 brew 路径候选），避免纯色回退
    (libexec/"assets").install "assets/ghlink-icon.png"

    # 托盘依赖（pystray + Pillow）仅注入安装包：pip 装到 libexec/vendor，核心源码保持零依赖
    py = Formula["python@3.12"].opt_bin/"python3.12"
    system py, "-m", "pip", "install", "--target", libexec/"vendor", "--quiet", "pystray", "Pillow"

    # bin 入口：绝对导入 wrapper（仿 ghlink_entry.py）+ PYTHONPATH 注入 libexec + vendor
    (bin/"ghlink").write <<~EOS
      #!/bin/bash
      export PYTHONPATH="#{libexec}:#{libexec}/vendor"
      exec "#{py}" -m ghlink.main "$@"
    EOS
    chmod 0755, bin/"ghlink"

    # 配置目录（默认不自启，enable 时才注册系统 LaunchDaemon）
    # v0.2.19（李工 8 条⑦）：config 模板直接同步 config.example.json（8 域名），
    # 不再手写硬编码旧模板（旧模板只有 2 域名，与最新配置脱节）
    # v0.3.0（李工 2026-08-22 定）：换版本删旧配置——install 前清掉旧 config，
    # 用最新模板重建默认配置，避免旧字段不兼容
    (etc/"ghlink").mkpath
    old_cfg = etc/"ghlink/config.json"
    old_cfg.delete if old_cfg.exist?
    tmpl = libexec/"config.example.json"
    (etc/"ghlink/config.json").write(tmpl.read) if tmpl.exist?
  end

  # v0.4.7（李工反馈：卸载后 etc/ghlink 残留，期望自动删除）：
  # brew uninstall 默认保留 etc/ 配置（防误删用户数据），这里显式清理 ghlink 自身配置。
  # 系统级残留（/var/lib/ghlink 状态/备份、LaunchDaemon）需 root，由 ghlink uninstall 处理。
  def uninstall
    rm_rf etc/"ghlink" if (etc/"ghlink").exist?
  end

  def caveats
    <<~EOS
      ghlink 已安装。使用步骤：
        1. 编辑配置: sudo vim #{etc}/ghlink/config.json
        2. 启用值守: sudo ghlink enable   （注册系统 LaunchDaemon，1 小时粒度，需 root 写 hosts）
        3. 查看状态: ghlink status
        4. 停用值守: sudo ghlink disable  （保留最后写入的 hosts IP 与配置，不再自动更新）
        5. 彻底卸载: sudo ghlink uninstall（停任务 + 还原 hosts + 删 /var/lib/ghlink，v0.4.1 起）
      brew uninstall 已自动清理 #{etc}/ghlink 配置（v0.4.7 起）。
      默认不自启（opt-in），enable 后才注册定时任务。
      注意：值守需 root 权限（写 /etc/hosts），请用 sudo ghlink enable。
    EOS
  end

  test do
    assert_match "ghlink", shell_output("#{bin}/ghlink --version")
  end
end
