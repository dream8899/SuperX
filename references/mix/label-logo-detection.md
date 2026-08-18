# 文字标签与 Logo 定位、裁剪候选

## 固定流程

```text
低分辨率采样 → 时序共识 → 二维候选框 → 坐标映射 → 最小损失裁剪候选
  → 红框/绿框预览 → 人工批准 → 执行 → 完整解码验证
```

`smart_label_detect.py` 只生成分析、候选框和裁剪矩形，不直接修改视频。红框表示文字或 Logo 候选，绿框表示建议保留区域。

## 算法

- 从视频全长均匀抽取 8 帧，先缩放到 256 像素宽的灰度分析帧，显著降低逐像素计算量。
- 行级时序共识用于发现顶部/底部文字带；二维 8×8 网格同时计算局部细节和跨帧稳定性，用于确定文字和 Logo 的横纵坐标。
- 仅在边缘区域寻找候选，减少主体纹理误报；候选框按长宽比、持续性、细节和边缘关系分类并给出置信度。
- 将分析坐标映射回原视频坐标，从上、下、左、右四个方向计算裁掉候选所需的内容损失，选择损失最小的方向。
- 单方向裁除超过 25% 时进入 HOLD；组合后内容损失超过 20% 为 high risk，10%–20% 为 medium risk。
- 不贴边的候选也可生成建议，但必须标记 `edge_touching=false` 并人工观看预览。

## 调用

单视频：

```bash
python3 "$SKILL_DIR/scripts/smart_label_detect.py" INPUT.mp4 \
  --creator CREATOR_NAME --memory MEMORY_DIR \
  --target-label magicbox.studio \
  --report label-crop-analysis.json \
  --preview-dir previews
```

批量：

```bash
python3 "$SKILL_DIR/scripts/smart_label_detect.py" --input-dir INPUT_DIR \
  --creator CREATOR_NAME --memory MEMORY_DIR \
  --target-label magicbox.studio \
  --output-dir ANALYSIS_DIR --preview-dir ANALYSIS_DIR/previews
```

批量输出：

- `label_crop_analysis.json`：完整检测证据与裁剪候选。
- `label_crop_screening.tsv`：文件、裁剪矩形、内容损失、风险和复核状态。
- `previews/*__label-crop-preview.jpg`：红色候选框与绿色保留框。

提供 `--target-label` 时，仅保留顶部 OCR 与目标文字相似且跨帧稳定的文字候选；目标文字使用 4 个关键时点做原分辨率 OCR，其他顶部文字全部忽略。Logo 候选不受该过滤影响。

## 创作者记忆

只在人工确认预览与裁剪矩形后加入 `--confirm-memory`。未确认的自动检测不得写入记忆：

```bash
python3 "$SKILL_DIR/scripts/smart_label_detect.py" INPUT.mp4 \
  --creator CREATOR_NAME --memory MEMORY_DIR \
  --confirm-memory --report confirmed.json
```

记忆使用最近 10 次已确认裁剪的中位数，并同时保存上、下、左、右裁量。布局位置、分辨率或内容结构变化时应放弃记忆并重新检测。

## 执行门禁

1. `review_required=true` 时不得直接执行。
2. 检查红框确实是获授权处理的文字/Logo，不是主体、产品文字或场景标识。
3. 检查绿框没有截断人脸、手部、车轮、产品或必要字幕。
4. 若 Logo 位于画面内部且最小裁剪损失过大，改用已授权的固定区域修复或保留原画幅。
5. 执行必须写新文件，完成后验证尺寸、时长、音轨和完整解码。
