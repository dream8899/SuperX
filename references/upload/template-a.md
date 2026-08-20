# 模板 A：账号 A 的目录草稿发布

适用于已完成一次 `sau tencent login --account <account> --headed` 扫码、且 `sau tencent check --account <account>` 返回 `valid` 的视频号账号。模板使用 `sau` 的账号专属持久化 Chrome Profile；普通 Chrome 标签只用于人工查看，不能替代自动上传 Profile。

## 一键执行

在 `social-auto-upload` 项目根目录运行：

```bash
uv run python /Users/solo/.agents/skills/super-upload/scripts/template_a.py \
  --directory "/绝对路径/待发布视频目录" \
  --account "账号A" \
  --delay-min-seconds 60 \
  --delay-max-seconds 180 \
  --execute
```

脚本会扫描常见视频格式、生成不可覆盖的 JSON 清单、先验证登录态，再通过一个可见 Chrome 窗口顺序保存草稿。每次只点击一次“保存草稿”，等待 10 秒后进入草稿箱按主标题核验；相邻的**已确认**草稿之间随机等待 10–20 秒。整批完成后返回视频号平台主页并保持窗口打开。

执行前会调用 SuperMedia 账本进行查重和原子预约。相同成品已在目标账号时直接
停止；同一源作品的其他 Remix 已在目标账号时，必须由用户确认后添加
`--allow-source-repeat`。成功后回写 `draft_saved_verified`；批次中断则写入
`status_unknown`，禁止自动重跑。

首次配置账号时先运行以下命令并扫码一次：

```bash
uv run sau tencent login --account "账号A" --headed
uv run sau tencent check --account "账号A"
```

Profile 位于本地 `profiles/tencent/`，包含敏感登录状态，禁止提交 Git、复制或分享。同一账号一次只能运行一个登录、检查或上传任务。

首次使用可去掉 `--execute`，仅生成并检查清单。确认标题和文案后，再使用同一目录重新执行带 `--execute` 的命令。

## 单条测试

先测试指定文件，不会扫描并上传目录中的其他视频：

```bash
uv run python /Users/solo/.agents/skills/super-upload/scripts/template_a.py \
  --directory "/绝对路径/待发布视频目录" \
  --account "账号A" \
  --include "001.mp4" \
  --copy-json "/绝对路径/copy.json" \
  --execute
```

`--include` 必须是目录中的精确文件名，可重复传入多次。若遗漏或拼错文件名，脚本会在登录前停止。

## 自定义文案

未传文案时，脚本从文件名生成简短标题，并使用“分享本期视频内容，欢迎关注。”。若需要逐条文案，创建 JSON 文件：

```json
{
  "001.mp4": {
    "title": "本期模型制作过程",
    "desc": "从零开始完成模型细节制作。",
    "tags": ["模型制作", "手工"],
    "short_title": "模型制作过程"
  }
}
```

然后追加 `--copy-json /绝对路径/copy.json`。未列出的文件仍使用默认文案。短标题会过滤特殊字符；不要使用表情、连字符或下划线。

## 完成与边界

- 每条任务只有在草稿箱出现对应主标题后才进入下一条等待；保存按钮被点击或出现提示都不代表完成。
- 状态未知的条目不能自动重跑，必须先确认不存在同名草稿。
- 不安排每 4–6 小时刷新或点击页面来模拟在线。下次批量任务前运行一次 `sau tencent check` 即可。
- 同一账号一次只能运行一个模板 A 批次；不要同时启动其他视频号上传窗口。
- 无法唯一关联平台原生作品 ID 的文件会进入 HOLD，不得绕过账本直接上传。
- 位置字段默认不显示地址：上传页「短标题」后的位置保持为空，不附加任何地理位置；
  只有用户明确要求时才填写。
