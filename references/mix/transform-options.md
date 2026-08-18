# 类型化变换选项

## 目录

1. 选择模型
2. 默认保真方案
3. 裁切、轻度放大与高清方案
4. 执行顺序
5. 冲突与预览
6. 音画同步变速
7. 文件 MD5 轮换

## 1. 选择模型

增强操作统一使用 `off | auto | preset | custom`：

- `off`：明确关闭。
- `auto`：分析器提出建议；执行前展开为固定参数。
- `preset`：引用版本化预设，并在计划中保存展开参数。
- `custom`：保存经过类型与范围校验的用户参数。

构图使用 `preserve | fit | fill | smart | stretch | manual`。`stretch` 是显式 high-risk 选项，不是默认禁用的缺失功能。

## 2. 默认保真方案

```json
{
  "composition": "preserve",
  "resolution": "preserve",
  "fps": "preserve",
  "mirror": "off",
  "color": "off",
  "filter": "off",
  "denoise": "off",
  "sharpen": "off",
  "speed": 1.0
}
```

此方案适合只清理废片段、水印或硬字幕区域，不改变整体视觉风格的任务。

镜像反转使用独立模式：

- `off`：不反转，默认值。
- `horizontal`：左右镜像，映射到 FFmpeg `hflip`；要求 preview。
- `vertical`：上下反转，映射到 FFmpeg `vflip`；high risk。
- `both`：依次使用 `hflip,vflip`，视觉上等价于 180° 旋转；high risk。

只在用户明确要求或构图意图需要时启用，不将镜像作为“去重”手段。

## 3. 裁切、轻度放大与高清方案

- `--crop-aspect 3:4 --crop-anchor top`：9:16 输入保持全宽，裁掉底部约 25%；适合水印固定在右下、主体位于中上区域的素材。推荐输出 `1080x1440`。
- `--safe-zoom 1.08 --zoom-anchor top-left`：放大后保留左上区域，丢弃右侧和底部边缘；根据水印尺寸选择最小 factor。
- `--quality hd`：Lanczos 缩放、CRF 16、x264 slow；适合常规高清交付。
- `--quality hd-plus`：CRF 14、x264 slow；文件更大且要求 preview，不代表 AI 超分。

裁切和放大只能用于用户有权修改的固定可见标识。先检查主体、字幕、车轮、手部等是否进入裁切带。

顶部对齐 3:4 的批量工作必须先执行 `scripts/crop_3x4_preflight.py`。脚本抽样比较裁除带与主体区的边缘细节和跨帧运动，生成：

- `crop_3x4_screening_schedule.tsv`：筛选结论、风险指标、意见、优先级和建议排期。
- `crop_3x4_screening_schedule.json`：单个批次汇总，避免逐视频 JSON 堆积。
- `previews/*__crop3x4-top-preview.jpg`：红线以下为拟裁除区域。

自动指标只负责保守分组：`适合` 可优先审查，`人工复核` 必须观看，`不适合` 默认 HOLD。不要把算法结论直接转换成 `--approve-preview`。

## 4. 可控增强方案

- `color`：`natural`、`warm`、`vivid` 或 custom。
- `filter`：`cinematic`、`soft`、`vintage`、`monochrome` 或 custom。
- `denoise`：`light`、`medium` 或 custom；过强会损失纹理。
- `sharpen`：`light`、`medium` 或 custom；必须放在降噪后。
- `speed`：保存明确 factor；视频和音频使用同一语义速度。

预设名不是执行参数。计划必须同时保存 preset version 和展开后的参数，避免预设升级改变旧计划。

## 5. 执行顺序

按以下顺序构建 filter graph：

1. 时间轴裁切、同步变速、PTS 重建。
2. `delogo`、`removelogo` 或修复后端。
3. crop/reframe、安全 zoom、scale/pad，再执行已批准的 mirror/flip。
4. denoise。
5. color 与 filter。
6. sharpen。
7. overlay/subtitle。
8. fps、pixel format、encode。

先裁切再变速。多保留区间使用显式 concat，不依赖 `-shortest`。

## 6. 冲突与预览

同时启用 `color` 和 `filter` 时检查曝光、对比度、饱和度、gamma 与色温是否重复调整。发现重叠时输出 `needs_review`，不要静默合并。

以下情况要求 preview：

- `stretch`、`smart` 或可能裁主体的 `fill`。
- 中高强度 color/filter/denoise/sharpen。
- 多个视觉增强叠加。
- 大区域水印或字幕修复。
- 3:4 裁切、安全 zoom、HD+ 编码。

## 7. 音画同步变速

视频速度 factor 为 `s` 时使用等价于 `setpts=PTS/s` 的表达式。音频使用 factor 为 `s` 的 `atempo` 链；超出单个 filter 范围时拆成多个合法 factor。验证输出音视频时长差不超过一个输出帧周期或计划定义的容差。

## 8. 文件 MD5 轮换

`--md5-rotate`（plan 选项）或 `md5-rotate` 子命令对成品 MP4 做一次容器元数据 remux：

- 流级 `-c copy`，画面、声音、分辨率、时长、编码和感知指纹均不变。
- 写入唯一非内容元数据 tag（`comment=svmix-md5-rotate-v1:<nonce>`），文件字节变化后 MD5 与 SHA-256 随之改变。
- 输出必须为新路径且输入已完成编码兼容化；完成后完整解码、编解码一致性，并校验输出 MD5 ≠ 输入 MD5。
- 只适用于自有或已获授权内容；得到的是新字节的派生哈希值，不是把哈希改成指定值。

```bash
python3 "$SKILL_DIR/scripts/video_pipeline.py" md5-rotate OUTPUT.mp4 \
  --output OUTPUT__md5-rotate.mp4 --report md5-rotate.json
```

或在 plan 中一并交付：

```bash
python3 "$SKILL_DIR/scripts/video_pipeline.py" plan INPUT \
  --final-output OUTPUT.mp4 --md5-rotate \
  --output plan.json
```
