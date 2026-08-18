# 跨系统依赖与 Agent 安装

## 基础依赖

运行核心能力只需要：Python 3.10+、FFmpeg（包含 `ffprobe`）和 Git（用于版本管理）。核心脚本仅使用 Python 标准库；AI/OCR/VSR 后端是可选项，未安装时必须明确降级为分析或路由建议。

安装完成后，在新终端验证：

```bash
python3 --version
ffmpeg -version
ffprobe -version
git --version
```

## macOS

### Homebrew（推荐）

```bash
brew install python ffmpeg git
```

Apple Silicon 的默认路径通常由 Homebrew 自动配置。若终端找不到 `ffmpeg`，先重新打开终端，再运行 `command -v ffmpeg`；不要在 Skill 中硬编码 `/opt/homebrew`。

### macOS 实战验证步骤

```bash
# 1. 确认三个核心依赖都在
which python3    # Apple Silicon: /opt/homebrew/bin/python3；Intel: /usr/local/bin/python3
which ffmpeg     # 同上
which ffprobe    # 同上

# 2. 确认版本
python3 --version    # 需要 3.10+
ffmpeg -version | head -1
ffprobe -version | head -1

# 3. 关键帧分析（不需要 PIL/OpenCV）
ffmpeg -ss 3 -i input.mp4 -vframes 1 -q:v 2 frame.jpg   # 提取帧
sips -g pixelWidth -g pixelHeight frame.jpg              # 查看尺寸（macOS 原生）
ffmpeg -i input.mp4 -vf "signalstats=stat=all" -f null -  # 信号统计
```

### macOS 常见问题（实战验证）

| 问题 | 现象 | 解决 |
|------|------|------|
| Homebrew 未安装 | `brew: command not found` | `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` |
| Xcode 命令行工具缺失 | ffmpeg 安装报编译错误 | `xcode-select --install` |
| ffmpeg 版本过旧 | 缺少 `signalstats` 滤镜 | `brew upgrade ffmpeg` |
| PIL/OpenCV 不可用 | `ModuleNotFoundError: No module named 'PIL'` | **不需要安装**。用 `sips` + `ffprobe signalstats` 替代所有图像分析。 |
| Skill 路径变更 | 符号链接断裂，`SKILL.md` 不可访问 | 见下方"符号链接安装"一节。 |

## Linux

### Debian / Ubuntu

```bash
sudo apt update
sudo apt install -y python3 ffmpeg git
```

### Fedora / RHEL

```bash
sudo dnf install -y python3 ffmpeg git
```

部分发行版需要先按本机软件源策略启用提供 FFmpeg 的仓库；启用后必须重新运行验证命令。

### Arch Linux

```bash
sudo pacman -S --needed python ffmpeg git
```

## Windows

推荐先安装 Python、FFmpeg 与 Git，再关闭并重新打开 PowerShell。

```powershell
winget install --id Python.Python.3.12 -e
winget install --id Gyan.FFmpeg.Shared -e
winget install --id Git.Git -e
```

如果环境没有 `winget`，可使用团队批准的 Chocolatey 或 Scoop 包源。验证使用：

```powershell
py --version
ffmpeg -version
ffprobe -version
git --version
```

PowerShell 路径包含空格时始终加引号；不要把 macOS/Linux 的 `$VAR` 语法直接复制到 PowerShell。Windows 原生可运行此 Skill；偏好 WSL 时，应在同一个 WSL 发行版内安装 Python、FFmpeg 与 Git，且使用 Linux 路径，不要混用 `C:\` 路径与 `/mnt/c/` 路径。

## Claude Code 符号链接安装（macOS/Linux 实测）

将 Skill 目录通过符号链接注册到 Claude Code 的 skills 目录：

```bash
# 1. 确认 Skill 源目录存在
ls /path/to/your/super-video-mix/SKILL.md

# 2. 创建符号链接
ln -s /path/to/your/super-video-mix ~/.claude/skills/super-video-mix

