# 经验沉淀模块

SuperX 的经验沉淀是“只追加、不自动晋升”的隔离候选区。任务运行可以把事实性
学习记录追加到本目录，但不得据此直接改写核心脚本、默认阈值或安全门禁。

## 目录

- `LEARNINGS.md`：正向经验（LRN-YYYYMMDD-NNN）
- `ERRORS.md`：失败与修正记录（ERR-YYYYMMDD-NNN）
- `governance/proposals/`：提出规则变更的候选提案
- `governance/approvals/`：人工批准记录（guard 校验通过后才能改核心文件）

## 追加方式

```bash
python3 superx.py learn --area learnings --summary "一句话结论" --details "更多细节（可选）" --tags tag1,tag2
python3 superx.py learn --area errors --summary "发生了什么 / 如何修复" --details "..."
```

也可以手工按以下模板追加：

```text
## [LRN-20260818-001] 主题

**Logged**: 2026-08-18T00:00:00+08:00
**Priority**: medium
**Status**: candidate
**Area**: download | mix | upload | console | ledger

### Summary
一句话结论。

### Details
事实与证据；不写“总是/永远”式的全局断言。

### Metadata
- Source: task_run | user_feedback | failure
- Related Files: 相关脚本路径
- Tags: ...
- Pattern-Key: 唯一可检索键
```

## 晋升门槛

候选记录不会自动晋升。若要据此修改核心文件，必须在单独维护任务中提交
`governance/approvals/*.json`（含 scope、reason、risk、rollback、tests、human 批准人），
再运行：

```bash
python3 scripts/controlled_evolution_guard.py --approval-file governance/approvals/xxx.json
```

guard 将 `.learnings/`、`governance/proposals/`、`governance/approvals/` 视为可写候选区，
其余核心文件受保护。
