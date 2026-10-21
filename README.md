# 音乐MV完整工作流 · 公开源码版

从歌曲、故事和八部门设计，到提示词、逐步批准、素材审查、剪辑及交付的工作流源码与Skill。

## 本次公开发布范围

- 包含 `SKILL.md`、全部业务脚本与测试、十二步独立签名批准锁、配置示例及使用文档。
- 不包含Docker构建/导出内容、真实项目媒体、凭证、私钥、模型权重、实际批准证书或原Git历史。
- 快速入门：[使用说明](使用说明.md)；当前顺序：[实际工作流](references/actual-execution-workflow.md)；批准机制：[十二步批准锁](references/workflow-approval-locks.md)。
- `examples/workflow-trust-store.example.json`的空公钥必须由独立复核者配置，不提供默认批准身份。
- 每步机检完成后暂停；未经当前有效独立签名批准，不进入下一步。默认视频后端关闭。
- 批准锁约束编排器，不是宿主权限沙箱；代码、执行入口、信任配置和私钥须由部署者隔离保护。
- 本次公开发布只整理源码和脱敏说明；最近对应业务代码隔离回归472项通过，不代表实际影片验收或本次重新全量运行。

### 第三方许可特别提醒

自有代码按原项目MIT口径；内置ZY Cinematic Realism方法库采用 **CC BY-NC 4.0（含非商业限制）**。
作者署名、来源、`LICENSE`及`NOTICE.md`原样保留，详见[第三方声明](THIRD_PARTY_NOTICES.md)。
**不能将整个仓库宣传为不受限制的MIT或默认可商用。** 商用请先核对并取得必要授权。

---

## 原项目说明

# mv-auto-pipeline-fusion

当前执行主链：**歌曲与故事 → ZY 故事开发＋电影语料辅助组织 → 连续性设计 → ZY 导演执行 → 全能参考编译 → 视频生成与审片**。先读[实际执行流程与资料入口](references/actual-execution-workflow.md)，再按[电影组织交接](references/film-grammar-creative-bridge.md)执行。普通创作参考无需API密钥或语料签名。

新增十二步持久编排、正式J/L声音轨、独立签名口型事件测量、电影语料验收、批量队列、复盘证据与审片同步，入口和兼容范围见[八项补齐使用说明](references/gap-integration-guide.md)。

本地有声审片入口：`scripts/review_workbench.py` 提供真实原曲/对标/候选播放器、逐行时间复核、逐镜观察和设计草稿；[使用与验收边界](references/review-workbench.md)。所有导入均待核，不自动认证AI听看或正式批准。

音乐 MV 的设计、提示词编译、门禁与本地剪辑工作流（Agent Skill）。后端由用户选择，动态审美与口型需要真实审片，不保证无人参与自动产出合格成片。

当前验证范围和未完成项见[当前验收状态](references/current-validation-status.md)。本地契约/媒体测试不是电影感证明；本轮用户否定的生成与返剪实验不作为成功范例。运行媒体、凭证和具体项目设计不随代码发布。

当前任务明确禁止使用生成视频后端。项目策略默认关闭，预占/派发/回查/MCP领取均检查该约束，宿主桥接也默认拒绝外部调用；只做本地分析、现有媒体与离线测试。详见[MCP交接和后端禁用](references/mcp-handoff-contract.md)。

十二步业务流程，机器/人工/外部职责见 [生产契约](references/production-hardening.md)，真实门禁入口见 [registry](references/gate-registry.json)。`gate_checks.py` 实际提供gate1/gate5/gate6，检查数从报告读取。

**十二步逐步批准锁：** `workflow_run.py run/resume` 每次最多新完成一步，随后等待独立人工签名批准；未经当前步骤同意，不进入下一步，第12步也须签名才算完成。批准绑定项目、计划、执行记录、产物及前序批准链，不接受自写approved。配置、独立签名和权限隔离前提见[逐步批准锁](references/workflow-approval-locks.md)。该锁强制约束编排器，不自动拦截独立脚本或仓库外MCP；防恶意绕行必须由宿主保护代码、信任目录、启动环境和允许入口。