# 3. 验证
ls -la ~/.claude/skills/super-video-mix
# 输出示例: super-video-mix -> /path/to/your/super-video-mix
```

**注意**：
- 符号链接断裂时（源目录被移动/删除），Skill 会从可用列表消失。重新指向正确路径即可。
- 源目录在外部磁盘/网络挂载时，确保挂载点在 Claude Code 启动前已就绪。
- 修改源目录中的 SKILL.md 会即时生效，不需要重新安装。

**实战踩坑**：Skill 从旧目录迁移到 `SuperVideoMix/super-video-mix` 后，旧符号链接会断裂，Claude Code 仍显示旧 skill 名但无法执行。若新名称尚不存在，安全地重命名旧链接：

```bash
mv ~/.claude/skills/video-remix-007 ~/.claude/skills/super-video-mix
readlink ~/.claude/skills/super-video-mix
```

若 `super-video-mix` 已存在，不要删除任一目录；先用 `readlink` 确认其目标，再用本技能的 `install_skill.py --agent claude-code --link --force` 受控更新。

## 安装到 Agent

本 Skill 的机器名是 `super-video-mix`，调用名是 `$super-video-mix`。使用随附安装器可避免手工复制漏文件：

```bash
python3 scripts/install_skill.py --agent codex
python3 scripts/install_skill.py --agent claude-code
python3 scripts/install_skill.py --target-dir /absolute/path/to/skills
```

Windows PowerShell：

```powershell
py scripts\install_skill.py --agent codex
py scripts\install_skill.py --target-dir 'C:\path\to\skills'
```

安装器默认拒绝覆盖已有 `super-video-mix`。需要更新时，先备份或确认旧版本无用，再显式加入 `--force`。目标目录规范：

| Agent / 用法 | 默认目标目录 | 调用方式 |
| --- | --- | --- |
| Codex | `~/.codex/skills` | `$super-video-mix` |
| Claude Code | `~/.claude/skills` | `$super-video-mix` |
| 其他 Agent | 使用 `--target-dir` 指向该 Agent 已配置的 skills 目录 | 以该 Agent 的技能发现语法调用 |

安装后重启或刷新 Agent 的技能发现缓存，再检查其技能列表。若目标 Agent 不支持 Agent Skills 格式，不要假设会自动加载；将 `SKILL.md` 与 `scripts/` 放入其项目级说明目录，并在该 Agent 的项目指令中显式链接此路径。

## 仓库同步：推荐单一源链接

Git 仓库中的 `super-video-mix/` 是唯一源。macOS/Linux 上可让 Codex、Claude Code 等 Agent 直接链接到仓库；以后 `git pull` 后无需重复复制安装文件：

```bash
python3 scripts/install_skill.py --agent codex --link --force
python3 scripts/install_skill.py --agent claude-code --link --force
```

安装器会把目标目录登记到 `~/.supervideomix/agent-installs.txt`。完成仓库升级后，可用下面的命令同步本机已登记的 Codex、Claude 和其他 Agent：

```bash
python3 scripts/install_skill.py --sync-agents
```

同步策略是：Codex/Claude 优先使用符号链接；其他 Agent 若原来是复制安装，则安全地用当前仓库版本重新复制。同步只处理 Skill 安装目录，不触碰视频素材。需要让新 Agent 参与同步时，先执行一次：

```bash
python3 scripts/install_skill.py --target-dir /absolute/path/to/skills
```

`--link` 只替换对应的 Skill 目录，不会修改源视频。Windows 建议先启用开发者模式或以允许创建符号链接的终端运行；若受权限限制，改用默认复制安装方式，并在每次 `git pull` 后重新执行安装器。链接或更新后重启 Agent，使其重新发现 `super-video-mix`；不要同时保留旧名 `video-remix-007`，以免 Agent 读取过期规则。

### Claude CLI 实战约定

- Claude CLI 的 Skills 根目录为 `~/.claude/skills`；使用 `$super-video-mix`，不要再调用 `$video-remix-007`。
- Claude 目录若为符号链接，任何仓库更新会即时生效；先用 `readlink ~/.claude/skills/super-video-mix` 核对目标，再重启 Claude CLI。
- Claude 与 Codex 都必须阅读同一份 `SKILL.md` 和 `references/avoid-pitfalls.md`，不得在某一 Agent 内维护未回写仓库的私有规则。
- 不要在某个 Agent 的安装目录直接修改规则；修改仓库源后运行 `--sync-agents`，这样所有已登记的复制安装也会更新。
- 平台路径、包管理器和 Python 命令有差异时，先按本文档验证依赖，再调用脚本；不要复制另一个系统的 shell 语法。

## 可移植调用模板

所有 Agent 都应在请求中给出：授权说明、绝对输入路径、操作、输出目录和审批边界。例如：

```text
使用 $super-video-mix 处理我已获授权的视频：先分析并给出计划；fit 到 1080x1920、natural 调色、light 降噪后 light 锐化、0.9 倍音画同步变速；不要覆盖源文件，预览后等待我的批准再执行。
```

## 发布前自检

```bash
python3 scripts/install_skill.py --check
python3 scripts/video_pipeline.py --help
python3 /path/to/skill-creator/scripts/quick_validate.py .
```

`--check` 只检查本机依赖与 Skill 文件，绝不修改视频或安装目录。

`quick_validate.py` 是开发校验工具，可能额外需要 `PyYAML`；它不是 SuperVideoMix 的运行时依赖。受 Homebrew/PEP 668 管理的 Python 不要全局 `pip install`，使用一次性虚拟环境：

```bash
python3 -m venv /tmp/super-video-mix-validate
/tmp/super-video-mix-validate/bin/pip install PyYAML
/tmp/super-video-mix-validate/bin/python /path/to/skill-creator/scripts/quick_validate.py .
```
