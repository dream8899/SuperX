---
name: SuperX
description: 统一短视频工作流——下载归档（Instagram/抖音/视频号等）、清理翻新与去重（normalize/dedupe/拆片/裁切/文案）、多平台上传（sau CLI，草稿优先），并接管统一的 SuperMedia 账本与本地资产中心。Use when the user needs to download, clean/remix, or upload short videos, or manage the shared asset catalog.
---

# SuperX

SuperX 把 `superdown88`（下载）、`super-video-mix`（混剪/清理）、`super-upload`（上传）
合并为一套入口。统一入口是 `superx`，三个方向保留各自 CLI 契约，账本只有一个。

## 入口

```bash
python3 "$SKILL_DIR/superx.py" doctor      # 环境自检
python3 "$SKILL_DIR/superx.py" download ... # 下载/发现/归档（safe_social_archiver）
python3 "$SKILL_DIR/superx.py" mix ...      # 混剪/清理/验证（video_pipeline）
python3 "$SKILL_DIR/superx.py" upload ...   # 上传/草稿（sau / template-a）
python3 "$SKILL_DIR/superx.py" ledger ...   # 统一账本（media_asset_catalog）
python3 "$SKILL_DIR/superx.py" console ...  # 资产中心（supermedia_console）
```

把 `SKILL_DIR` 设为本 Skill 目录。`superx doctor` 会检查 Python、FFmpeg/ffprobe、
yt-dlp、uv、Chrome、`sau` 仓库与 `Video_Download` 根目录。

## 三条流水线的边界（不可合并为一句话）

### 下载（download）

只用 yt-dlp 公开传输（`--ignore-config --no-cookies`）；浏览器仅用于发现规范作品 URL，
绝不导出 cookie / 临时 CDN URL / 本地存储。匿名发现 403 时，用授权浏览器分页发现短码，
下载仍走公开路径。单条先试点，再按每批 ≤20 串行推进；失败先冷却，不重试风暴。

详见 `references/download/field-guide.md` 与 `references/download/installation-and-agent-adapters.md`。

### 混剪（mix）

先分析出 JSON 计划，人工审批后才执行；输出不得与源文件同路径、不覆盖成品。用户未要求
增强时保持 `preserve`。清理与翻新是独立操作；裁剪、镜像、增强的审批门禁在
`references/mix/workflow.md` 与 `references/mix/transform-options.md`。

### 上传（upload）

先用 `sau` CLI，不用浏览器桥接首发。首条或批量先存草稿，只点击一次保存并在草稿箱核验；
只有用户明确授权才公开发布或定时。「位置」默认不显示地址：上传页「短标题」后的位置
字段保持为空，只有用户明确要求填写时才设置。定时发表计划模板见
`references/upload/schedule-templates.md`（模板一：每天 N 条，节点 9:00/12:00/15:00/20:00）。
上传前先做 VFR 检测修复（封面生成依赖恒定帧率）；视频标注选择「含AI生成内容」；不自动
选择合集；标签不含比例描述；定时发表只能设未来 10 天，长排班按窗口分批。
批量上传前必须走账本 `preflight-manifest` /
`reserve-manifest`，完成后 `complete-manifest` 回写。详见 `references/upload/template-a.md`、
`references/upload/install.md`、`references/upload/media-lineage.md`。

### 账号继承（视频号 / 即梦）

SuperX 直接复用原 `sau` 仓库的账号资产（`conf.py`、`cookies/`、`profiles/tencent/`
持久 Profile）与 VideoHub 的即梦账本（`_ACCOUNT_BOOK.csv`），不复制登录态、无需
重新扫码。查看继承结果：

```bash
python3 "$SKILL_DIR/superx.py" accounts
```

迁移与路径覆盖见 `references/shared/account-inheritance.md`。

## 统一账本与资产中心

三段的统计只写同一份 SQLite（默认 `Video_Download/.supermedia/media_catalog.sqlite`）。
`source_key = platform:native_media_id` 是永久身份；文件名与序号不是身份。资产中心：

```bash
python3 "$SKILL_DIR/superx.py" console serve --open   # 固定 127.0.0.1，默认端口 8765
```

资产中心只做两件事：更新资产库、人工补全已核验 HOLD 血缘；不下载、不上传、不读登录态。
血缘与发布状态契约见 `references/shared/media-lineage-contract.md`。

管理台（暗色主题）现支持：KPI 总览、博主资产地图（点击查看来源/资产/血缘/发布明细）、
预约队列、最近活动时间线、最近发布与 HOLD 补全。一键启动：

```bash
# macOS：双击 installer/打开资产中心.command（已运行则直接打开浏览器）
# Windows：双击 installer/打开资产中心.cmd
```

## 安装

macOS 运行 `installer/install_macos.sh`，Windows 运行 `installer/install_windows.ps1`。
下载/混剪用系统 Python 3.10+；上传走独立 3.12 虚拟环境（`uv sync` 安装 `sau`）。

## 治理

经验沉淀模块位于 `.learnings/`（`LEARNINGS.md` / `ERRORS.md`），采用“只追加候选、
不自动晋升”模式；任务运行可用 `superx learn` 追加事实性记录，见
`.learnings/README.md`。

核心脚本、默认阈值、安全门禁只允许在单独维护任务中变更：预先提交人工批准记录，并运行
`scripts/controlled_evolution_guard.py --approval-file ...`。任务运行只能追加隔离学习候选，
不得自动晋升为规则。完整约束见 `references/shared/controlled-evolution.md`。
