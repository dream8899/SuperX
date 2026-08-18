# 即梦（Jimeng）批量视频生成

即梦是视频**生产**环节（sau 上传前的上游）；`sau` 不支持即梦，用 Kimi WebBridge 控制
已登录即梦的 Chrome 执行。工作流与上传一样坚持：草稿/生成前授权、批量前积分计划、
防重复、失败隔离、本地敏感数据不外发。

## 前置

- Kimi WebBridge daemon 运行（`~/.kimi-webbridge/bin/kimi-webbridge start`），Chrome 已登录即梦。
- 主题目录约定（MORPHWORKS 或同类）：`系列容器/<主题>_YYYYMMDD/` 内含
  `meta.yaml`（id/status/series/publish）、`frame_start.png`、`frame_end.png`、
  `内容包.md`（Core/Upgraded/Signature 三档“最终视频提示词”）、`state.json`、`publish.md`。
- 全局账本 `_ACCOUNT_BOOK.csv`：id,theme,date,video_filename,status,credits,platform_published。

## 工具

`scripts/jimeng_video_batch.py`，子命令：

```bash
python3 scripts/jimeng_video_batch.py plan [--variant core|upgraded|signature]
python3 scripts/jimeng_video_batch.py prepare --id <id>
python3 scripts/jimeng_video_batch.py generate
python3 scripts/jimeng_video_batch.py poll
python3 scripts/jimeng_video_batch.py run-one
python3 scripts/jimeng_video_batch.py run-pipeline            # 跨会话流水线：进入生产即提交下一条
python3 scripts/jimeng_video_batch.py run-batch-conv --batch-size 10   # 每 N 条共用一个会话
```

默认：Seedance 2.0 mini · 10s · 比例跟随内容包（首尾帧模式锁定“自动匹配”，输出跟随图片
比例，不一致会告警）· 视频回写来源文件夹，命名 `YYYYMMDD_主题_未发布.mp4`。

## 铁律（必须遵守）

1. 批量前先 `plan` 输出积分统计（余额、每条成本，页面当时显示为准），**取得明确授权才
   自动点「生成」**；否则默认人工确认。
2. 提交后必须校验：URL 出现数字 workspace 且积分扣减（`_verify_submitted`），未确认不继续。
3. 下载前 MD5 查重（`seen_md5`），重复即拒绝归档；下载地址是签名直链会轮换，**拿到即下载**。
4. 上传图片用 base64→File→DataTransfer 注入（WebBridge `upload`/CDP 文件注入被扩展全局
   拦截）；**不要点击上传槽**，避免弹出 macOS 原生 Finder 对话框滞留。
5. 同会话批量：导航回会话后模式会被重置为“全能参考”，必须先切回首尾帧再清空输入区；
   会话是虚拟列表，**按 60 字唯一提示词标记定位轮次**，不能按“最后一张卡片”。
6. 断点续跑：`.jimeng_batchconv.json` / `.jimeng_pipeline.json` 记录已提交条目，防止
   重复提交；失败条目隔离记录（reason），不阻塞整批，错误文件保留在 `/tmp/jimeng_dl/wrong/`。
7. 发布仍走本 Skill 上传流程；`meta.yaml` publish 与 `publish.md` 标记状态。

## 页面机制备忘

- 视频生成页：`https://jimeng.jianying.com/ai-tool/generate/?type=video`（直达 `/ai-tool/video/generate` 会跳首页）。
- 模式下拉：全能参考 / 首尾帧 / 智能多帧 / 智能编辑 / 超长视频；模型：Seedance 2.5 / 2.0 mini / Fast / VIP 等。
- 首尾帧输入区是 `textarea.prompt-textarea`（占位符提示首帧/尾帧过渡）；上传后两个槽各一张预览图。
- 积分显示：提交按钮旁 `.actual-credits`（如 60）与 `.original-credits`（如 90，划线原价）；
  容器文本可能是“6090”拼接，读子元素。余额：`.credit-amount-text`。
- 轮询完成标志：该轮 record（`record-*`，不含 header/list）内有真实视频
  （排除 `loading-animation`/`record-loading` 占位视频）。
- popover（时长/比例面板）用 Escape 或 `document.body.click()` 关不掉，点击输入区可收起。
