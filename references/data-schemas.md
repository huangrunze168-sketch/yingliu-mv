# 数据 Schema 规格

项目执行策略`state/execution_policy.json`含project_id、严格布尔video_backend_access和原用户instruction记录；缺配置默认禁止，当前真实项目为false。`state/mcp_calls/CALL_ID/`保留call、完整response与tool_result；call含原进程创建标识、一次领取、实际参数SHA和nonce。作业request_archive_path指向不可改写的原请求字节；Lovart终态task_binding_kind=request_context明确以原任务调用关联，不假称原响应含任务ID。更多边界见[MCP交接契约](mcp-handoff-contract.md)。

新增`media_origins.kind=derived`：含parent_clip、冻结plan_path/plan_sha256、repair_snapshot_path/SHA、execution_path/execution_sha256、compiled、repair_id及repair_plan_sha256。compiled保留精确fps_ratio、逐段源帧映射、源/输出画布、upscaled及aspect_stretched；回执绑定实际命令、进程结果和选段声音PCM匹配。新素材pending，章节批准并close以后才reviewed，详见[本地变换契约](local-transform-contract.md)。

交付新增`state/delivery_packages/OUTPUT.mp4.json`，绑定export_id、specification_sha256、实际输出SHA、所有包成员/依赖和ZIP。`state/final_media_review.OUTPUT.mp4.json`绑定最终文件、时间轴、分镜与逐镜窗口/口形观察，默认pending。`delivery_state`的technical_verified不能直接视为ready；ready必须保存当前delivery_package核验结果和verified_versions。嘴型独立测量仍为false，详见[交付包契约](delivery-package-contract.md)。

`state/repairs/REPAIR_ID.json`保存冻结失败媒体、来源/请求快照、失败审片、计划SHA及新任务修复结果；状态仅open/resolved。多轮历史通过supersedes及历史SHA连接。`state/repair_assessment.json`绑定当前逐章/全片批准、选段、素材和返修版本，结论为no_registered_repairs或all_repairs_resolved，均不授予艺术批准。正式交付缺此当前汇总即失败，详见[返修状态契约](repair-state-contract.md)。

正式`generated_clips.json`每个clip必须含`origin_receipt_path`，指向`state/media_origins/*.json`。`kind=generated`绑定原completed作业、原生输出URL指针、HTTP文件SHA、原契约与视频SHA；`kind=retained`绑定明确用户授权原文、授权文件SHA及用途。两类登记均保留`artistic_approval=false`、`dynamic_verified=false`，不代替章节审片。正式`edit_assembly.json.origin_versions`保存全部来源依赖版本，合成与交付重新核验。详见[素材来源契约](media-origin-contract.md)。

> 所有状态文件 JSON，UTF-8，字段名英文（铁律 C 语言分层）。时间单位一律秒（float），禁止字符串时间码进状态层。

V1.4.1 以 `visual-workflow-contract.md` 和 `director-scene-master.md` 为新增视觉开发/资产/剪辑数据契约。下面的旧示例仅说明通用字段；新项目使用 schema_version=2、timing_mode=music_referenced_visual_edit、timing_refs、lyric_lines、scene_id 与显式 performance_policy。

## 1. state/timeline.json —— 权威时间基准（关 1 产物）

