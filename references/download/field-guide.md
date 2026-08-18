# 成功经验与避坑指南

## 已沉淀的有效做法

- **发现与下载分离**：浏览器或扩展仅发现规范 URL；yt-dlp 以无 Cookie 方式独立下载。
  这既减少账号风险，也让 CLI 和其他 Agent 能无会话接续。
- **短码优先**：Instagram 的 shortcode/规范 post URL 是稳定主键；临时 CDN URL 会失效，
  不能作为清单、状态或跨机器交接依据。
- **本地脚本承担重复工作**：URL 规范化、去重、归档、ffprobe 验证和 registry 重建都交给
  脚本，避免在对话中逐条处理或一次提交数百条任务。
- **文件名解析要有边界**：先剥离博主目录名带来的前缀，再识别短码；对
  `reel-序号_短码`、`__h264-aac`、`__fdash` 等后处理命名做专门清理。短码允许
  `B/C/D` 开头、内部下划线或末尾连字符，不能简单假设以 `D` 开头。
- **顶层源文件与衍生文件分离**：只统计 `CREATOR/*.mp4`，不递归进入 remix、发布、审核
  或 quarantine 子目录。重复源文件移动到隔离区，保留可恢复证据。
- **metadata 兼容演进**：推荐 12 列（含规范视频地址），下载器同时兼容旧四列；短码集合
  必须来自 metadata、归档和顶层文件名的并集。metadata 按发布日期倒序，日期缺失时用
  下载时间排序，便于最新作品优先。
- **快速盘点与完整验证分层**：大型归档先用非空文件完成去重和清单重建，注册表记录
  `verification_basis=nonempty-file`；需要媒体完整性时显式运行 `--verify`，避免每次更新都
  被全量 ffprobe 拖慢。
- **先 5 后 20**：五条全部成功才升到最多二十条；串行、零重试、第一条错误即停，比大批量
  重投更高效且更可恢复。
- **真实数量以证据为准**：主页显示数只是上限。分别记录已发现、唯一 ID、已验证、明确不可
  访问和未暴露估计，避免为补齐已删除/隐藏作品反复访问。
- **iGram 的可借鉴点**：解析与传输分离是正确架构；但不依赖其未公开 API。yt-dlp 已提供
  解析、info JSON、下载归档、更新和媒体传输，应优先复用。

## 常见失败与处理

| 症状 | 分类 | 正确处理 | 不要做 |
|---|---|---|---|
| 429、checkpoint、challenge、验证码 | rate_limit/challenge | 停止来源，记录并等待状态冷却 | 刷新、改 UA、代理轮换、Cookie 导出 |
| 页面空白、临时超时、网络失败 | transient | 保留 manifest，至少冷却 15 分钟 | 自动立即重跑、反复重开主页 |
| 页面显示数大于发现数 | not-discovered-or-unavailable | 记录差额估计，等待后续增量 | 假定必有漏项并无限滚动 |
| 作品 URL 已发现但媒体为空/403/404 | inaccessible-or-deleted | 冷却后最多一次单条复核，仍失败则终态记录 | 放回大队列循环重试 |
| yt-dlp 写入归档但媒体失败 | provisional archive | 移除该条临时归档行，保留错误日志 | 将其视为已成功、或立即批量重投 |
| FFmpeg 后处理失败、DASH 中间文件缺失 | postprocess failure | 单条以原生 MP4 merge 重试一次并验证 | 判为删除、或递归转码原文件 |
| 浏览器工具断开/桥超时 | discovery adapter failure | 保存已发现 URL，换人工/其他发现器 | 猜测协议或用桥循环抓 CDN |
| 文件数量高于作品数量 | duplicate/derivative | 以唯一 ID + ffprobe 的顶层源文件计数 | 把 remix、审核、修复副本计入下载量 |
| 账号名被识别成短码前缀 | parser-boundary | 先移除 `CREATOR_`/`creator-reel-序号_` 前缀，再做短码匹配 | 只用“最后一个下划线”或假设短码以 D 开头 |
| metadata 有新列导致旧脚本失败 | compatibility | 让解析器按表头读取 12 列，同时保留四列回退 | 直接改列顺序而不更新下载器 |
| 视频号分享页 yt-dlp 报 Unsupported URL | extractor | 单次浏览器解析并下载本地文件；批量需经批准的客户端捕获 | 导出微信 Cookie 或循环刷新分享页 |
| 视频号解析页浏览器成功但 CLI 超时 | network-path | 保留浏览器单条路径，不承诺纯 CLI；立即校验并登记稳定分享 ID | 反复 curl 或把签名 CDN URL写入 metadata |

## 完成与复盘清单

完成一轮前确认：每个当前可发现的可访问 ID 都有验证文件；终态失败有类别；metadata、
README、archive、batch log、registry 同步；无活动下载或临时文件；新经验只写为隔离
候选，不在运行任务中写回 Skill。平台更新后先升级 yt-dlp、跑测试和一条公开试下载；若仍
需改规则，另开受控维护任务，按 `controlled-evolution.md` 审批、测试和回滚。
