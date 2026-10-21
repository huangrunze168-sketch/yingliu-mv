# 六段式提示词记录 + 分层制作包 + ZY 依赖检查（V1.4.1）

> 铁律 C 的完整落地。六段式结构 × 导演 Agent 出稿 × 派生顺序纪律（具体瞬间 / 场景母版 / 导演轴 / 参考职责 / 局部修复判定）。

## 零、电影化构造前置层

有用户对标时，先读 [对标氛围转译与提示词细化](benchmark-atmosphere.md)。把拉片中已观察到的空间压力、光色、现场反应、摄影距离及声画关系落实到下述六段式；逐镜与章节提示词不得把它们压缩成题材标签。细节充分性以可执行和可观察为准，不以字数为准。

每镜先按 [`zy-cinematic-integration.md`](zy-cinematic-integration.md) 调用内置
`vendor/zy-cinematic-realism/`：建立 Scene Master，选择必要的 Creative Grammar，完成具体瞬间、人物调度、空间层次、实际光源与物理可行机位。然后才把结果映射到下述六个字段。

该前置层没有时间轴权限：不得修改 `window / duration_sec / lyric_line / lyric_text / lip_sync / shot_type / audio_policy`。上游模型适配器面向静帧；只有在制作参考帧时使用其模型原生语法，视频生成提示词默认采用模型中立表达，并服从实际视频后端契约。

## 一、六段式结构（一个字段都不能少）

| 段 | 字段 | 语言 | 派生顺序 | 内容要求 |
|---|---|---|---|---|
| 1 风格 | `style` | 中文 | ① | 与 style.json 视觉关键词一致；有导演轴时在此体现（见下），不得私自加新风格词 |
| 2 场景 | `scene` | 中文 | ② | **从场景母版实例化**：空间、实际光源、道具必须有来源（母版锁定词），不得另起炉灶 |
| 3 主体 | `subject` | 中文 | ③ | 人物外观与 wardrobe 一致（无主角模式写环境主体） |
| 4 表演 | `performance` | 中文 | ④ | **一个具体瞬间**：一个动作 + 一个情绪落点，动作/空间/光源先成立；口型段歌词独立成行放段尾 |
| 5 镜头 | `camera` | 中文 | ⑤ 最后 | 景别 + 运镜 + 焦段——**镜头胶片词最后加入**，不得挤压叙事段 |
| 6 音频策略 | `audio_policy` | 中文 | ① 同步 | 口型段写驱动方式；非口型段写闭口声明 |
| 附加 | `negative_prompt` | 英文优先 | — | 平台负面词 |

**派生顺序是硬规矩**：style→scene→subject→performance 先成立，camera 最后写——先定「谁在此刻做什么、光从哪来」，再考虑镜头与胶片词。字段名、标签、枚举值全部英文；叙述与歌词全部中文。

## 二、场景母版 —— 跨镜事实锁

style.json 中锁定一次，全片 25+ 镜复用：

```json
"scene_master": {
  "locked": true,
  "space": ["昏暗书房", "木格窗"],
  "light_sources": ["铜灯烛火", "窗纸月色"],
  "props": ["黄铜罗盘", "翻开的案卷"],
  "palette_locked": true
}
```

- 每镜 `scene` 段**必须命中至少一个**锁定空间/光源/道具词（机检 #13）。
- 换生成后端时母版原样带走，重写的只有措辞；场景事实不随后端切换漂移。

## 三、导演参考轴（可选）

style.json 可挂 `director_ref`，强度三档：轻微 / 明确 / 强烈。真实方法记录在 development.director 与逐镜 director_decisions，规则：

1. 根据实际导演文件决定开放的光/色/机位/空间轴，并在逐镜记录场景理由与观察依据；指定姓名而没有决策时拒绝编译；
2. **永不覆盖**已锁定事实：窗口、切点、景别（铁律 A/B）与场景母版（光源/空间）优先于导演轴；
3. 不指定则不注入，保持模型中立。

轻微只选1–2个兼容特征，未激活轴保留场景事实；不能用通用四轴掩盖缺失路由。场景母版、分镜关键帧和视频运动提示词分别编译，完整数据与命令见 `visual-workflow-contract.md`。

## 四、参考职责分离

`meta.reference_duties` 声明每张参考图只管什么：

```json
  "reference_duties": ["likeness", "wardrobe"]
```

- 枚举：`composition / color / lighting / material / likeness / wardrobe / none`（机检 #14）。
- 主人公图必须同时承担 `likeness + wardrobe`：锁定五官比例、发型、耳饰、服装版型/纹样/层次；不得继承参考图的构图、背景和灯光。风格参考图只取「规律」，**不搬内容**。
- 人物参考服装优先于系统补全的风格服装；两者冲突时保留参考服装，除非用户明确要求换装。身份与服装锁写入 Continuity Bible，并传入逐镜和章节制作包；表情、动作、衣料运动与受光变化按叙事自然变化，不锁成静态姿势。
- 关 3 资产关执行同一规则：上传时即登记职责，指纹缓存按「指纹+职责」双键。

## 五、唱歌专用规范

**口型段（lip_sync=true）**

- performance 段尾必须逐字放歌词行，一句一行：
  ```
  女子缓缓抬手抚过罗盘边缘，唇形随歌词开合
  画不完的圆
  ```
