# SuperMedia 资产中心使用指南

## 前置条件

- 已安装 Python 3.10+。
- `Video_Download` 目录已有渠道/博主/`metadata.tsv` 的标准结构。

本工具只使用 Python 标准库和现有 Catalog 脚本，不需要 Node、浏览器扩展或 AI Agent。

## 一键更新

### macOS

在 Finder 双击 `scripts/update-supermedia.command`。首次若被系统阻止，可在终端执行：

```bash
chmod +x /Users/solo/.codex/skills/superdown88/scripts/update-supermedia.command
SUPERMEDIA_ROOT="/Users/solo/Desktop/AI工作室/Video_Download" \
  /Users/solo/.codex/skills/superdown88/scripts/update-supermedia.command
```

### Windows

双击 `scripts\\update-supermedia.cmd`。若素材根目录不在当前目录下，先设置：

```bat
set SUPERMEDIA_ROOT=D:\\Video_Download
scripts\\update-supermedia.cmd
```

### Linux / 通用终端

```bash
SUPERMEDIA_ROOT="/absolute/Video_Download" sh scripts/update-supermedia.sh
```

更新完成后会输出备份路径、扫描渠道、审计结论和报表路径。首次扫描会计算哈希，后续更新复用未变化文件的哈希缓存。

管理台的“在库文件”是当前磁盘上仍存在的去重资产数，删除素材后会在下一次更新下降；
“历史资产”保留已删除文件的哈希与发布血缘，用于审计，因此不会因删除而减少；“已删除路径”
显示同步发现的历史路径数。

## 打开本地管理台

```bash
python3 /Users/solo/.codex/skills/superdown88/scripts/supermedia_console.py \
  --root "/Users/solo/Desktop/AI工作室/Video_Download" serve --open
```

默认地址为 `http://127.0.0.1:8765`。关闭运行此命令的终端，管理台即停止；端口占用时添加 `--port 8766`。
macOS 的“打开资产中心”启动器会先复用已运行的 `8765` 服务并直接打开浏览器，不会再启动
第二个服务。

## HOLD 血缘处理

1. 在 HOLD 列表点击“关联来源”。
2. 查看文件路径、代表帧和历史处理报告，确认它对应的原始作品。
3. 填入 `source_key`，例如 `instagram:DaBCJx9CdIU`，以及核验依据。
4. 提交后资产标记为 `manual_reviewed`，并保留审计事件。

不确定来源时不要关联；保持 HOLD 才能阻止后续误上传。

## 恢复与排错

- 更新前数据库备份位于 `Video_Download/.supermedia/backups/`。
- 最近更新记录位于 `Video_Download/.supermedia/reports/last_update.json`。
- 文件缺失或数据库异常时先停止上传，再运行：

```bash
python3 scripts/media_asset_catalog.py --root "/absolute/Video_Download" audit
```

- 不要用 Excel 或 SQLite 浏览器直接修改数据库；请使用管理台或 `link-asset` 命令。