```json
{
  "song": "demo_song",
  "source_audio": "input/demo_song.mp3",
  "master_audio": "input/demo_song.mp3", // 用户原始母带；source_audio 可为分析副本，交付只读 master_audio
  "duration_sec": 245.32,
  "fps_assumed": 24,
  "asr_engine": "volc-audio-align",
  "lines": [
    {
      "index": 1,
      "type": "vocal",            // vocal | harmony | bridge | interlude
      "text": "画不完的圆",
      "start": 12.40,             // ASR 给时间
      "end": 16.08,
      "confidence": 0.97,         // ASR 置信度；官方歌词校正后标记 corrected
      "corrected": true           // 是否被官方歌词改写过
    },
    {
      "index": 2,
      "type": "interlude",        // 间奏显式标出（铁律 A：间奏决定分镜切点）
      "text": "",
      "start": 16.08,
      "end": 24.50,
      "confidence": 1.0,
      "corrected": false
    }
  ],
  "segments": [                    // 歌曲结构段（verse|chorus|bridge|intro|outro）
    { "id": "S01", "role": "verse", "line_indexes": [1, 2], "start": 12.40, "end": 45.10 }
  ],
  "lyrics_source": "official",     // official | asr_only
  "pending_review": [],            // 待核行：confidence < 0.85 的行，需用户点头
  "production_timing_verified": true // 只有权威对齐并完成人工待核后才能设为 true；正式口型章节硬门禁
}
```

`source_audio` 可以是分析副本，`master_audio` 才是交付母带。`simulation_only=true`、`authority` 含 provisional/pending/diagnostic/fallback、`pending_review` 非空，或缺少 `production_timing_verified=true` 时，仍可做视觉开发，但不得准备正式口型章节。

上述字段不是来源认证。正式生产还须提供`timing_source.execution_path`和`timing_source.review_path`，绑定原始ASR、原曲/歌词SHA及当前时间轴的逐行复核；详见[timing-evidence-contract.md](timing-evidence-contract.md)。真实识别与草稿生成不会自动设置批准。

导出规则：`.lrc` 由 lines 生成，`[mm:ss.xx]text`；interlude 行不写文本。

## 2. state/style.json —— 风格锁定（关 2 产物，先于资产）

```json
{
  "style_name": "东方器物 · 电影化",
  "visual_keywords": ["水墨质感", "低饱和", "侧逆光"],
  "palette": { "primary": "#1a1a1a", "accent": "#c8a86b" },
  "wardrobe": "素色棉麻长衫",
  "aspect_ratio": "9:16",
  "resolution": "1080x1920",
  "scene_master": {                 // 跨镜场景事实锁（可选，锁定后关5机检#13生效）
    "locked": true,
    "space": ["昏暗书房", "木格窗"],
    "light_sources": ["铜灯烛火", "窗纸月色"],
    "props": ["黄铜罗盘", "翻开的案卷"],
    "palette_locked": true
  },
  "director_ref": {
    "name": "用户选择的导演参考",
    "intensity": "轻微",
    "axes": {
      "light_contrast": "由所选导演文件推导的场景级决策",
      "color_exposure": "由所选导演文件推导的场景级决策",
      "lens_camera": "由所选导演文件推导的场景级决策",
      "composition_space": "由所选导演文件推导的场景级决策"
    },
    "active_traits": ["只保留与本场景兼容的可执行特征"]
  },
  "fingerprint": "sha256:...",    // 锁定后指纹化；提示词派生必须引用同一指纹
  "locked": true
}
```

## 3. state/storyboard.json —— 分镜表（关 4 产物）

```json
{
  "shots": [
    {
      "id": "SH001",
      "segment": "S01",
      "lyric_line": 1,             // 对应 timeline.lines.index；间奏镜可为 null
      "window": { "start": 12.40, "end": 16.08 },  // 从歌词边界或有证据的音乐切点引用推导
      "shot_type": "特写",          // 大特写|特写|近景|中景|中远景|全景|远景|空镜|背影|群像
      "lip_sync": true,            // 只有前四类允许 true（铁律 B）
      "role": "perform",           // perform | narrative | insert
      "visual_concept": "主人公垂眸看手中罗盘，烛光晃动",
      "camera": "缓慢推近",
      "generation_role": "lip_sync", // lip_sync | narrative；不是后端名称
      "prompt_file": "prompts/SH001.json",
      "prompt_fingerprint": null,  // 关5通过后写入；重抽卡时比对失效则退回
      "repair": {                  // 局部修复判定：重抽前先判能否局部修
        "mode": "none",            // none | partial | full_reroll
        "problems": []             // 最多 3 个主导问题，每项 {"field": "...", "desc": "..."}
      },
      "status": "pending"          // pending|generated|fitted|verified|failed
    }
  ],
  "performance_policy": {}         // 比例与连续上限仅在项目明确指定时启用
}
```

