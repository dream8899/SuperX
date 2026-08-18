# Errors

## [ERR-20260803-001] wechat_channels_public_resolver_cli

**Logged**: 2026-08-03T03:00:00Z
**Priority**: medium
**Status**: resolved
**Area**: infra

### Summary
微信视频号公开解析页在浏览器成功，但同域 CLI GET/POST 均在 20–30 秒超时。

### Error
`curl: (28) Connection timed out`

### Context
- 输入为单个公开 `weixin.qq.com/sph/` 分享链接。
- 浏览器单次查询能返回匹配的作者、标题和原始视频下载。
- 未传 Cookie、登录态或授权头，也未连续刷新。

### Suggested Fix
把公开解析页限制为浏览器单链接备用；CLI 超时立即停止，长期保存稳定分享 ID，不保存
临时签名媒体地址。批量模式需另行批准微信客户端本地捕获方案。

### Metadata
- Reproducible: yes
- Related Files: references/wechat-channels.md

### Resolution
- **Resolved**: 2026-08-03T03:00:00Z
- **Notes**: 已固化浏览器降级路径、停止条件与本地注册脚本。

---

## [ERR-20260803-007] github_git_transport_and_commit_newline

**Logged**: 2026-08-03T04:35:00Z
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
Git HTTPS 推送在当前环境静默中断；Git Data API 首次提交因消息末尾换行不同而产生不同 SHA。

### Error
- `git push` 未返回退出码，远端引用未前进。
- API 树 SHA 一致，但提交 SHA 不一致，因此门禁拒绝更新分支。

### Suggested Fix
停止重复 Git 重试；使用 GitHub API 时先校验父提交与树 SHA，并保留本地提交消息末尾换行。
只有 API 提交 SHA 与本地 SHA 完全一致时才以 `force=false` 更新引用。

### Metadata
- Reproducible: environment-dependent
- Related Files: .git
- Promotion: forbidden; transport-specific candidate only

### Resolution
- **Resolved**: 2026-08-03T04:35:00Z
- **Notes**: 远端与本地最终提交 SHA 完全一致。

---

## [ERR-20260803-006] api_push_temp_cleanup_rejected

**Logged**: 2026-08-03T04:20:00Z
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
Git Data API 备用推送命令因包含临时目录的 `rm` 清理而被安全策略拒绝。

### Error
`rm -f style commands are not permitted`

### Suggested Fix
API 请求改用内存 JSON 和标准输入，不创建或删除临时文件；不放宽删除策略。

### Metadata
- Reproducible: yes
- Related Files: .git
- Promotion: forbidden; environment-local implementation error only

### Resolution
- **Resolved**: 2026-08-03T04:20:00Z
- **Notes**: 后续改用纯内存 GitHub API 编排。

---

## [ERR-20260803-005] controlled_evolution_dot_path

**Logged**: 2026-08-03T04:00:00Z
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
受控进化守卫错误移除了 `.learnings/` 的前导点，候选区被误判为核心区。

### Error
`assertFalse(is_protected(".learnings/candidate.md"))` 失败。

### Suggested Fix
只逐段移除明确的 `./` 前缀，不使用会删除任意点和斜杠字符的 `lstrip("./")`。

### Metadata
- Reproducible: yes
- Related Files: scripts/controlled_evolution_guard.py
- Promotion: forbidden; implementation-local error only

### Resolution
- **Resolved**: 2026-08-03T04:00:00Z
- **Notes**: 三个 Skill 的守卫实现和回归测试已同步修正。

---

## [ERR-20260803-004] git_status_wrong_workdir

**Logged**: 2026-08-03T03:12:00Z
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
最终核对从媒体工作区而非 Skill 仓库运行 Git 命令。

### Error
`fatal: not a git repository`

### Suggested Fix
所有 SuperDown88 Git 命令显式使用 `git -C /absolute/skill/path` 或将工作目录设为 Skill
仓库；媒体目录核对与 Git 核对分开执行。

### Metadata
- Reproducible: yes
- Related Files: .git

### Resolution
- **Resolved**: 2026-08-03T03:12:00Z
- **Notes**: 已从正确仓库目录重新核对。

---

## [ERR-20260803-003] github_non_fast_forward

**Logged**: 2026-08-03T03:10:00Z
**Priority**: medium
**Status**: resolved
**Area**: infra

### Summary
推送前远端已出现等价的新提交，导致 `main` 非快进拒绝。

### Error
`[rejected] main -> main (fetch first)`

### Context
- 本地领先 4 个提交、落后远端 3 个提交。
- 远端 3 个提交与本地先前的资产中心提交内容等价，但哈希不同。

### Suggested Fix
禁止强推；先 fetch 和检查双方提交，再 rebase 到 `origin/main`。Git 会跳过等价提交，
只重放本次新增改动。

### Metadata
- Reproducible: no
- Related Files: .git

### Resolution
- **Resolved**: 2026-08-03T03:10:00Z
- **Notes**: 已安全 rebase，三个等价提交被自动跳过。

---

## [ERR-20260803-002] skill_quick_validate_missing_pyyaml

**Logged**: 2026-08-03T03:05:00Z
**Priority**: low
**Status**: resolved
**Area**: tests

### Summary
官方 `quick_validate.py` 在系统 Python 中因缺少 PyYAML 无法启动。

### Error
`ModuleNotFoundError: No module named 'yaml'`

### Context
- 所有功能单元测试已通过。
- 失败发生在校验器导入阶段，尚未读取 Skill。

### Suggested Fix
使用 `uv run --with pyyaml python .../quick_validate.py SKILL_DIR` 临时隔离运行，不修改
系统 Python。

### Metadata
- Reproducible: yes
- Related Files: SKILL.md

### Resolution
- **Resolved**: 2026-08-03T03:05:00Z
- **Notes**: 改用 uv 临时依赖执行校验。

---
