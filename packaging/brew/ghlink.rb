# Homebrew Formula: ghlink
# 用法: brew install ghlink（本 formula 由 brew tap liwmj/ghlink 提供）
# 参考: v0.2 安装包技术方案草案（brew 线：libexec + bin 入口 + launchd 模板）

class Ghlink < Formula
  desc "GitHub 链路自愈工具：主动监控连通性，异常时自动换 IP 写 hosts，自检回滚 + 多渠道告警"
  homepage "https://github.com/liwmj/ghlink"
  url "https://github.com/liwmj/ghlink/archive/refs/tags/v0.2.0.tar.gz"
  sha256 "REPLACE_WITH_V0.2.0_SHA256"
  license "MIT"
  head "https://github.com/liwmj/ghlink.git", branch: "master"

  depends_on "python@3.12"

  def install
    # 源码安装到 libexec（不污染系统 Python 环境）
    libexec.install Dir["src/ghlink/*.py"]
    libexec.install "config.example.json" => "config.example.json"

    # bin 入口
    (bin/"ghlink").write <<~EOS
      #!/bin/bash
      exec "#{Formula["python@3.12"].opt_bin}/python3" "#{libexec}/main.py" "$@"
    EOS
    chmod 0755, bin/"ghlink"

    # 配置目录
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
        2. 启用值守: sudo ghlink enable   （注册 LaunchDaemon，1 分钟粒度）
        3. 查看状态: ghlink status
        4. 停用值守: sudo ghlink disable
      默认不自启（opt-in），enable 后才注册定时任务。
    EOS
  end

  service do
    run [opt_bin/"ghlink", "run", etc/"ghlink/config.json"]
    keep_alive false
    run_type :interval
    interval 60
    log_path var/"log/ghlink.log"
    error_log_path var/"log/ghlink.log"
  end

  test do
    assert_match "ghlink", shell_output("#{bin}/ghlink --version")
  end
end