V1.4.1 的 `story_plan.shots[]` 还必须提供 `story_intent / obstacle / keyframe_moment / audience_information / shooting_station / frame_boundary / visual_acceptance`。静帧只消费 `keyframe_moment`；整段运动留在 `current_action / motion_design`。详见 `director-scene-master.md`。

`visual_assets.json` 中的 `status=approved` 仅表示带证据的静态结构登记；V1.4.1 同时记录 `review_scope=static_structure / agent_inspected / user_accepted / dynamic_verified / quality_claim`，避免把登记误读为用户接受或动态艺术验收。

### V1.5 AI 剧组字段

`development.json` 必须包含 `crew_bible.schema_version=2`，并分别提供 `screenwriting / directing / cinematography / lighting / production_design / character_design / blocking / editing` 八部门交接物。`handoffs[]` 为部门之间的 `from / to / deliverable / acceptance` 交接图。导演部门的 `style_application_order` 固定为 `after_film_grammar`。

可选 `grammar_reference` 绑定匿名电影语法画像：`profile_path / profile_sha256 / profile_id / mode / rationale / use_scope`。`profile_path` 必须是项目内相对路径；`mode=advisory` 只报告，`mode=enforce_envelope` 会阻断样本不足或镜长越界。画像指纹变化后必须重新批准，不能静默换库。

V1.6.1：所有正式编译入口均核验已声明画像；无画像时省略 `grammar_reference`，不填空对象。资产设计指纹包含 `crew_bible`，修改全片灯光等部门策略需重新复核依赖资产。

每个 `development.scenes[]` 增加：

- `art_direction`：`foreground / midground / background / scenic_action / hero_prop / negative_space / material_contrast / set_history / color_script / scale_cue / background_activity`
- `lighting_plan`：`primary_source / secondary_source / shadow_zones / exposure_priority / color_temperature_relation / cue_changes / key_direction / key_quality / fill_strategy / practicals / continuity_anchor`

每个 `story_plan.shots[]` 增加 `crew`，其中摄影必须记录 `viewpoint_id / shot_size / camera_position / camera_height / camera_side / angle_deg / lens_behavior / camera_movement / movement_motivation / axis_id`；调度记录起止点、入出画方向、视线与屏幕方向；剪辑记录镜头职责、前后关系、节奏、音乐 cue、视觉变化、地理状态和 hook。完整定义见 `references/crew-production-pipeline.md`。

## 4. prompts/SHxxx.json —— 六段式提示词（关 5 校验对象）

```json
{
  "meta": {
    "shot_id": "SH001",
    "window": { "start": 12.40, "end": 16.08 },
    "duration_sec": 3.68,
    "shot_type": "特写",
    "lip_sync": true,
    "generation_role": "lip_sync",
    "lyric_line": 1,
    "lyric_text": "画不完的圆",
    "style_fingerprint": "sha256:...",
    "character_fingerprint": "sha256:...",
    "reference_duties": ["likeness", "wardrobe"]  // 人物图仅锁五官/发型/服装，不继承构图背景灯光
  },
  "style": "低饱和水墨质感，侧逆光，东方器物美学",
  "scene": "昏暗书房，烛火在铜灯里摇曳，窗纸透出月色",
  "subject": "素色棉麻长衫的年轻女子，眉眼低垂",
  "performance": "女子缓缓抬手抚过罗盘边缘，唇形随歌词开合\n画不完的圆",
  "camera": "中近景，缓慢推近，浅景深",
  "audio_policy": "人声驱动口型，逐字对齐歌词「画不完的圆」",
  "negative_prompt": "念白, 字幕, 水印, 多人, 面部变形"
}
```