逐步检查入口为 `scripts/workflow_audit.py`，复用现有校验，输出十二步缺口；不会补建状态或审批。最近一次真实项目的跳步与代码修正见 [2026-10-16工作流审计](references/workflow-audit-2026-10-16.md)。

```bash
python scripts/workflow_audit.py PROJECT --phase before-generation --submission SUBMISSION.json
python scripts/workflow_audit.py PROJECT --phase before-batch --submission SUBMISSION.json
python scripts/workflow_audit.py PROJECT --phase before-delivery --output-name final_mv.mp4
```

原生请求和上传回执通过后，视频提交须先 `python scripts/submission_jobs.py prepare PROJECT --contract SUBMISSION.json` 预占预算，再由用户选定外部适配器调用dispatch。制作包pass不能直接当派发许可；未知调用不得自动重试。详见[提交作业契约](references/submission-job-contract.md)。

原任务完成后，用 `python scripts/media_origin.py PROJECT --job-id JOB --output-pointer /实际输出字段` 实际下载并登记来源。正式素材须有`origin_receipt_path`；保留素材绑定用户授权原文。正式选段、合成和交付会重新核验来源，详见[素材来源契约](references/media-origin-contract.md)。登记来源不自动批准动态审片。

返修通过`repair_state.py open/close`绑定失败媒体、冻结计划、新任务和新章重审。全片审片后须执行`python scripts/repair_state.py assess PROJECT`，交付核验该汇总；没有登记返修也须明确记录当前状态。详见[返修状态契约](references/repair-state-contract.md)。

裁切、放大及按帧重剪用`python scripts/local_transform.py PROJECT --plan TRANSFORM.json`，必须在冻结返修计划许可范围内实际执行，保留父素材、命令、帧映射及声音匹配结果；先登记pending，新章重审后close。详见[本地变换契约](references/local-transform-contract.md)。

正式导出先到technical_verified，完整交付包通过后才ready。运行`python scripts/delivery_package.py PROJECT --output-name final_mv.mp4`，缺最终动态复核时只建立pending模板并阻断；实际完成有声复核后再执行，生成双版本、权威LRC、实际对齐执行证据与ZIP。详见[交付包契约](references/delivery-package-contract.md)。不会自动认证嘴型或用户已接收。

交付包还强制[离线费用核验](references/production-cost-contract.md)，未知费用不填零。仅从未派发的准备可用`submission_jobs.py cancel-prepared PROJECT --job-id JOB`释放本地预占；不查询后端，不是取消供应商任务或退款。

正式生产另要求关1/5/6、八部门和制作桌的实际进程回执，直接手写gates报告或只运行只读audit不算执行。通过固定入口运行：

```bash
python scripts/workflow_execution.py run PROJECT --step gate1
python scripts/workflow_execution.py run PROJECT --step gate5
python scripts/workflow_execution.py run PROJECT --step crew_pipeline
python scripts/workflow_execution.py run PROJECT --step crew_rehearsal
python scripts/workflow_execution.py run PROJECT --step gate6
python scripts/workflow_execution.py check PROJECT
```

以上顺序在设计和提示词当前版本编译完成后执行：关5可能更新分镜提示词指纹，随后重跑八部门和制作桌才能绑定当前分镜。失败重跑使旧成功记录失效，旧记录归档；运行日志、报告、输入及脚本版本均须一致。关1默认要求实际ASR来源和逐行复核，临时时间失败；结构试跑须显式 `gate_checks.py gate1 PROJECT --diagnostic`，不能作为正式回执。回执不认证实际听看；拥有文件写权限者仍可伪造文件，不把本地哈希当外部可信签名。

实际本地ASR及时间轴草稿入口：

```bash
python scripts/asr_capture.py PROJECT --audio ORIGINAL_AUDIO --lyrics OFFICIAL_LYRICS --model-path LOCAL_MODEL_DIR
python scripts/timing_draft.py PROJECT
```