- 禁止出现「说某某话」句式（正则：`说.{0,4}[「"'‘“]` 及 `说.{0,6}话`）——否则模型让人物念白。
- shot_type 必须 ∈ {大特写, 特写, 近景, 中景}（铁律 B 白名单）。

**非口型段（lip_sync=false）——比口型段更要紧**

- performance / audio_policy 中**不得出现任何唱歌标记**（`唱|开口|歌声`），否则模型会让人跟着唱。
- audio_policy 强制注入声明：「嘴唇自然闭合，不跟随人声开合」。
- negative_prompt 必须含口型压制词：`lip movement, talking, singing mouth`。

## 六、20 项机检清单（关 5 · gate_checks.py gate5）

**基础 11 项**

| # | 检查项 | 判据 |
|---|---|---|
| 1 | 六段齐全 | style/scene/subject/performance/camera/audio_policy 六字段非空 |
| 2 | 语言分层 | 字段名符合 schema；歌词文本与 timeline 对应行逐字一致 |
| 3 | 唱歌格式 | 口型段：performance 含歌词独立行（`lyric_text` 原文出现） |
| 4 | 念白禁令 | 全文无「说…话」/「说「…」」句式 |
| 5 | 非口型闭口声明 | 非口型段：audio_policy 含「嘴唇自然闭合」且 negative_prompt 含口型压制词 |
| 6 | 非口型无唱歌标记 | 非口型段正文无 `唱|开口唱|歌声` |
| 7 | 景别与生成职责 | lip_sync=true 时 shot_type ∈ 四类白名单且 `generation_role=lip_sync`；非口型镜为 `narrative`，均与 storyboard 一致 |
| 8 | 禁手写秒数 | 全文正文不含 `\d+(\.\d+)?\s*秒`（时长只进 meta.duration_sec） |
| 9 | 时长一致 | `meta.duration_sec` 与 window 长差 < 0.05s |
| 10 | 窗口合法 | start < end，且落在 [0, 全曲长] 内 |
| 11 | 指纹一致 | `meta.style_fingerprint` == style.json.fingerprint；character_fingerprint 非空且非 "REF"（有主角时） |

**ZY 故事与导演扩展项（#12–#20）**

| # | 检查项 | 判据 |
|---|---|---|
| 12 | 具体瞬间 | performance ≥10 字且含至少一个具体动作词（词表内置 gate_checks.py） |
| 13 | 场景事实编译 | V1.4 场景引用一致，Scene Master 的空间/调度/道具/光线/材质/视觉中心全部进入制作包；具体空间合理性仍须观察审查 |
| 14 | 参考职责声明 | `reference_duties` 每项在枚举内；有主人公图时必须同时含 `likeness + wardrobe` |
| 15 | ZY 故事结构 | `beat / before / next / shot_delta` 完整，不接受歌词动作模板 |
| 16 | 故事指纹 | `story_plan_fingerprint` 已锁定 |
| 17 | 导演四轴展开 | 光与反差、色彩与曝光、镜头与机位、构图与空间均为可执行描述 |
| 18 | 导演姓名去标签化 | 最终六段式正文不以导演姓名或“某某式”代替视觉方法 |
| 19 | 信息层级与运动物理 | `direction.anti_ai` 同时定义清晰度预算与重量/惯性/接触关系 |
| 20 | 因果切镜 | `direction.edit_trigger` 明确由动作、视线、道具状态或句尾触发 |

V1.4 另检查逐镜设计指纹与项目级 ZY 来源契约、提示词覆盖和制作依赖；这些结构检查不代表实际图像/视频的审美 PASS。

## 七、局部修复判定 —— 步骤 9 重抽卡的前置层

哪段不满意时，**先判能不能局部修，再决定整段重抽**：

```
不满意的段 → 列主导问题（最多 3 个，多了说明是设计问题不是修复问题）
  ├─ ≤3 个且互相独立 → repair 路径：只改涉及的字段（如只修 audio_policy 光线声明）
  │                     + 局部重绘/重生成该段，其余锁定不动
  └─ >3 个或牵连结构 → 整段重抽（走指纹失效 → 退回关 5 重审的原闭环）
```

判据写进 storyboard：`repair.mode ∈ none | partial | full_reroll`，`repair.problems[]` 每项含字段名与问题描述。**改过提示词指纹照样失效**——修复不豁免关 5。

## 八、导演 Agent 出稿守则

1. **先读 timeline 再想画面**（铁律 A）：Agent 输入只有 timeline + style(含母版) + 已锁定资产，输出只填 `visual_concept / camera / shot_type / lip_sync`，窗口和切点它无权改。
2. **瞬间优先**：每镜先定「人物意图和阻力、唯一静帧瞬间、观众新知道什么、摄影机实际站位、画框边界、动作怎样受力、光从哪来、怎样观察验收」，camera 段最后派生。整段视频动作只进入 `current_action / motion_design`，不得替代 `keyframe_moment`。
3. **口型节奏规划**：根据已分析的音乐结构和 treatment 分配演唱；目标比例与连续上限只在 performance_policy 显式指定时检查，不默认副歌优先或固定配额。
4. **表演一致**：同一 segment 内 subject 情绪递进，跨 segment 服装道具不得漂移（都锚在 style.json + 场景母版）。
5. **无主角模式**：subject 改写为环境主体（空镜/群像/道具特写/背影），lip_sync 一律 false，画面靠剪辑节奏对拍。
