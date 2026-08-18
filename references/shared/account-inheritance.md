# 账号继承与迁移

SuperX 不复制、不重建已有账号，而是直接指向原来的账号资产路径。上传与即梦流程
继承同一份登录态，无需重新扫码或重新配置。

## 视频号（tencent）

账号数据全部位于 `sau` 仓库（默认 `social-auto-upload`）：

- `conf.py`：平台账号与 Chrome 路径配置
- `cookies/tencent_uploader/`：按账号命名的 cookie 文件
- `profiles/tencent/`：账号专属持久 Chrome Profile（如 `tencent_main`、
  `tencent_mowan`、`tencent_梦到消消乐` 等）

`superx upload` 直接 `uv run --project <sau仓库> sau ...`，因此账号、Profile 与
登录态原样生效。查看继承到的账号：

```bash
python3 "$SKILL_DIR/superx.py" accounts
```

`superx doctor` 也会列出视频号账号数量与即梦账本。

## 即梦（Jimeng）

- 工作区账本：`VideoHub/<工作区>/_ACCOUNT_BOOK.csv`（默认根目录
  `/Users/solo/Desktop/AI工作室/VideoHub`，可用 `SUPERX_VIDEOHUB_ROOT` 覆盖）
- 浏览器登录态：复用已登录的 Chrome（Kimi WebBridge），不另存密码
- 生成流程：`superx upload` 之外使用 `scripts/jimeng_video_batch.py`
  （plan/prepare/generate/poll），回写账本与来源文件夹

## 路径覆盖

换机器或目录变更时，用环境变量覆盖默认路径，无需改代码：

```bash
export SUPERX_SAU_REPO="/绝对路径/social-auto-upload"
export SUPERX_VIDEO_ROOT="/绝对路径/Video_Download"
export SUPERX_VIDEOHUB_ROOT="/绝对路径/VideoHub"
```

## 迁移到新机器

1. 复制整个 `social-auto-upload` 目录（含 `conf.py`、`cookies/`、`profiles/`、
   `batches/`、`logs/`），保持相对结构不变。
2. 复制 `Video_Download` 与 `VideoHub` 工作区。
3. 在新机器运行 `installer/install_macos.sh` 或 `install_windows.ps1`，
   再 `superx accounts` 确认账号已继承。
4. 视频号若检测到 Profile 失效，只需对对应账号重新扫码一次，其余账号不受影响。