**派生顺序硬规矩**：style→scene→subject→performance 先成立，camera 最后派生——先让画面成为故事中的具体瞬间，再考虑镜头与胶片词。

**不对口型段的 audio_policy 必须含**：「嘴唇自然闭合、不跟随人声开合」，且 negative_prompt 必须含口型类词（`张嘴说话|对口型|lip movement`）——机检第 5 项。

## 5. prompts/chapters/CHxx.json —— 全能参考章节蓝图

`chapter_prompt_build.py` 会在模型中立章节包中写入 `omni_reference`。该对象只定义参考素材职责和逐镜时间轴，不包含供应商字段，也不提交生成任务。

```json
{
  "omni_reference": {
    "schema_version": 1,
    "mode": "omni_reference_blueprint",
    "backend": null,
    "adapter_status": "not_in_scope",
    "subject_definitions": [
      {
        "subject_id": "protagonist",
        "handle": "@图片 1",
        "retain": ["五官比例", "发型", "耳饰", "同一套服装的结构与材质"],
        "reject_transfer": ["background", "composition", "lighting", "pose"]
      }
    ],
    "reference_bindings": [
      {
        "handle": "@图片 1",
        "modality": "image",
        "source": {"local_path": "input/protagonist.png", "sha256": "..."},
        "intents": ["character_identity", "wardrobe"],
        "do_not_inherit": ["background", "composition", "lighting", "pose"]
      },
      {
        "handle": "@图片 2",
        "modality": "image",
        "shot_id": "SH001",
        "source": {"local_path": "clips/keyframes/SH001.png", "sha256": "..."},
        "intents": ["composition", "location", "blocking_start_state"],
        "do_not_inherit": ["character_identity", "wardrobe", "performance_timing"]
      },
      {
        "handle": "@音频 1",
        "modality": "audio",
        "source": {"local_path": "input/chapter_audio/CH01.wav", "sha256": "..."},
        "intents": ["performance_timing", "action_timing"],
        "condition_only": true,
        "generate_audio": false,
        "final_master_replaced_locally": true
      }
    ],
    "timeline": [
      {
        "shot_id": "SH001",
        "start_sec": 0.0,
        "end_sec": 2.0,
        "reference_handles": ["@图片 1", "@图片 2", "@音频 1"],
        "editorial_role": "建立线索",
        "cut_relation": "视线匹配"
      }
    ],
    "audio_policy": {
      "reference_audio_is_condition_only": true,
      "generate_audio": false,
      "delivery_audio": "timeline.master_audio"
    }
  }
}
```

人物图只允许承担 `character_identity + wardrobe`；逐镜关键帧只承担构图、地点与起始调度；章节音频只承担表演和动作时序。`scripts/omni_reference.py` 对这些边界、时间连续性、引用绑定和母带回贴策略执行机检。

## 6. state/ledger.jsonl —— 成本台账（追加制）

```json
{"ts": "2026-10-12T14:00:00", "action": "submit", "shot_id": "SH001", "backend": "user-selected-provider", "coins": 0, "cash_cny": 1.20, "note": "首镜试产"}
```

字段固定 8 个：`ts / action / shot_id / backend / coins / cash_cny / duration_sec / note`。`action ∈ submit|retry|salvage|fail_refund|fit|concat`。汇总口径见 `scripts/ledger.py`。

## 7. output/verification_report.json —— 对齐三验报告（关 7/8）

```json
{
  "check1_total_duration": { "clips_sum": 245.31, "song": 245.32, "diff": 0.01, "pass": true },
  "check2_waveform_lag": [
    { "shot_id": "SH001", "expected_start": 12.40, "best_lag": 0.02, "correlation": 0.83, "pass": true }
  ],
  "check3_vocal_energy": [
    { "shot_id": "SH001", "lip_sync": true, "energy": 0.117, "pass": true }
  ],
  "overall": "PASS"
}
```

阈值：check2 `|lag| < 0.15s` 且 `correlation > 0.6`；check3 `energy > 0.05`（全混代理，可按曲调参）。
