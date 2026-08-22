"""内置 GitHub520 兜底数据（v0.3.0 新增，李工 2026-08-22 定）。

背景：新安装/首次拉取失败时若无缓存，ghlink 拿不到任何社区 IP →
      GitHub520 段为空、非核心域名无 hosts 条目。内置一份最新快照兜底，
      保证首装断网/拉取失败也能直接可用。

更新规则：每次发版前抓取 https://raw.hellogithub.com/hosts 更新本文件
（数据仅含非核心域名——核心域名永远由动态自愈兜底，不写死社区 IP）。
"""

# 快照时间：2026-08-22T07:51:33+08:00（来源 raw.hellogithub.com/hosts）
BUILTIN_GITHUB520: dict = {
    "avatars.githubusercontent.com": ["185.199.111.133"],
    "avatars0.githubusercontent.com": ["185.199.111.133"],
    "avatars1.githubusercontent.com": ["185.199.111.133"],
    "avatars2.githubusercontent.com": ["185.199.111.133"],
    "avatars3.githubusercontent.com": ["185.199.111.133"],
    "avatars4.githubusercontent.com": ["185.199.111.133"],
    "avatars5.githubusercontent.com": ["185.199.111.133"],
    "camo.githubusercontent.com": ["185.199.111.133"],
    "central.github.com": ["140.82.114.22"],
    "cloud.githubusercontent.com": ["185.199.111.133"],
    "codeload.github.com": ["20.205.243.165"],
    "collector.github.com": ["140.82.114.22"],
    "desktop.githubusercontent.com": ["185.199.111.133"],
    "education.github.com": ["140.82.114.21"],
    "favicons.githubusercontent.com": ["185.199.111.133"],
    "gist.github.com": ["159.106.121.75"],
    "github.global.ssl.fastly.net": ["203.111.254.117"],
    "github.githubassets.com": ["185.199.110.215"],
    "github.map.fastly.net": ["185.199.111.133"],
    "live.github.com": ["140.82.112.26"],
    "media.githubusercontent.com": ["185.199.111.133"],
    "objects.githubusercontent.com": ["185.199.111.133"],
    "pipelines.actions.githubusercontent.com": ["13.107.42.16"],
    "private-user-images.githubusercontent.com": ["185.199.111.133"],
    "raw.githubusercontent.com": ["185.199.111.133"],
    "user-images.githubusercontent.com": ["185.199.111.133"],
}