ASR原始结果、模型指纹和官方歌词匹配候选分别保存；草稿全部待核，不自动补缺句、判间奏或批准句界。`timing_evidence.py`要求原曲/歌词/ASR/逐行复核绑定同一版本；复核者声明不等于自动证实其实际听过。

退出0仅代表本阶段当前机检通过，不能声明十二步历史执行、艺术质量或交付接收通过。外部调度器仍须调用提交契约、记录真实回执和成本；直接MCP调用不受本地Python自动拦截。

## 快速开始

主定义与完整流程见 [SKILL.md](SKILL.md)，版本历史见 [CHANGELOG.md](CHANGELOG.md)。

V1.4.1 的数据填写、完整执行顺序与旧项目迁移见 [视觉开发执行契约](references/visual-workflow-contract.md)。逐镜导演构造见 [导演级 Scene Master 契约](references/director-scene-master.md)。先完成ZY故事与电影参考组织、连续性结构选择，再完成绑定该结构的ZY导演执行，随后生成场景母版、关键帧与视频；后端由用户选择。

```bash
# 1. 降级对齐（无 ASR key 的试跑时间轴；正式口型成片需接 ASR 通路）
python scripts/timeline_fallback.py input/song_16k.wav input/lyrics.txt state/timeline.json --master-audio input/song.mp3

# 2. 无视觉编辑方案时只输出时间骨架，不自动配景别与口型
python scripts/storyboard_build.py <project_dir> --fps 24

# 3. 先按ZY故事方法编写独立BEATS，再组织并选择连续性结构
python scripts/organization_workflow.py propose --project <project_dir> --beats <BEATS.json> --index <INDEX.json> --output <PROPOSAL.json>
# 修订提案，填写reviewer和decision=accept_structure后：
python scripts/organization_workflow.py select --project <project_dir> --draft <PROPOSAL.json>
# 按已选结构调用ZY导演方法编写development/visual_edit_plan/story_plan/sequence_direction
# development.organization_structure必须填写select返回的binding
python scripts/organization_workflow.py bind --project <project_dir>
python scripts/storyboard_build.py <project_dir> --fps 24
python scripts/visual_development.py <project_dir>
python scripts/crew_pipeline.py <project_dir>
# 可选：用合法标注构建的电影语法画像做统计审计（不下载正片）
python scripts/film_grammar_corpus.py audit <project_dir> <profile.json>
python scripts/prompt_build.py <project_dir>

# 4. 硬门禁机检（关1/关5/关6，同时写 gates/*.json + gates/*.html）
python scripts/workflow_execution.py run <project_dir> --step gate5
python scripts/workflow_execution.py run <project_dir> --step gate6

# 5. 多场景关键帧预览成片（优先 input/scene_*.png；无场景图才回退人物参考图）
# 用户未选择视频后端时，正式流程在这里停止。
python scripts/render_preview.py <project_dir>

# 图片先register为pending，实际观察后以当前图片SHA、审查者和证据执行approve-static。
# 母版先获有效静帧批准，才可登记依赖它的关键帧；登记不等于批准。
# visual_revision.py 用于 Remix 单变量比较与局部修复契约检查，详见执行契约。

# 6. 用户选定视频后端并核验真实契约后，显式传入该后端的时长上限
python scripts/chapter_plan_build.py <project_dir> --max-duration <backend_limit>

# 7. 准备模型中立章节资产、全能参考蓝图与导演制作包；generation_manifest.json 默认不可提交
python scripts/chapter_audio_build.py <project_dir>
python scripts/chapter_prompt_build.py <project_dir>

# 首次视频生成前：生成完整音乐分镜预演，完整观看并填写四维度/时间码证据
python scripts/animatic_review.py init <project_dir> --media output/mv_preview.mp4
python scripts/animatic_review.py check <project_dir>
# 若设计/预演改变：先撤销，重做预演，再刷新待审记录并完整重审
# python scripts/animatic_review.py invalidate <project_dir>
# python scripts/animatic_review.py refresh <project_dir> --media output/mv_preview.mp4

# 8. 外部适配层按真实 schema 只提交代表样片；完整观看后填写并检查审片记录
python scripts/director_review_gate.py init <project_dir>
python scripts/director_review_gate.py check <project_dir>
python scripts/submission_contract.py <submission.json> --project-dir <project_dir>

# 9. 审片批准后，外部适配层才可生成其余章节；每章先执行 duration_fit.py

# 10. 先逐章审批所选的真实素材版本，再做正式选段渲染
# 每章分别初始化并完成动态审片；init不自动批准
python scripts/director_review_gate.py init <project_dir> --chapter CH01
python scripts/director_review_gate.py check <project_dir> --chapter CH01
python scripts/edit_assemble.py <project_dir> --render
# 正式选段已渲染；输出全片候选预览，不重写edit_assembly
python scripts/stitch_chapters.py <project_dir> --allow-unreviewed-preview \
  --output-name sequence_preview.mp4 --width 1280 --height 720
# 完整观看该候选，再填写并检查全序列审片；批准后剪辑清单冻结
python scripts/director_review_gate.py init <project_dir> --chapter full_sequence
python scripts/director_review_gate.py check <project_dir> --chapter full_sequence
python scripts/stitch_chapters.py <project_dir> --width 1280 --height 720
python scripts/delivery_state.py check <project_dir> --output-name final_mv.mp4

# 如需修改已批准剪辑，显式撤销后修改，刷新待审记录并重新完整审片
# python scripts/director_review_gate.py invalidate <project_dir> --chapter full_sequence
# python scripts/edit_assemble.py <project_dir> --render
# python scripts/director_review_gate.py refresh <project_dir> --chapter full_sequence

# 未审片只能显式输出技术预览，且文件名必须包含 preview
python scripts/edit_assemble.py <project_dir> --render --technical-preview
python scripts/stitch_chapters.py <project_dir> --allow-unreviewed-preview \
  --output-name technical_preview.mp4

# 11. 对齐三验（建议同时传入从成片提取的候选音轨，避免把母带与自身比较）
python scripts/align_verify.py input/song_16k.wav state/storyboard.json state/timeline.json \
  --candidate-wav output/final_16k.wav --candidate-video output/final_mv.mp4 \
  --out output/verification_report.json

# 12. 运行回归测试
python -m unittest discover -s tests -v
```

