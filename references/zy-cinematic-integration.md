# ZY Cinematic Realism → MV 提示词构造适配

新项目执行顺序以[当前实际执行流程](actual-execution-workflow.md)为准；本文件说明对应阶段的细节。

V1.4.1 执行顺序和数据契约见 `visual-workflow-contract.md`，导演级逐镜写法见 `director-scene-master.md`：故事、参考视觉、导演/摄影三链协同，不再把导演方法局限为 style 词汇或把歌词窗口等同视觉镜头。

> 上游：[`popopo-99/zy-cinematic-realism`](https://github.com/popopo-99/zy-cinematic-realism)，内置版本 v2.5.0（提交 `9a3b36d1fb6a980ae9e21f74c86dd56bae443a4d`）。上游负责“画面是什么、如何被观看”；MV 流水线负责“画面何时发生、是否对口型、唱哪句、持续多久”。

## 一、职责边界

| 数据/决定 | 权威来源 | ZY 层权限 |
|---|---|---|
| 歌词、窗口、时长、切点 | `timeline.json` / `storyboard.json` | 只读，禁止改写 |
| `lip_sync`、`shot_type`、连续口型上限 | `storyboard.json` | 只读，机位只能在既定景别内细化 |
| 风格、服装、色板、场景母版 | `style.json` | 读取并扩展为 Scene Master，不得覆盖锁定事实 |
| 人物身份、服装与参考职责 | 资产指纹 + `reference_duties` | 主人公图锁 `likeness + wardrobe`，不继承构图、背景或灯光 |
| 动作、调度、空间层次、实际光源、材质 | ZY Scene Master | 可构造，但必须服务当前歌词窗口与分镜角色 |
| 歌词原文、闭口声明、负面口型词 | MV 六段式规范 | ZY 层不得润色、翻译或删除 |
| 模型/平台参数 | 实际视频后端契约 | 未知时保持模型中立，不套用静帧模型参数 |

冲突优先级：

`用户明确锁定 > timeline/storyboard 硬事实 > style/资产连续性 > Scene Master > Creative Grammar > 模型措辞`

## 二、逐镜构造顺序

1. **冻结 MV 硬事实**：复制 `shot.id / window / duration_sec / lyric_line / lyric_text / shot_type / lip_sync / generation_role`，后续只读。
2. **恢复全片连续性**：从 `style.json`、人物指纹、场景母版和上一镜状态建立 Base Lock；跨镜复用服装、道具、空间拓扑、光源方向与材质状态。
3. **建立 Scene Master**：为当前镜头确定一个具体时刻、人物物理动作、前中后景、可用空间、实际光源、物体状态与可实现的摄影机见证位置。
4. **应用 Creative Grammar**：仅按需求选择导演、风格、摄影或 Dream Decode 分支；不默认堆叠全部卡片，不复制特定电影镜头。
5. **模型编译分流**：
   - 制作参考帧：可按 `vendor/zy-cinematic-realism/references/model-routing.md` 选择静帧适配器。
   - 生成视频：默认输出模型中立的物理描述；只有拿到真实视频后端字段表后才转成后端原生格式。
6. **降级映射到六段式**：按下表写入 `style → scene → subject → performance → camera`，最后由 MV 层独立写 `audio_policy` 与歌词。
7. **清理与门禁**：应用 anti-AI cleanup，去掉无因果的雾、轮廓光、广告摆拍和空泛质量词，然后运行 `gate_checks.py gate5`；20 项未全绿不得提交生成。

### `state/story_plan.json` 最小契约

`storyboard_build.py` 无 visual_edit_plan 时只交付时间骨架，不确定景别、口型或后端。Agent 必须按上游
Story Visual Development 编写 `state/story_plan.json`，再运行一次 `storyboard_build.py` 合并。
文件必须声明 `method: "zy_story_visual_development"`，包含 `continuity_bible`，且 `shots`
必须与时间分镜镜头 ID 完整一致。每镜至少包含：

- `id`、`scene_id`、`source_beat`；`lyric_lines` 由时间轴相交关系推导
- `story_beat`、`narrative_before`、`current_action`、`implied_next`
- `space`、`blocking`、`props`、`light`、`camera_witness`
- `visual_center`、`information_hierarchy`、`gaze_target`
- `action_physics`、`material_response`、`edit_trigger`
- `story_intent`、`obstacle`、`keyframe_moment`、`audience_information`
- `shooting_station`、`frame_boundary`、`visual_acceptance`
- `shot_delta`
- `state_before / state_after`、`motion_design`、按需选择的 `active_rule_ids / director_decisions`

故事层只能补充这些字段，不得覆盖 `window / lyric_line / lip_sync / shot_type / generation_role`。
缺少故事方案时 storyboard 会标记 `story_mode: timing_only`，`prompt_build.py` 必须阻断，
不能悄悄退化成“歌词 + 通用动作”的模板提示词。

## 三、Scene Master 到六段式字段映射

| ZY 构造结果 | MV 字段 | 约束 |
|---|---|---|
| 媒介、色彩/曝光、材质规律、导演四轴的已选部分 | `style` | 必须与 `style.json` 指纹和关键词一致 |
| 空间拓扑、前中后景、实际光源、道具与使用痕迹 | `scene` | 命中已锁定场景母版词；不凭空换景 |
| 身份、服装、身体位置、与道具关系 | `subject` | 主人公图承担 `likeness + wardrobe`，不得接管构图、背景、灯光或画风 |
| 单一具体动作、姿态、视线、关系变化 | `performance` | 口型段歌词原文独立置于段尾；非口型段不出现唱歌标记 |
| 物理可行机位、既定景别内的距离/高度、运动与焦点行为 | `camera` | 最后派生；不得更改 `shot_type` |
| 视觉中心、信息层级、动作受力、材质响应、切镜原因、导演四轴 | `direction` | 导演姓名不作为执行指令；必须翻译为可观察、可机检的决策 |
| 无对应项 | `audio_policy` | 完全由 MV 层根据 `lip_sync` 写入 |
| 场景特定排除项 + 口型排除项 | `negative_prompt` | 不粘贴泛化负面词墙；MV 口型规则优先 |

## 四、按需加载路由

不要每镜读取整个上游库。步骤 5 最小加载集合：

- 普通电影化镜头：`cinematic-principles.md`、`camera-and-light.md`、`anti-ai-cleanup.md`、`quality-checklist.md`。
- 连续镜组：另加 `continuity-cards.md`，以 Base Lock + Shot Delta 工作。
- 用户指定导演：另加 `director-routing.md`、导演索引和一个目标导演文件。
- 参考图风格迁移：按 `reference-role-router → medium-router → dream-decode` 路由。
- 回图修复：读取 `result-repair.md`，最多处理三个主导问题，并保留其他已成功事实。
- 参考帧模型编译：只加载目标模型对应适配器；视频提示词不默认继承 Midjourney、GPT Image、Seedream 或 Nano Banana 参数。

## 五、失败保护

- ZY 层若建议的机位与 `shot_type` 冲突，保留 `shot_type`，只在白名单范围内调整机位。
- ZY 层若建议人物开口，但 `lip_sync=false`，删除开口/唱歌描述并注入闭口规则。
- 导演或参考图规则若改变场景母版、人物身份或歌词动作，视为漂移并拒绝。
- 一次修复超过三个主导问题，返回 Scene Master 重建，不继续堆叠修饰词。
- 任何提示词变动都会使 `prompt_fingerprint` 失效，必须重新过关 5。
- 章节生成前必须先编译模型中立导演制作包；用户选定后端并由外部适配层按真实 schema 转码后，只允许先生成代表样片，完整动态审片通过前不得解锁批量生成。

## 六、许可与来源

`vendor/zy-cinematic-realism/` 保留上游原始 `LICENSE`、`NOTICE.md` 和文件级版权头，采用 CC BY-NC 4.0。该目录的非商业限制不因被放入本仓库而改变；商业项目使用前应另行取得上游作者授权。


## 七、P1：导演风格后置于语法，并落到可机检 crew 增量

1. `crew_bible.directing.style_application_order` 固定为 `after_film_grammar`；`crew_rehearsal` 在结构错误时将 `director_stylization.eligible_for_routing` 设为 false，禁止先套导演再补基础镜头。
2. 无 `style.director_ref.name` 时不注入导演方法，项目仍可出片。
3. 有导演时继续核验 `source_file` + `source_sha256` + 激活轴/`director_decisions`；并额外要求 `development.director.crew_constraints`：
   - `exposure_bias`：`protect_highlights` / `protect_faces` / `broad_midtones` / `practical_adaptation` / `dense_blacks` / `neutral`
   - `camera_height_policy`：`eye_level` / `low` / `high` / `grounded_threshold` / `participant` / `sit_eye_level`
4. 上述约束必须写进逐镜 `crew.lighting.exposure_bias` 与 `crew.cinematography.height_strategy`，与 ZY 卡中的曝光/机位高度指纹对应；不得用导演姓名或空泛“电影感”代替。
