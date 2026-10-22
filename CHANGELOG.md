# 更新记录

## v1.0.5 · 2026-10-21

按实际制作流程重构，并把交付音轨改为片段自带音频。

### 新增

- README 的「工作流程」改为六步：提供素材 → 需求分析 → 对标拆解 → ZY 全能参考提示词 → 选模型生成 → 成片交付，并标注内部阶段映射；第 5 步保留样片审查与逐段审查两个审批点。
- `references/baseline-video.md`：对标视频拆解卡。要求实际完整观看后才可标 `reviewed: true`，结论写入 `concept.json` 的 `baseline`，并区分可迁移结构与不可迁移内容。
- `project.json` 新增 `reference_video`、`reference_voice`、`delivery_audio` 三个字段。
- `design.json` 支持可选 `global.omni_reference`：每项必须同时声明 `asset` 与 `role`，结构不合规时 `compile`/预演/按镜打包直接阻断。

### 变更

- `render --mode final` 默认拼接片段自带音频（统一 AAC 48kHz 后拼接），**母带不再重贴**，只作为时间基准与备用。
- `delivery_audio=master` 可回退到贴母带；`render --mode preview` 的预演仍使用母带判断音乐节奏。
- 演练夹具改为生成带音轨的合成片段，覆盖新的交付路径。

### 修正

- 片段缺音轨时明确报错并提示回退选项，不再静默产出无声成片。
- 演练音频校验从「与母带逐样本哈希一致」改为「成片音轨可解码」，与新的交付语义一致。

---

## v1.0.4 · 2026-10-21

ZY 方法库从「可选」改为设计阶段的强制环节。

### 新增

- `design.json` 顶层必须声明 `"method": "zy-cinematic-realism"`，未声明时 `compile`、`render --mode preview`、`pack --shot` 直接阻断。
- 未声明即阻断的单元测试。

### 变更

- `data.md`、`SKILL.md`、`creative.md`、`vision-generation.md`、`README.md` 中的「可选」表述全部改为强制。
- `simulate_new_user.py` 演练夹具补上 `method` 声明，否则演练会被自身校验拦住。

---

## v1.0.3 · 2026-10-21

接回 ZY 方法库并适配新版 `design.json`。

### 新增

- `vendor/zy-cinematic-realism/`：77 个文件，含 40 张导演卡、`LICENSE`、`NOTICE.md`。
- `references/zy-cinematic-integration.md`：重写为映射到 `global` / `scenes` / `shots` 的适配文档，不再引用旧版已不存在的脚本与状态文件。
- `THIRD_PARTY_NOTICES.md`：CC BY-NC 4.0 署名与来源。

### 变更

- `SKILL.md`、`creative.md`、`vision-generation.md`、`README.md` 增加 ZY 加载入口。

---

## v1.0.2 · 2026-10-22

以映流 MV 六阶段独立实现替换旧版工作流。

六阶段：prepare → concept → storyboard → pilot → production → delivery；单 CLI `scripts/mv.py` 管理状态、输入包、审批与本地媒体处理；只依赖 Python 标准库与 FFmpeg/FFprobe，不内置视觉模型、ASR 与生成后端。

旧版十二步批准锁工作流保留在 Git 历史（`3fd8ea3`）与 `mv-auto-pipeline-fusion` 仓库中。

---

## 许可

本仓库自有代码按 MIT 使用。`vendor/zy-cinematic-realism/` 为 ZY / popopo-99 的第三方作品，采用 CC BY-NC 4.0（含非商业限制），详见 `THIRD_PARTY_NOTICES.md`。
