# AI 剧组制工作流与视听语言门禁

## 目标

把 AIGC 自动化当成一个剧组，而不是一个会堆提示词的生成器。正式镜头必须依次完成：

1. 编剧：事件因果、人物目标、阻力、转折、观众新增信息。
2. 导演：场景目标、表演弧、演员调度和观看逻辑。
3. 摄影：物理机位、景别、机位高度、轴线、镜头行为与运动动机。
4. 灯光：来源光、阴影区、曝光优先级、色温关系和随剧情变化的灯光 cue。
5. 场景美术：前中后景、可行动空间、英雄道具、负空间、材质对比和场景自身动作。
6. 人物美术：剪影、服装状态、妆发与磨损连续性。
7. 演员调度：起止点、入出画方向、视线、运动轴与屏幕方向。
8. 剪辑：镜头职责、进入/离开关系、音乐切点、节奏类型、视觉变化与开场钩子。

导演风格位于这八个部门之后。王家卫、昆汀、诺兰等方法只能改变仍然开放的轴，不能替代轴线、地理、动线、光源和剪辑因果。

## GitHub 项目参考及吸收范围

- [OpenTimelineIO](https://github.com/AcademySoftwareFoundation/OpenTimelineIO)：吸收 Timeline → Track → Clip 的明确层级、时间范围与 namespaced metadata 思路。项目不复制其实现，也不新增运行依赖；仅把镜头、时间窗、剪辑关系和部门元数据分离。
- [Kitsu / CGWire](https://github.com/cgwire/kitsu)：吸收 Project → Sequence → Shot → Task、资产与部门任务分离、review/approved 状态的制作管理思路。
- [Wonder Unit Storyboarder](https://github.com/wonderunit/storyboarder)：吸收先布置人物、物体、灯光和摄影机，再检查构图与连续性的 Shot Generator 思路。
- [OpenFrame](https://github.com/kazim-45/openframe)：吸收 shot type、camera movement、lens、duration、action、dialogue 分栏记录的 shot list 思路。
- [CineTechBench](https://github.com/PRIS-CV/CineTechBench)：吸收景别、角度、构图、运镜、灯光、色彩和焦段七维摄影审计框架。
- [Baton Scene Description](https://github.com/digital-rain-tech/baton-scene-description)：吸收 session/beat 分层、人物 blocking、结构化光源和逐节拍状态表达。
- [Video Notation Schema](https://github.com/context-notation/video-notation-schema)：吸收全局资产、场景覆盖、灯光预设和转场字段分离的模式。

这些项目是结构参考，不作为本项目生成后端，也不引入它们的源代码或依赖。

## 数据入口

### `development.crew_bible`

八部门全片级交接物。`schema_version=2`，每个部门都必须有明确输出。`handoffs[]` 还要记录 `from / to / deliverable / acceptance`，至少覆盖编剧→导演、导演→调度、场景/人物美术→调度、调度/灯光/场景美术→摄影、摄影/导演→剪辑。剪辑部门另带 `pacing_policy`：

```json
{
  "max_average_shot_sec": 3.0,
  "slow_shot_threshold_sec": 4.0,
  "max_consecutive_slow_shots": 2,
  "max_consecutive_same_rhythm": 2,
  "hook_deadline_sec": 5.0,
  "max_hook_gap_sec": 12.0,
  "max_visual_stasis_sec": 6.0,
  "min_shot_size_variety_per_scene": 2,
  "max_consecutive_near_same_angle": 2
}
```

数值来自歌曲结构、目标受众和导演方案，不是模型时长上限。慢歌可以使用更长镜头，但必须显式给出策略，不能因为视频模型能生成 15/30 秒就让一个镜头占满整个生成包。项目策略可以加严；若宽于 `tempo_class`（fast/medium/slow）对应的默认 soft-max，必须同时提供 `pacing_exception.reason` 与 `evidence`。

### `development.scenes[]`

每个 Scene Master 除拓扑、光源图和材质规则外，必须有：

- `art_direction`：原有空间层次之外，增加 `set_history / color_script / scale_cue / background_activity`
- `lighting_plan`：原有来源光之外，增加 `key_direction / key_quality / fill_strategy / practicals / continuity_anchor`

灯光和场景美术不是“唯美、电影感、高级感”等形容词。它们必须说明可搭建的空间层、真实光从哪里来、什么区域不照亮、什么材质接住光，以及场景如何参与叙事。

P1 机检：`lighting_plan` / `art_direction` / 逐镜 lighting·production_design 命中禁词表（电影感/唯美/大片/高级感/氛围感等）即阻断；`key_direction` 必须使用受控方位枚举（如 `right_rear` / `右后侧` / `画面右后侧`）。`topology` 必须为对象：`nodes[]` 至少含入口或出口，以及 barrier/landmark/mark；节点可声明 `axis_id` 引用本场 `axes`；逐镜 `blocking.start_mark` / `end_mark` 必须等于已注册节点 id。

### `story_plan.shots[].crew`

逐镜记录八部门执行值。摄影和调度至少要能推导以下连续性：

- `viewpoint_id / shot_size / camera_height / camera_side / angle_deg / axis_id`
- `start_mark / end_mark / entry_direction / exit_direction / screen_direction / gaze_target`
- `incoming_relation / outgoing_relation / rhythm_class / music_cue / visual_change`
- 构图与光学：`focal_length_mm / focus_target / subject_placement / headroom / look_room / depth_strategy / frame_purpose`
- 灯光与美术：`key_direction / key_quality / fill_strategy / color_temp_k / set_dressing / material_response / color_accent / atmospheric_layer`
- 调度与剪辑：`body_orientation / action_path / eyeline_vector / crossing_rule / coverage_role / transition_type / cut_motivation / audio_transition / duration_intent`

## 视听语言硬门禁

`scripts/crew_pipeline.py` 在关 5 和章节编译前执行：

- 连续三个镜头不得复用相同场景、机位签名（含 `axis_id`、`angle_deg` 15° 分桶、`position_xy` 网格）、景别、高度、侧别和镜头行为。
- 同一轴线、同一侧的相邻镜头，机位变化不足 30° 时阻断——**景别变化不能豁免**；有意跳切必须明确 `deliberate_jump_cut + jump_cut_justification`。
- 同轴同侧且角度差过小的近同角度连续镜数默认 ≤2（`pacing_policy.max_consecutive_near_same_angle`，可配但受曲速 soft-max 约束）。
- 左右侧跨越视线/动作轴时必须先用中性镜或建立镜重置，并写 `axis_reset=true`。
- 上一镜出画方向与下一镜入画方向冲突时阻断，除非明确重置轴线。
- 新场景第一次出现时必须建立地理，不能直接用连续碎特写让观众猜位置。
- 相邻镜头必须同时给出上一镜 `outgoing_relation` 和下一镜 `incoming_relation`。
- `music_cue` 必须引用 `music_structure.cues[].id` 或 `song:` / `line:N:start|end` / `cue:ID` 锚点，且 cue 时间落入镜头窗口（约 1 帧容差）；缺少 `music_structure` 直接阻断。
- 平均镜头时长等 `pacing_policy` 不得无例外地宽于曲速默认 soft-max（`music_structure.tempo_class` 或 bpm→fast/medium/slow）；放宽须 `pacing_exception.reason` + `evidence`。
- 连续三个镜头必须产生新的视觉变化，不能只是同角度换一句歌词或重复人物坐姿。
- 头部空间与视线空间必须是 0..1 画幅比例，焦段和色温必须是有效物理数值；禁止只写“高级电影感”。
- 项目必须按自己的节奏策略限制钩子最大间隔和同一视觉事件持续时间。
- 每场至少达到项目指定的景别种类；叙事/表演场 ≥3 镜时硬性要求 establish/reestablish、action/develop、reaction/detail、turn/payoff 覆盖，不能只有连续正面中近景。

机器门禁只能排除明显错误，不能证明作品好看。正式样片仍需动态观看，检查表演、音乐呼吸、构图吸引力、灯光层次、空间可读性和剪辑情绪。



## 剧组基础门禁清单（P0–P2，机器可执行）

在进入 ZY 导演风格化（`style_application_order=after_film_grammar`）之前，下列硬约束必须通过：

| 部门/主题 | 机检要点 | 阶段 |
|---|---|---|
| 摄影/角度 | 三连签名含角度分桶+轴侧+位置；同轴同侧 30° 不因景别豁免；近同角度连续上限 | P0 |
| 覆盖 | 叙事/表演场 ≥3 镜：establish、action/develop、reaction/detail、turn/payoff | P0 |
| 剪辑/节奏 | music_cue 对齐结构；曲速 pacing soft-max + exception | P0 |
| 灯光/美术 | 禁空泛词；`key_direction` 枚举；topology.nodes ↔ axes/marks | P1 |
| ZY | 无导演可出片；有导演须 SHA/轴 + `crew_constraints` 对齐镜头 | P1 |
| 调度/动线 | continuous 比对 screen/eyeline；static 须 crossing_rule + 站位承接；非 continuous 须 time_relation_evidence；有意反向须 *_reverse_evidence；body_orientation 反向须 body_orientation_evidence；revision 须为 2；film_grammar 生产 enforce_envelope | P2/P3 |
| 注意力 | `novelty_axis` + `visual_change_code` 枚举与指纹 | P2 |
| 人物美术 | 交接物禁空泛审美词 | P2 |
| 审片/成本 | 正式提交走 `submission_contract`（含 animatic）；批量 ledger 须预演+样片批准 | P2 |

仍须人工：成片是否好看、animatic/director 证据与时间码、是否批准导演剪辑定稿、是否付费生成。机器只挡结构造假与明显跳变。

## 风格化顺序

1. 八部门结构通过。
2. 场景地理、轴线、人物动线、光源与美术层次锁定。
3. 导演路由选择一个主导演；混合时第二导演只控制一个轴。
4. 把导演特征翻译成机位、表演距离、曝光、色彩、空间和剪辑决定。
5. 检查风格没有破坏人物、服装、地理、动线和音乐时间轴。

`crew_bible.directing.style_application_order` 必须为 `after_film_grammar`。任何“先套导演风格，再补基础镜头”的方案都会被阻断。

指定 `style.director_ref` / `development.director` 时：除导演文件路径与 SHA、激活轴完整性外，必须提供可机检 `crew_constraints.exposure_bias` 与 `camera_height_policy`，并分别对齐到逐镜 `crew.lighting.exposure_bias`、`crew.cinematography.height_strategy`——把 ZY 指纹翻译成 crew 增量，而不是只加长 prompt。无导演可出片；制作桌在基础结构错误时 `director_stylization.eligible_for_routing=false`。
