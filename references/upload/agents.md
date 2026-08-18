# 各类 Agent 的调用约定

## 统一规则

- Agent 进入仓库后先运行 `uv run sau --help` 与目标平台的 `--help`。
- 优先 CLI，不要从网页抓取 cookie、不要在日志里回显 cookie。
- 碰到扫码、验证码、平台安全验证时暂停自动化并把二维码/页面交给账号所有者完成。
- 需要可视化确认时使用 `--headed`；批量视频号任务不要自动关窗。

## Codex / Claude Code / 终端型 Agent

将项目设为工作目录，执行安装参考中的命令。Skill 的安装目录取决于客户端：Codex 常用用户级 skills 目录，Claude Code 可使用仓库内 `.claude/skills/`。不确定目录时，直接在仓库执行 `uv run sau ...`，不要猜测或覆盖用户已有 skills。

已确认的常见放置位置如下；复制或软链接时要保留整个 `super-upload/` 目录及其 `references/`：

| Agent | 常见位置 |
| --- | --- |
| Codex | `${CODEX_HOME:-~/.codex}/skills/super-upload` |
| Claude Code（项目级） | `<项目根目录>/.claude/skills/super-upload` |
| OpenClaw | `~/.openclaw/skills/super-upload` |

其他 agent 以其自身的 skill 注册目录为准；不确定时不要覆盖已有目录或假设它能自动发现本地文件。

推荐任务顺序：安装 → `--help` → `login` → `check` → 单条草稿 → 批量草稿 → 用户确认后公开发布。

## OpenClaw 与其他可执行命令的 Agent

给 agent 提供仓库工作目录和本 skill；要求它使用 `uv` 与 `sau`，并要求它展示本地二维码而非只报路径。若运行环境是远程无桌面主机，先确认是否有受信任的图形桌面与扫码交互通道；没有则不要承诺能完成视频号登录。

## Kimi WebBridge / Computer Use

它们适合作为真实浏览器的**辅助层**：

1. 先由 `sau` 打开并保持登录/上传页面。
2. 仅在 CLI 因页面元素改版、封面裁切或人工确认步骤卡住时，使用浏览器桥接观察和完成该步骤。
3. 不用桥接去循环点击“保存草稿”或“发表”，也不在每个任务后关闭浏览器。
4. 完成后回到 CLI 的状态检查或草稿列表确认结果。

浏览器桥接无法替代可复现的 CLI；对批量任务，CLI 的 JSON 清单和单一持久会话才是默认路径。

Kimi WebBridge 需要 Chrome 扩展和本地 daemon 已就绪。macOS/Linux 可安全执行 `~/.kimi-webbridge/bin/kimi-webbridge start` 以启动 daemon；若扩展仍未连接，保留页面给用户处理，不要尝试停止、重启或卸载 daemon。桥接优先用语义快照定位元素，避免坐标点击。
