# 视频号成功路径与避坑

## 已验证的登录与草稿路径

1. 用 `--headed` 启动 `sau tencent login --account <name>`；首次扫码会创建账号专属持久化 Chrome Profile。
2. 二维码现在可能位于 `open.weixin.qq.com/connect/qrconnect` iframe；CLI 会尝试从 iframe 提取二维码。agent 应展示二维码图片给用户扫码。
3. 扫码完成后执行 `sau tencent check --account <name>`，只在输出 `valid` 时继续。
4. 先用 `--draft --keep-open` 上传一条，检查草稿是否实际出现。
5. 批量时使用 `upload-video-batch`，让同一个 Profile、context 和 page 顺序处理清单；不要每条视频新开浏览器。

## 单条草稿模板

```bash
uv run sau tencent upload-video \
  --account main \
  --file "/absolute/path/video.mp4" \
  --title "清晰且不含敏感符号的标题" \
  --desc "正文文案" \
  --tags "标签1,标签2" \
  --short-title "简短标题" \
  --draft --headed --keep-open
```

## 批量清单模板

```json
[
  {
    "file": "videos/001.mp4",
    "title": "第一条标题",
    "desc": "第一条文案",
    "tags": ["模型制作", "机车"],
    "short_title": "模型制作",
    "draft": true
  }
]
```

`file` 和 `title` 必填；`tags` 可用字符串或数组；`draft` 缺省时就是 `true`。仅当用户明确批准公开发布时才写 `"draft": false`。如需自动关闭共享窗口，再添加 `--close-when-done`。

批量命令默认 `--headed`、默认保留窗口；它在一个账号专属持久 Profile、context 和 page 中顺序处理清单。整批结束后关闭窗口才结束进程。仅在明确需要无人值守时才传 `--headless --close-when-done`。

## 避坑清单

- **保存草稿反复点击**：提交动作必须只点击一次；等待 10 秒后直接进入草稿箱，按主标题核验。核验成功后随机等待 10–20 秒再处理下一条；超时应报错并保留页面，不能继续点击。
- **普通 Chrome 无法自动选文件**：浏览器桥接可检查页面，但 Chrome 扩展通常不能向视频号子页面注入本地文件。自动上传必须使用 `sau` 的账号专属持久 Profile，首次扫码一次后长期复用。
- **Profile 并发占用**：同一账号一次只运行一个登录、检查或上传任务。窗口保持打开时不要启动同账号的新任务。
- **窗口风控**：批量任务默认保持同一可见窗口。人工关闭窗口前不要启动新的一批或新建多个 Chrome 实例。
- **二维码找不到**：先用 `--headed`，检查 iframe 和网络；二维码 `src` 可能是相对地址，不能只按 data URL 判断。
- **短标题报特殊字符**：使用中文、字母、数字和平台允许符号；连字符等特殊符号可能被拒绝。缺省时让 CLI 从主标题生成。
- **位置默认不显示地址**：上传页「短标题」后的「位置」字段保持为空，不显示任何地址；
  只有用户明确要求填写时才设置。
- **cookie 失效**：先执行 `check`，不要在失效状态下反复上传；重新扫码后再继续。
- **上传未处理完成**：保留窗口、检查错误提示与视频格式。不要把“上传按钮可点”误判成“草稿已保存”。
- **公开发布风险**：先草稿、再人工核验内容和账号；定时或公开发布需要用户明确授权。

## 完成判据

1. 草稿任务在单次保存后等待 10 秒，必须进入草稿箱确认对应主标题；按钮被点击或出现提示不代表成功。
2. 公开发表任务在仍保留的窗口确认作品管理页出现对应主标题。
3. 只在确认不存在同名草稿后重试失败条目；未知状态的条目不能自动重跑。
4. 需要时再运行 `uv run sau tencent check --account <account>` 验证更新后的本地登录态；不得导出账号状态文件。
