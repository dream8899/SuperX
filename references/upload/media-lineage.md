# SuperMedia 发布门禁

`super-upload` 与 `superdown88`、`super-video-mix` 共用
`supermedia.lineage/v1`。SQLite 是事实源，文件名和 Excel 不是。

直接运行 `sau` 前，先调用 `superdown88/scripts/media_asset_catalog.py`：

```bash
python3 "$CATALOG" --root "$VIDEO_ROOT" reserve-manifest \
  --manifest batch.json --target-platform tencent --target-account ACCOUNT
```

有 blocker 时禁止上传；同源不同 Remix 的 review 只有在用户明确同意后才可加
`--allow-source-repeat`。上传完成后必须回写：

```bash
python3 "$CATALOG" --root "$VIDEO_ROOT" complete-manifest \
  --manifest batch.json --target-platform tencent --target-account ACCOUNT \
  --status draft_saved_verified --verification draft_box_title
```

若批次中断、保存结果不明或未检查草稿箱，使用 `status_unknown` 或
`draft_saved_unverified`，不得自动重试。模板 A 已自动执行上述流程。