## 组件

| 脚本 | 职责 |
|---|---|
| gate_checks.py | 关1/关5/关6机检，检查数由报告给出；关1区分结构通过与production |
| workflow_audit.py | 只读汇总十二步与生成前/批量前/交付前当前检查，复用关5和审批校验，不补历史批准 |
| production_preflight.py | 正式提交、选段渲染及最终合成共用实时关1/5/6、导演序列、剧组报告及关键帧检查 |
| timeline_fallback.py | 无 ASR key 降级对齐 |
| storyboard_build.py | 音乐引用驱动的视觉分镜；无方案只输出时间骨架，不猜副歌或轮换景别 |
| prompt_build.py | 从 ZY Scene Master / Continuity Bible 编译提示词；缺少故事方案时阻断 |
| chapter_plan_build.py | 按视频模型最长时长聚合连续镜头，保留 Continuity Bible 与 Shot Deltas |
| chapter_audio_build.py | 按章节窗口裁出模型中立音频资产，记录来源窗口与 SHA-256，不假设后端字段 |
| chapter_prompt_build.py | 将 ZY Scene Master、Continuity Bible、Shot Deltas 编译为模型中立章节制作包 |
| omni_reference.py | 构建并机检人物/关键帧/音频的全能参考职责、保留/排除项与逐镜时间轴；不调用后端 |
| director_review_gate.py | 强制完整观看单章动态样片并分别记录技术检查与九项导演检查；批准前阻断批量生成/正式合成 |
| animatic_review.py | 视频提交前的完整音乐分镜预演批准；不批准运动、表演或口型 |
| review_dependencies.py | 公共锁＋本章直接依赖的审片投影；全片绑定整条序列 |
| chapter_admission.py | 正式选段前核对素材章节/镜头归属、批准版本与实际审片文件；禁止仅reviewed字符串准入 |
| stitch_chapters.py | 拼接任意用户所选后端的章节产物、修正交付画布并贴回同源母带 |
| delivery_state.py | 独立记录已输出、技术验收、可交付及版本失效；不自动登记用户接收 |
| edit_assemble.py | 先在独立版本目录生成全部选段，核验输入/审批版本后发布清单；失败保留旧成功片段 |
| duration_fit.py | 时长贴合（帧网格+流复制快路径） |
| align_verify.py | 对齐三验（纯标准库） |
| render_preview.py | 多场景关键帧预览（章节分配+等比画面+Ken Burns+母带重贴） |
| ledger.py | 成本台账（币与真钱分列） |
| audio_master.py | 分离原始母带与分析副本，保留音轨采样率和声道，选择流复制或无损编码 |
| restore_master_audio.py | 复用已有视频，另存原始母带音轨版本，并校验音频与画面载荷 SHA-256 |
| visual_development.py | 故事来源、导演路由、参考权限、连续性和动作设计契约检查 |
| crew_pipeline.py | 八部门剧组交接与视听语言审计：重复机位、30/180度、动线、地理、节奏、美术和灯光 |
| film_grammar_corpus.py | 将合法电影镜头标注聚合为匿名统计画像，并审计平均镜长、景别转移和运镜变化；不保存电影画面 |
| visual_assets.py | 场景母版和分镜关键帧登记、版本与依赖失效检查 |
| visual_revision.py | Remix 单轴变化与 Result Repair 定向修复契约检查 |

