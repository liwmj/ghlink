# ghlink 验收清单（ACCEPTANCE-CHECKLIST）

> 维护角色：技术角色（R-TCH-01，赛博主审）+ 项目经理（R-OPS-02，顾笙）
> 用途：发版前验收防回归的固定用例清单；每次发版按本清单逐项执行，结果回填。
> 关联：docs/SMOKE-TEST.md（真机冒烟记录）、docs/PLATFORM-RESULTS.md（平台矩阵回填）、docs/PROJECT-INFO.md（项目信息）

## 验收纪律（2026-08-26 新增，赛博定）

- **验收一律基于正式版 / 远端 HEAD**：验收对象必须是 tap 远端 HEAD（`git fetch && git status` 干净）或 GitHub Release 正式制品。
- **绕过版仅限排障**：本地临时改动的「验收绕过版」只允许用于定位问题，**禁止进入验收结论**；验收前必须 `git checkout -- <file>` 还原并确认与远端 HEAD 一致。
- 验收结果以「正式版复验」为准，绕过版期间产生的中间结论一律作废重测。

## 一、pkg 链路（macOS 真机）

| # | 用例 | 断言 | 结果 |
|---|------|------|------|
| 1 | pkg 安装 | 版本 0.4.22；symlink + sudoers 自动就位；sudo -n 免密直出 | ✅ 2026-08-26 |
| 2 | Dock 隐藏 | LSUIElement=true，无 python 图标 | ✅ 2026-08-26 |
| 3 | 单实例锁 | 连开 3 次（open 真实路径），最终托盘进程 1 个 | ✅ 2026-08-26（3/3 成功） |
| 4 | 值守开启 | LaunchDaemon running（runs≥5）；hosts 自动备份；status=normal | ✅ 2026-08-26 |
| 5 | 重启持久 | 真实重启后 plist 仍在、值守自动拉起 | ⏳ 待李工真实重启确认（不阻塞发版） |

## 二、brew 链路（Cask，2026-08-26 缺陷修复后复验）

> 背景：2026-08-25 发现本地 tap 为「验收绕过版」（uninstall script 钩子缺失），brew uninstall 后残留 4 处 + 值守未停。
> 修复：本地 tap 作废绕过版，公式对齐远端 HEAD（uninstall 钩子已在远端），本地不留旁路版本。

| # | 用例 | 断言 | 结果 |
|---|------|------|------|
| B1 | tap 对齐 | 本地 tap 与远端 HEAD 一致（git status 干净，Casks/ghlink.rb 含 uninstall script 钩子 + zap） | ✅ 2026-08-26 |
| B2 | brew 安装 | `brew install --cask ghlink`：版本 0.4.22；/usr/local/bin/ghlink symlink 就位；/etc/sudoers.d/ghlink 写入；sudo -n /usr/local/bin/ghlink 免密直出 | ✅ 2026-08-26 |
| B3 | 值守就位 | `sudo ghlink enable` 后 LaunchDaemon 托管、ghlink status=normal | ✅ 2026-08-26 |
| B4 | brew 卸载 | `brew uninstall --cask ghlink`：值守停止（LaunchDaemon 移除）；hosts 还原（ghlink 段落清零）；sudoers.d 清理；/etc/ghlink 配置删除；pkgutil receipt 注销 | ✅ 2026-08-26 |
| B5 | 零残留复验 | 卸载后 9 项全查：symlink / app / pkgutil / sudoers / LaunchDaemon / 配置目录 / hosts 段落 / brew 记录 / 进程 全零残留 | ✅ 2026-08-26 |

## 三、平台矩阵（三平台 × 场景）

> 详细断言见 docs/PLATFORM-RESULTS.md（Linux Ubuntu / Windows Server 2022 / macOS CI runner 真机实测，E-001~E-006 + 权限 + DNS 刷新）。

- 单测：三平台全量用例全绿（51→60+，随版本增长）
- 真机冒烟：Ubuntu + Windows 双平台人工真机 + macOS CI runner 自动化
- 回归：存量用例发布前全跑
