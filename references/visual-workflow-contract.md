# V1.4.1 视觉开发执行契约

代码检查结构、权限、状态与版本，不代替 ZY 创作判断和实际审片。所有生成后端由用户提供；以下工具不发出生成请求。

当前总流程见[实际执行流程](actual-execution-workflow.md)。新项目先完成独立ZY故事节拍与电影参考组织，执行 `organization_workflow.py propose → select` 冻结连续性结构，再填写以下导演执行文件。

## 数据与执行顺序

1. `timeline.json`：权威歌词窗口和原始 `master_audio`。
2. `music_structure.json`：实际分析的段落与切点；`cues` 每项含 `id / time_sec / kind / evidence`。未知副歌标为待分析，不猜测。没有新增切点时可以仅引用歌词边界。
3. `development.json`：ZY 故事、参考规律、导演及场景开发记录。
4. `visual_edit_plan.json`：视觉镜头独立设计，引用歌曲/歌词/音乐切点，明确景别、演唱与切镜理由。
5. `story_plan.json`：每镜 Scene Master、来源节拍、连续性状态、导演执行决定与视频动作过程。
6. `development.organization_structure`绑定select返回的结构；执行`organization_workflow.py bind`核验四份导演设计并保存执行版本记录，再由`storyboard_build.py` 合并；`visual_development.py` 检查结构；`prompt_build.py` 编译三类提示词；关5/6通过后签发指纹。
7. 用户选定静帧后端后，Agent 编译原生提示词、生成母版候选、单轴比较、实际审查，登记批准母版与关键帧。批次和费用服从用户预算。
8. 用户选定视频后端并核验真实时长上限后构建章节、裁切母带资产、编译已批准关键帧对应的章节制作包。生成清单仍 `submission_ready=false`；外部适配层根据用户授权处理请求。
9. 代表样片完整观看；审片绑定媒体和上游依赖指纹，记录动态与段落证据；新难点针对性验证。
10. 已审素材登记、按帧选段，本地剪辑，再合成、对齐与音质检查。原始母带不能被分析副本替代。

## development.json

| 字段 | 最小内容与边界 |
|---|---|
| `treatment` | `concept / form / emotional_arc / visual_motif / climax / performance_rationale / status`；status=proposed 或 accepted，系统建议不能自动变成 accepted |
| `sources[]` | `id / locator / read_scope`，真实提供材料和已读范围 |
| `source_ledger[]` | `id / source_id / kind / description`；kind=fact、interpretation、proposal，proposal 另含 status |
| `references[]` | `id / kind / roles / observed_scope`；kind=character、visual_grammar、location 等；人物职责严格 likeness+wardrobe；visual_grammar 另含 medium 和 transfer_scope |
| `visual_rules[]` | 有视觉参考时记录5–8条 `id / reference_id / evidence / instruction / transfer`；transfer=strong 或 conditional，无视觉参考时留空 |
| `scenes[]` | `id / source_beat / topology / light_map / material_rules / master_prompt`，场景可行空间与母版描述 |
| `director` | 仅选择导演时填；`name / strength / source_file / source_sha256 / rationale / active_axes / active_traits` |

导演 `source_file` 是 vendor 内相对路径，例如 `references/directors/king-hu.md`。`source_sha256` 为文件 SHA-256，前缀 `sha256:`；来源文件变动后要重新核对。强度与 style.director_ref 一致。

四轴：`light_contrast / color_exposure / lens_camera / composition_space`。仅激活适用轴；轻微选择1–2个兼容特征。未激活轴标为 preserved 并保留 Scene Master，不填假导演效果。候选比较与参考观察由 Agent 执行，文件路径或指纹本身不能证明其审美执行成功。

所有可引用 ID 使用字母、数字、下划线或连字符。不要使用路径作为 ID。

## visual_edit_plan.json 示例

示例引用需要时间轴中存在对应歌词和已分析切点，不是可直接套到所有歌曲的模板。

