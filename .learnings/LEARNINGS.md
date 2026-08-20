## [LRN-20260731-001] correction

**Logged**: 2026-07-31T15:46:00+08:00
**Priority**: high
**Status**: resolved
**Area**: frontend | backend | tests

### Summary
历史资产总账不能作为管理台的实时在库文件统计。

### Details
Catalog 会保留已删除文件的 `assets` 行，以维持发布、血缘与审计可追溯性；同步正确地将
`asset_locations.present` 置为 0。初版管理台却展示 `assets`，使用户删除文件后误以为一键
更新失效。

### Suggested Action
所有库存界面必须使用 `COUNT(DISTINCT asset_id) WHERE present=1`，并同时明确展示历史资产与
缺失路径，避免改变审计账本语义。

### Metadata
- Source: user_feedback
- Related Files: scripts/media_asset_catalog.py, scripts/supermedia_console.py
- Tags: inventory, deletion, dashboard, catalog
- Pattern-Key: catalog.live-inventory-vs-history

### Resolution
- **Resolved**: 2026-07-31T15:46:00+08:00
- **Notes**: 新增 `present_assets` 与 `missing_asset_locations`，并加入回归测试。

---
## [LRN-20260818-001] 资产中心新增博主详情、预约队列与最近活动视图

**Logged**: 2026-08-18T15:03:04.686218+08:00
**Priority**: medium
**Status**: candidate
**Area**: learnings

### Summary
资产中心新增博主详情、预约队列与最近活动视图

### Details
为 supermedia_console 增加 /api/creator 与 dashboard 的 reservations/events 字段；博主行可点击查看来源/资产/血缘/发布。

### Metadata
- Source: task_run
- Related Files: （待补充）
- Tags: console, asset-center, superx
- Pattern-Key: lrn-20260818-001
## [LRN-20260818-002] SuperX 账号继承机制：复用 sau 仓库与 VideoHub 即梦账本

**Logged**: 2026-08-18T21:13:44.935435+08:00
**Priority**: medium
**Status**: candidate
**Area**: learnings

### Summary
SuperX 账号继承机制：复用 sau 仓库与 VideoHub 即梦账本

### Details
新增 superx accounts 与 doctor 账号段；视频号 4 个持久 Profile + main cookie、即梦 Morphworks 账本原样继承，迁移只需复制目录并设 SUPERX_* 环境变量。

### Metadata
- Source: task_run
- Related Files: （待补充）
- Tags: accounts, tencent, jimeng, inheritance
- Pattern-Key: lrn-20260818-002
## [LRN-20260820-001] 视频号封面生成卡住与网络环境相关，需先检查再决策

**Logged**: 2026-08-20T19:20:00+08:00
**Priority**: high
**Status**: candidate
**Area**: upload

### Summary
视频号封面生成是否卡住与当前网络环境强相关：网络好时上传后封面立即出现、发表按钮立即可用；网络差时封面一直「生成中」、按钮无法激活（曾被误归因为 VFR，重编码 CFR 有效但非唯一原因）。

### Details
- 同一批文件在不同网络下表现不同：换网络后同样文件上传直接出封面，无需重编码。
- 决策顺序应为：上传后先检查封面是否正常生成/发表按钮是否可用 → 正常则直接发表；
  卡住时若检测到 VFR（r_frame_rate ≠ avg_frame_rate）先重编码 CFR+faststart 重传；
  仍卡住再走「保存草稿 → 草稿箱 → 定时发表」兜底。
- 重编码不能无条件做，要考虑网络环境与是否需要。

### Metadata
- Source: task_run
- Related Files: social-auto-upload/uploader/tencent_uploader/main.py, scripts/tencent_draft_to_schedule_batch.py
- Tags: tencent, cover, network, vfr, upload
- Pattern-Key: lrn-20260820-001