提示词构造层内置 [ZY Cinematic Realism](vendor/zy-cinematic-realism/SKILL.md) v2.5.0，
通过 [MV 适配规范](references/zy-cinematic-integration.md) 接入 Story Visual Development、Story Source Ledger、
Scene Master、Continuity Bible、Shot Delta 与局部修复流程。缺少 `state/story_plan.json` 时只保留
`timing_only` 时间骨架，禁止静默退化成歌词动作模板。

## 环境依赖

交付音轨必须来自 `timeline.master_audio` 的用户原始母带。`source_audio` 可为 ASR/对齐用的 16 kHz 副本，但不得用于交付。合成直接复制可承载的原始 MP3/AAC 音频流；PCM/FLAC 等转为 ALAC 无损音轨。原采样率和声道保留，生成片段音轨不进入母带重贴版。音轨对齐 PASS 仅代表时间一致，不代表音质一致。

基础校验与剪辑：Python 3.10+标准库及ffmpeg/ffprobe。ASR另用Python 3.12与`requirements-asr.txt`（faster-whisper/av/ctranslate2兼容版本）；多模态观察用`requirements-perception.txt`；独立验收签名用`requirements-validation.txt`。安装与实际入口见[使用说明](references/gap-integration-guide.md)。**视频生成后端由用户提供，skill 只提问不内置**；但所选模型必须通过多模态全能参考能力门禁，普通文生视频、单首帧图生视频和仅角色库模式均不可用于正式生成。未提供合格后端时流程止步于关 6 + 静帧预览成片（见 `references/api-backends.md` 后端门限）。

## License

本仓库自有代码按 MIT 使用。`vendor/zy-cinematic-realism/` 为 ZY / popopo-99 的第三方作品，采用 **CC BY-NC 4.0**，包含非商业限制；再分发和使用时须保留署名、来源与许可证。详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

统一运行五项实际门禁：`python scripts/workflow_execution.py run-required PROJECT`。固定依赖顺序为 gate1 → gate5 → crew_pipeline → crew_rehearsal → gate6，首个失败即停止并列明未执行项；成功后还会重验全部当前回执。该命令不调用生成视频后端，不代表十二步全部完成，也不批准人工审片。