```json
{
  "performance_policy": {},
  "shots": [
    {
      "id": "SH001",
      "scene_id": "gate",
      "start_ref": "song:start",
      "end_ref": "cue:first_accent",
      "shot_type": "全景",
      "lip_sync": false,
      "cut_reason": "重音与人物停步共同触发"
    },
    {
      "id": "SH002",
      "scene_id": "gate",
      "start_ref": "cue:first_accent",
      "end_ref": "song:end",
      "shot_type": "近景",
      "lip_sync": true,
      "cut_reason": "唱完后人物完成决定"
    }
  ]
}
```

引用形式为 `song:start/end`、`line:歌词index:start/end` 或 `cue:id`。音乐秒数只出现在来源分析文件，不在视觉镜头里自由填写。边界吸附一次、共享帧网格，末尾保留母带时长。

如确需项目口型目标，显式设置 `performance_policy.lip_ratio_range=[0.35,0.55]` 与 `consecutive_lip_limit=3`。未配置时不强制配额。景别白名单继续有效。无编辑方案只返回 `timing_only`，shots为空，不能继续编译。

## story_plan.json 逐镜字段

保留 `method=zy_story_visual_development`、`continuity_bible` 和完整 `shots`。

每镜包含 `id / scene_id / source_beat`，原有16项故事字段，以及：

- `story_intent / obstacle`：当前人物目标与实际阻力，禁止用情绪形容词代替戏剧动作。
- `keyframe_moment`：关键帧唯一当前瞬间；`current_action / motion_design` 继续描述整段视频过程，两者不得混用。
- `audience_information`：本镜新增可见信息与保留在画外的信息。
- `shooting_station / frame_boundary`：可画在场景平面上的摄影机站位，以及画框为何在这里截断人物、道具和空间。
- `visual_acceptance`：回图后能直接观察的构图、动作、信息层级和物理通过条件，不写“有电影感”这类不可验收目标。

- `state_before / state_after`：状态字典，下一镜前态等于上一镜后态；回忆或时间跳跃也明确追踪转换，不能因换场景而重置服装、道具和人物知识。
- `active_rule_ids`：有 Decode Card 时选择3–5条相关规则，不贴全卡。
- `director_decisions`：逐镜、逐激活轴给出 `instruction / reason / observable_check`。不得扩大激活轴或覆盖用户锁。
- `motion_design`：`start_state / action / physics / camera_motion / end_state`，写清准备、接触、受力与余波；只给当前镜头可执行的主动作。

可选 `coverage_plan[]` 只登记补充镜头候选：`id / purpose / keyframe_moment / selection_status`。`selection_status=selected` 时必须先给出 `materialized_shot_id`，并把它落实为 `visual_edit_plan` 中拥有独立音乐引用、景别、口型与切镜理由的正式镜头。候选不能在章节编译时临时混入口型镜头。

`lyric_lines` 自动从视觉窗口与原时间轴相交计算，不手工改写。多行歌词分别成行；完整行文本是上下文，实际口型服从音频裁切窗口，不能重新唱出已切掉部分。

## 分层制作包与资产

- `prompts/scenes/*.json`：场景母版与物理空间。
- `prompts/keyframes/SH*.json`：当前瞬间、人物/服装、场景、机位、视觉规律和导演方法。
- `prompts/motion/SH*.json`：人物锁、动作起止、物理过程、音频与剪辑意图。
- `prompts/SH*.json`：供关5检查的模型中立总记录。
- `state/visual_assets.json`：登记真实母版和关键帧文件；包含文件指纹、观察证据、作用场景、母版引用与相关设计依赖。

场景母版修改，引用该母版的关键帧需复核；不相关地点的设计改变不自动使其他场景母版失效。共同 treatment/导演/参考视觉/连续性锁改变可能影响多个地点。工具报告需复核，不删除或自动重新生成用户已接受图片。

Remix 使用两个 Scene Master JSON 比较一个变量；局部修复计划包含 `dominant_failures[]`（dimension、classification=actual_drift、evidence、cause）、`structural_failure / action / change_only / preserve_exactly`。结构失败的 action=rebuild_scene_master，局部问题为 surgical_repair。

## 命令

