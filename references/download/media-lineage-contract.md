# SuperMedia 统一资产与发布契约

## 目的

让 `superdown88`、`super-video-mix`、`super-upload` 和其他 Agent 共享同一份
源作品身份、派生血缘与发布历史。SQLite 是事实源；JSONL 是可重建审计镜像；
Excel/TSV 只是报表。

## 身份

- `source_key = platform:native_media_id`。Instagram 使用 shortcode；其他平台使用
  各自规范作品 ID。
- `asset_id` 由文件 SHA-256 生成；相同字节只产生一个资产，可有多个路径。
- `variant_id` 等价于派生成品的 `asset_id`；必须通过 `asset_lineage` 指向一个或
  多个源作品。
- `account_key = target_platform:account_name`。

文件名只用于阅读。博主位于独立字段和目录中，不加入 `source_key`。

## 三个 Skill 的交接

1. `superdown88` 每次下载并验证后运行 `sync --sources-only`，登记源作品和源资产。
2. `super-video-mix` 的 plan 携带 `lineage`；verified execution 回执携带同一字段，
   再运行 `ingest-receipt`。
3. `super-upload` 在上传前运行 `reserve-manifest`；有阻止项不得上传，有同源提醒
   必须取得明确允许。完成后运行 `complete-manifest`。

其他 Agent 必须使用 `supermedia.lineage/v1` 回执。没有回执的历史文件可由
`sync` 根据路径中的平台作品 ID 推断；不能唯一匹配的资产进入
`HOLD_LINEAGE_UNKNOWN` 或 `HOLD_LINEAGE_AMBIGUOUS`。

## 状态

- `draft_saved_unverified`：只知道执行了单次保存，未在草稿箱确认。
- `draft_saved_verified`：草稿箱按主标题确认。
- `scheduled`、`published`：平台侧已确认。
- `status_unknown`：中断或结果无法确认；禁止自动重试。
- `failed`、`cancelled`：没有成功提交的明确终态。

## 命令

```bash
CATALOG="$SUPERDOWN88_SKILL_DIR/scripts/media_asset_catalog.py"

python3 "$CATALOG" --root "/absolute/Video_Download" init
python3 "$CATALOG" --root "/absolute/Video_Download" sync --platform instagram
python3 "$CATALOG" --root "/absolute/Video_Download" ingest-receipt execution.json
python3 "$CATALOG" --root "/absolute/Video_Download" link-asset \
  --file legacy-remix.mp4 --source-key instagram:SHORTCODE \
  --evidence "人工代表帧核验和历史批次记录" --confidence 1
python3 "$CATALOG" --root "/absolute/Video_Download" reserve-manifest \
  --manifest batch.json --target-platform tencent --target-account account-a
python3 "$CATALOG" --root "/absolute/Video_Download" complete-manifest \
  --manifest batch.json --target-platform tencent --target-account account-a \
  --status draft_saved_verified --verification draft_box_title
python3 "$CATALOG" --root "/absolute/Video_Download" audit
python3 "$CATALOG" --root "/absolute/Video_Download" export-reports
```

数据库默认位于 `ROOT/.supermedia/media_catalog.sqlite`。不要把数据库、事件日志、
账号状态或素材提交到 Skill 仓库。
