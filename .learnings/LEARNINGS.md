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