```bash
python scripts/storyboard_build.py PROJECT --fps 24
python scripts/visual_development.py PROJECT
python scripts/crew_pipeline.py PROJECT
# 可选；PROFILE 必须由合法 annotation-only 语料构建
python scripts/film_grammar_corpus.py audit PROJECT PROFILE
python scripts/prompt_build.py PROJECT
python scripts/gate_checks.py gate5 PROJECT
python scripts/gate_checks.py gate6 PROJECT

# 用户所选静帧后端生成图片后，实际审查并登记；此命令不看图、不生成。
python scripts/visual_assets.py register PROJECT --id MASTER_GATE --kind scene_master --scene gate --path IMAGE_PATH --evidence REVIEW_EVIDENCE
python scripts/visual_assets.py approve-static PROJECT --id MASTER_GATE --observed-sha256 IMAGE_SHA256 --observer REVIEWER --evidence ACTUAL_STATIC_OBSERVATION
python scripts/visual_assets.py register PROJECT --id SH001 --kind keyframe --scene gate --path KEYFRAME_PATH --parent MASTER_GATE --evidence REVIEW_EVIDENCE
python scripts/visual_assets.py approve-static PROJECT --id SH001 --observed-sha256 KEYFRAME_SHA256 --observer REVIEWER --evidence ACTUAL_STATIC_OBSERVATION
python scripts/visual_assets.py check PROJECT
python scripts/visual_revision.py remix BASE_JSON --candidate VARIANT_JSON --axis camera
python scripts/visual_revision.py repair REPAIR_PLAN_JSON

# 时长上限由用户后端实测契约提供，没有默认模型或时长档位。
python scripts/chapter_plan_build.py PROJECT --max-duration VERIFIED_LIMIT
python scripts/chapter_audio_build.py PROJECT
python scripts/chapter_prompt_build.py PROJECT

# 外部适配层生成样片后，再初始化审片（避免准备音频/提示词改变依赖）。
python scripts/director_review_gate.py init PROJECT
# 完整观看并填写 reviewed_media / reviewed_media_sha256、九项导演与四项段落检查、时间码证据。
python scripts/director_review_gate.py check PROJECT

python scripts/edit_assemble.py PROJECT
python scripts/edit_assemble.py PROJECT --render
python scripts/stitch_chapters.py PROJECT
```

## 素材与剪辑

`generated_clips.json` 的 `clips` 字典按素材ID索引，每项含 `path / sha256 / fps / frame_count / scene_id / status=reviewed / evidence`。来自用户选择的后端，记录真实下载素材。

`edit_selection.json` 的 `selections` 每项含 `shot_id / clip_id / in_frame / out_frame / edit_reason / state_check`。out_frame为排他终点。区间与目标镜头帧数一致，不能为了模型15秒上限整段套入成片或默默变速。

`edit_assemble --render` 校验实际帧率与长度后裁切到 `clips/edit/SH*_fitted.mp4`。末镜吸收全曲非整数帧残差，视频总帧数=floor(母带时长×fps)。`stitch_chapters` 优先使用已完成的 edit_assembly，核对分镜、选段和产物指纹，再贴原始母带。没有 edit_assembly 的旧章节合成仍可用于历史产物，但旧审片不能直接放行新设计。

## 迁移

旧项目不自动改剧情、窗口或资产。旧 storyboard 需按本契约补充 visual_edit_plan、development、state_before/after、motion_design，以及 V1.5 的 `crew_bible`、场景美术/灯光和逐镜 `crew` 后重建；提示词重新过关5，图片重新核对并登记。原媒体保留。电影语法画像是可选证据层，不会自动改写旧镜头。

艺术创作与实际观察由 Agent/用户完成；工具不会宣称靠字段检查就获得“爆款质感”。对应回归覆盖音乐引用、跨行歌词、导演缺失/强度、连续性、资产失效、三层提示词、后端中立与素材选段。

口型正式视频准备另有时间权威门禁：`timeline.production_timing_verified=true`、`pending_review=[]`，且不得带 simulation/provisional/fallback 权威标记。未满足时仍可继续故事与静态视觉开发，但 `chapter_plan_build.py` 必须阻断口型章节。
