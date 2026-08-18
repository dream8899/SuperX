# 微信视频号下载路径

## 已验证结论（2026-08-03）

- `weixin.qq.com/sph/<ID>` 会跳转到 Finder 预览页；当前 `yt-dlp 2026.07.04`
  不支持该 URL。
- 公开预览接口可返回作者、标题、日期、互动数、封面和 `dynamicExportId`，但不返回
  可下载视频流；普通 Chrome 打开官方 feed 深链会转到微信升级提示。
- `wx_channels_download` 项目的公开单链接解析页可在浏览器中解析分享链接并下载原始
  MP4。本次样本验证为 H.264 1080×1440 + AAC，8.382 秒。
- 同一解析接口从终端直连可能超时，因此它只能作为单链接浏览器备用，不得承诺纯 CLI
  稳定性，也不得用刷新循环恢复。

## 单链接低风险路径

1. 先用 `yt-dlp --ignore-config --no-cookies --simulate --no-playlist URL` 探测；明确
   `Unsupported URL` 后停止，不重试。
2. 在浏览器打开 `https://sph.litao.workers.dev/`，粘贴一个规范分享链接，查询一次。
3. 核对作者和文案，点击“下载原始视频”一次。不要复制、记录或复用页面展示的临时
   `finder.video.qq.com` 签名地址。
4. 用 `ffprobe` 验证本地文件，再运行：

```bash
python3 scripts/wechat_channels_register.py register \
  --share-url 'https://weixin.qq.com/sph/ID' \
  --downloaded-file '/absolute/download.mp4' \
  --root '/absolute/Video_Download' \
  --creator '视频号名称' --published YYYY-MM-DD --title '作品标题'
```

该脚本不联网，只把已验证文件纳入 `wechat_channels/<creator>/`，以 `sph` ID 去重，
更新 metadata、下载归档和渠道注册表。长期只保存规范分享链接和 ID。

## 批量与客户端路径

公开分享解析适合单条验证，不适合作为批量依赖。批量时使用开源桌面工具在微信客户端
播放阶段捕获媒体，例如 `ltaoo/wx_channels_download`、`putyy/res-downloader` 或
`lecepin/WeChatVideoDownloader`。这些工具通常会设置本地代理并安装根证书，属于系统级
安全变更：必须先取得用户明确授权，记录监听地址和证书名称，关闭其他敏感应用，按 5 条
试批，结束后退出工具、清除系统代理并移除其根证书。异常退出后必须人工确认代理已清除。

不要把微信登录 Cookie、客户端数据目录、会话存储、Authorization 头交给脚本。不要
把代理暴露到局域网或公网。不要绕过付费、私密、权限控制或 DRM；仅归档公开或用户有权
保存的内容。

## 降级与停止

- 在线解析超时：保留分享链接，停止；不要连续查询或刷新。
- 解析结果与页面作者/标题不一致：不下载。
- 下载文件无法通过 `ffprobe`：移入 quarantine，不写 metadata。
- 需要安装证书、提升权限或修改系统代理：先向用户说明影响和回滚，再等待确认。
- 没有浏览器能力：让用户人工下载单条原始视频，再从本地注册脚本继续；不要索要登录态。
