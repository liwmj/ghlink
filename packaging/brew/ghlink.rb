# Homebrew Formula: ghlink
# 用法: brew install ghlink（本 formula 由 brew tap liwmj/ghlink 提供）
# 参考: v0.2 安装包技术方案草案（brew 线：libexec + bin 入口 + launchd 模板）
#
# 修复记录（赛博接口复核 2026-08-14）：
# P1: 相对导入入口 —— 安装保持包结构 libexec/ghlink/，bin wrapper 用绝对导入
# P2: service 块移除 —— 用户级 launchd 写 /etc/hosts 会失败，官方值守统一走 ghlink enable

class Ghlink < Formula
  desc "GitHub 链路自愈工具：主动监控连通性，异常时自动换 IP 写 hosts，自检回滚 + 多渠道告警"
  homepage "https://github.com/liwmj/ghlink"
  url "https://github.com/liwmj/ghlink/archive/refs/tags/v0.2.0.tar.gz"
  sha256 "426b9dcfcbf171bb9ad17a07e4587397e84d93e24ccf564a6faa3f9b421c8384"
  license "MIT"
  head "https://github.com/liwmj/ghlink.git", branch: "master"

  depends_on "python@3.12"

  def install
    # 保持包结构安装到 libexec/ghlink/（解决 main.py 相对导入）
    (libexec/"ghlink").install Dir["src/ghlink/*.py"]
    libexec.install "config.example.json"

    # bin 入口：绝对导入 wrapper（仿 ghlink_entry.py）+ PYTHONPATH 注入 libexec
    (bin/"ghlink").write <<~EOS
      #!/bin/bash
      export PYTHONPATH="#{libexec}"
      exec "#{Formula["python@3.12"].opt_bin}/python3" -c "from ghlink.main import main; import sys; sys.exit(main())" "$@"
    EOS
    chmod 0755, bin/"ghlink"

    # 配置目录（默认不自启，enable 时才注册系统 LaunchDaemon）
    (etc/"ghlink").mkpath
    (etc/"ghlink/config.json").write <<~EOS unless File.exist?(etc/"ghlink/config.json")
      {
        "probe": { "targets": ["github.com", "api.github.com"], "timeout_sec": 5 },
        "trigger": { "consecutive_failures": 3, "cooldown_min": 15, "verify_success_rounds": 2 },
        "resolver": { "doh_sources": [], "cache_ttl_sec": 3600, "max_candidates": 5 },
        "notify": { "enabled": false },
        "state_file": "/var/lib/ghlink/ghlink_status.json",
        "lock_file": "/var/lib/ghlink/ghlink.lock",
        "hosts_backup_dir": "/var/lib/ghlink/backup"
      }
    EOS
  end

  def caveats
    <<~EOS
      ghlink 已安装。使用步骤：
        1. 编辑配置: sudo vim #{etc}/ghlink/config.json
        2. 启用值守: sudo ghlink enable   （注册系统 LaunchDaemon，1 分钟粒度，需 root 写 hosts）
        3. 查看状态: ghlink status
        4. 停用值守: sudo ghlink disable
      默认不自启（opt-in），enable 后才注册定时任务。
      注意：值守需 root 权限（写 /etc/hosts），请用 sudo ghlink enable。
    EOS
  end

  test do
    assert_match "ghlink", shell_output("#{bin}/ghlink --version")
  end
end
