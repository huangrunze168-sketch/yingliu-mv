# ZY Cinematic Realism → 映流 MV 视觉构造适配

> 上游：[`popopo-99/zy-cinematic-realism`](https://github.com/popopo-99/zy-cinematic-realism)，内置版本 v2.5.0（提交 `9a3b36d1fb6a980ae9e21f74c86dd56bae443a4d`）。上游负责“画面是什么、如何被观看”；映流 MV 负责“画面何时发生、是否对口型、唱哪句、持续多久”。

本文件对应映流 MV v1.0.5 的六阶段与 `state/design.json`。旧版工作流的 `story_plan.json`、`storyboard_build.py`、`prompt_build.py`、`gate_checks.py` 契约在本版中不存在，已作废；ZY 的构造结果直接写进 `design.json`，由 `compile` 展开。

## 一、何时读取

storyboard 阶段必读，pilot 阶段返修时重读；prepare、concept、delivery 不读。ZY 方法是本版设计的强制环节：`state/design.json` 顶层必须声明 `"method": "zy-cinematic-realism"`，未声明时 `compile`、`render --mode preview`、`pack --shot` 一律直接阻断。与[创意与分镜](creative.md)、[视觉与生成](vision-generation.md) 同时使用时，本文件只负责视觉构造，不改 timeline、design 的硬事实。不要每镜重读整个上游库，按第五节的按需加载路由取用。

## 二、职责边界

| 数据/决定 | 权威来源 | ZY 层权限 |
|---|---|---|
| 歌词、窗口、时长、切点 | `state/timeline.json` | 只读，禁止改写 |
| `lip_sync` | `state/design.json` 的 `shots[].lip_sync` | 只读；不得把闭口镜改成开口 |
| 景别与机位范围 | `shots[].camera` 中既定景别 | 只能在既定景别内细化距离、高度与运动 |
| 风格、服装、色板、场景母版 | `design.json` 的 `global` / `scenes` | 可扩展为 Scene Master，不得覆盖已锁定事实 |
| 人物身份与服装 | 人物参考图 + `global.character` / `global.wardrobe` | 只锁 `likeness + wardrobe`，不继承构图、背景或灯光 |
| 动作、调度、空间层次、实际光源、材质 | ZY Scene Master | 可构造，但必须服务当前 `start/end` 窗口与切点 |
| 歌词原文、闭口声明 | MV 层写入 `design.json` 与交付说明 | 不得润色、翻译或删除 |
| 模型/平台参数 | 实际视频后端契约 | 未知时保持模型中立，不套用静帧模型参数 |

冲突优先级：

`用户明确锁定 > timeline/design 硬事实 > style/资产连续性 > Scene Master > Creative Grammar > 模型措辞`

## 三、逐镜构造顺序

1. **冻结 MV 硬事实**：复制 `id / scene / start / end / lip_sync`，后续只读。
2. **恢复全片连续性**：从 `design.json` 的 `global`、`scenes`、人物参考图与上一镜的 `before` / `after` 建立 Base Lock；跨镜复用服装、道具、空间拓扑、光源方向与材质状态。
3. **建立 Scene Master**：为当前镜头确定一个具体时刻、人物物理动作、前中后景、可用空间、实际光源、物体状态与可实现的摄影机见证位置。
4. **应用 Creative Grammar**：仅按需求选择导演、风格、摄影或 Dream Decode 分支；不默认堆叠全部卡片，不复制特定电影镜头。
5. **写入 design.json**：按第四节映射写入 `global` / `scenes` / `shots`；公共事实放 `global` / `scenes`，逐镜只写差异。
6. **编译核验**：运行 `python scripts/mv.py compile PROJECT`，检查 `prompts/SHxxx.json` 的 `resolved` 是否正确展开三层字段、是否残留空泛形容词。
7. **清理**：应用 anti-AI cleanup，去掉无因果的雾、轮廓光、广告摆拍和空泛质量词。清理会改变 `design.json` 的 `source_sha256`，必须重新 `compile`。

## 四、Scene Master 到 design.json 的字段映射

| ZY 构造结果 | 写入位置 | 约束 |
|---|---|---|
| 媒介、色彩/曝光、材质规律、导演四轴中已选部分 | `global.style`（需要时加 `global.medium` 等自定义键） | 必须与已锁定风格一致，不推翻参考图 |
| 空间拓扑、前中后景、实际光源、道具与使用痕迹 | `scenes.<ID>.geography` / `.lighting` / `.props` | 命中已锁定场景母版，不凭空换景 |
| 身份、服装、身体位置、与道具关系 | `global.character` / `global.wardrobe` + `shots[].blocking` | 参考图承担 likeness + wardrobe，不接管构图、背景、灯光 |
| 单一具体动作、姿态、视线、关系变化 | `shots[].action` / `.performance` | 只写一个具体瞬间；非口型段不出现唱歌标记 |
| 物理可行机位、既定景别内的距离/高度、运动与焦点行为 | `shots[].camera` | 最后派生；不得更改既定景别 |
| 视觉中心、信息层级、动作受力、材质响应 | `shots[].lighting` 或自定义键 | 必须可观察、可复核，不能用“电影感”代替 |
| 前一场到本场的状态差 | `shots[].before` / `.after` / `.continuity` | 写清起止状态，与邻镜衔接 |
| 切镜原因 | `shots[].cut_reason` | 必须回答“为什么在此切” |
| 场景特定排除项 | `shots[].continuity` 或交付说明 | 不写泛化负面词墙；口型规则优先 |
| 口型 | `shots[].lip_sync` | 完全由 MV 层决定 |

`prompts/` 只是本地制作包，不是供应商原生请求；后端适配层要按真实 API 再转码。

## 五、按需加载路由

不要每镜读取整个上游库。最小加载集合：

- 普通电影化镜头：`cinematic-principles.md`、`camera-and-light.md`、`anti-ai-cleanup.md`、`quality-checklist.md`。
- 连续镜组：另加 `continuity-cards.md`，以 Base Lock + Shot Delta 工作。
- 用户指定导演：另加 `director-routing.md`、导演索引和一个目标导演文件。
- 参考图风格迁移：按 `reference-role-router → medium-router → dream-decode` 路由。
- 回图修复：读取 `result-repair.md`，最多处理三个主导问题，并保留其他已成功事实。
- 参考帧模型编译：只加载目标模型对应适配器；视频提示词不默认继承 Midjourney、GPT Image、Seedream 或 Nano Banana 参数。

以上文件均位于 `vendor/zy-cinematic-realism/references/`。

## 六、失败保护

- ZY 建议的机位与 `shots[].camera` 既定景别冲突 → 保留景别，只在白名单内调整距离与高度。
- ZY 建议人物开口但 `lip_sync=false` → 删除开口/唱歌描述；口型声明由 MV 层写。
- 导演或参考图规则若改变场景母版、人物身份或歌词动作 → 视为漂移并拒绝。
- 一次修复超过三个主导问题 → 回 Scene Master 重建，不继续堆叠修饰词。
- `design.json` 任何改动都会改变 `prompts` 的 `source_sha256`，必须重新 `compile`；旧提示词会被移入 `prompts/history/`。
- 章节生成前必须先完成模型中立制作包；用户选定后端并由外部适配层按真实 schema 转码后，只允许先生成代表样片，完整动态审片通过前不得解锁批量生成。

## 七、许可与来源

`vendor/zy-cinematic-realism/` 保留上游原始 `LICENSE`、`NOTICE.md` 和文件级版权头，采用 CC BY-NC 4.0。该目录的非商业限制不因被放入本仓库而改变；商业项目使用前应另行取得上游作者授权。署名与来源见仓库根目录 [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md)。
