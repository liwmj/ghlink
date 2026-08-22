"""内置 GitHub520 兜底数据（v0.3.0 新增，李工 2026-08-22 定）。

背景：新安装/首次拉取失败时若无缓存，ghlink 拿不到任何社区 IP →
      GitHub520 段为空、非核心域名无 hosts 条目。内置一份最新快照兜底，
      保证首装断网/拉取失败也能直接可用。

格式：hosts 文本快照（与 raw.hellogithub.com/hosts 同构），由
      github520.parse_hosts() 解析——数据即字符串，避免大字典触发
      SonarCloud 重复代码块误报（D 级质量门禁）。

更新规则：每次发版前抓取 https://raw.hellogithub.com/hosts 更新本文件。
核心域名（github.com/api.github.com）仍由动态自愈兜底，解析时剔除。
"""

# 快照时间：2026-08-22T07:51:33+08:00（来源 raw.hellogithub.com/hosts）
BUILTIN_GITHUB520_HOSTS = """140.82.113.25 alive.github.com
20.205.243.168 api.github.com
140.82.114.21 api.individual.githubcopilot.com
185.199.111.133 avatars.githubusercontent.com
185.199.111.133 avatars0.githubusercontent.com
185.199.111.133 avatars1.githubusercontent.com
185.199.111.133 avatars2.githubusercontent.com
185.199.111.133 avatars3.githubusercontent.com
185.199.111.133 avatars4.githubusercontent.com
185.199.111.133 avatars5.githubusercontent.com
185.199.111.133 camo.githubusercontent.com
140.82.114.22 central.github.com
185.199.111.133 cloud.githubusercontent.com
20.205.243.165 codeload.github.com
140.82.114.22 collector.github.com
185.199.111.133 desktop.githubusercontent.com
185.199.111.133 favicons.githubusercontent.com
159.106.121.75 gist.github.com
16.15.237.95 github-cloud.s3.amazonaws.com
52.216.62.41 github-com.s3.amazonaws.com
16.15.223.74 github-production-release-asset-2e65be.s3.amazonaws.com
54.231.229.17 github-production-repository-file-5c1aeb.s3.amazonaws.com
52.217.64.212 github-production-user-asset-6210df.s3.amazonaws.com
192.0.66.2 github.blog
20.205.243.166 github.com
140.82.113.18 github.community
185.199.110.215 github.githubassets.com
203.111.254.117 github.global.ssl.fastly.net
185.199.111.153 github.io
185.199.111.133 github.map.fastly.net
185.199.111.153 githubstatus.com
140.82.112.26 live.github.com
185.199.111.133 media.githubusercontent.com
185.199.111.133 objects.githubusercontent.com
13.107.42.16 pipelines.actions.githubusercontent.com
185.199.111.133 raw.githubusercontent.com
185.199.111.133 user-images.githubusercontent.com
150.171.110.104 vscode.dev
140.82.114.21 education.github.com
185.199.111.133 private-user-images.githubusercontent.com
"""
