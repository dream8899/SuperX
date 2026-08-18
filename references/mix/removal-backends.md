# 水印与硬字幕处理后端

只处理用户自有、获授权或明确允许修改的画面元素。不要承诺删除不可见水印、SynthID 或规避来源识别。

| 场景 | Backend | 风险与要求 |
|---|---|---|
| 固定、小、背景简单的矩形区域 | FFmpeg `delogo` | 低到中风险；使用原分辨率坐标，先预览边缘伪影 |
| 已知逐帧掩膜 | FFmpeg `removelogo` | 中风险；掩膜尺寸、时长和位置必须匹配 |
| 大块硬字幕、复杂纹理或运动背景 | `video-subtitle-remover` adapter | 高风险；显式安装模型与依赖，保留后端版本 |
| 固定 Alpha 模板的 Gemini 生成图水印 | `gemini-watermark-remover` 方法 | 仅适用于已验证模板，不泛化到任意视频水印 |
| 移动或跨帧变化水印 | P2 tracking/inpainting | 当前 Core MVP 不实现，不回退到大区域 `delogo` |

## 后端路由规则

`plan` 支持 `--removal-backend auto|ffmpeg-delogo|gemini-watermark-remover|video-inpaint`。

- `auto` 目前只生成路由建议，默认落到 `ffmpeg-delogo`，并在计划中写入提示；不会把普通水印误判成 Gemini 水印。
- `gemini-watermark-remover` 只有在画面确认是 Gemini 标准半透明标识、尺寸和边距匹配时才可选。该仓库使用反向 Alpha 混合，不是通用 inpainting。
- `video-inpaint` 是复杂背景/硬字幕/非 Gemini 水印的目标后端；当前版本会明确拒绝执行，避免产生看似完成但实际糊块的结果。

示例：

```bash
python3 "$SKILL_DIR/scripts/video_pipeline.py" plan INPUT \
  --remove-region '580:1125:70:75' \
  --removal-backend ffmpeg-delogo \
  --confirm-authorized-removal --approve-preview \
  --final-output OUTPUT.mp4 --output plan.json
```

如果 `delogo` 产生明显糊块，应停止重试同一区域，改为准备逐帧 mask/inpainting；不要把 `gemini-watermark-remover` 当作任意水印修复器。

区域同时保存原分辨率像素坐标和可选归一化坐标。大区域、多位置或靠近主体的修复必须标记 high risk 并生成 preview。
