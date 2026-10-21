# 电影语料辅助镜头组织与导演交接

当前总览与资料顺序见[实际执行流程](actual-execution-workflow.md)。

目标是让歌曲和故事决定镜头组织，再由导演在成立的空间、动作、信息关系上风格化，最后交给全能参考编译层。电影语料用于提供可追溯的镜头经验，不作为艺术质量认证，也不要求API密钥或独立签名。

## 严格顺序入口（新计划）

新计划设置 `creative_workflow.organization_order=2`，第4步按以下顺序执行：

1. 创作角色根据歌曲与故事，按ZY故事开发方法编写独立 `BEATS.json`，不依赖最终story_plan、导演development、visual_edit_plan或sequence_direction。
2. `organization_workflow.py propose`仅读取时间轴、音乐分析、节拍和真实电影标注索引，生成前置组织提案。方法、来源与叙事职责由创作角色明确选择，不把关键词匹配当作故事理解。
3. 修订人物起止位置、动作状态、轴侧和过渡依据，填写reviewer与accept_structure，再执行select。连续性数据检查通过后保存 `state/organization_structure.json`，此时仍没有导演执行批准。
4. 创作角色读取已选结构和实际ZY导演方法，编写正式故事、剪辑、序列调度及development。development的 `organization_structure` 必须等于select返回的binding。组织字段与实际动作、音乐引用、坐标不符即拒绝绑定。
5. bind原子保存正式故事中的逐镜film_grammar以及 `state/director_execution.json`，绑定结构与实际执行文件版本。随后重建storyboard，编译逐镜、章节与全能参考。
6. 结构或导演执行文件改变，旧绑定失效；修改结构须重新select并重新导演执行，修改执行设计须重新bind。实际画面问题仍须回到对应阶段修订，数据通过不代表电影质感通过。

`film_grammar_bridge.py propose/adopt/preview`保留旧项目兼容；它们不是新流程的前置组织入口。严格策略禁止通过旧adopt跳过前置结构选择。章节编译与正式生产均重新核验交接。

### 前置故事节拍契约

BEATS包含 `method: "zy_story_beats"`、`zy_sources: [{"path":"相对ZY库路径","sha256":"实际指纹"}]` 和 `shots`。每镜须给出shot_id、scene_id、window、music_refs（start/end引用）、story_goal、current_action、editorial_role_proposal、organization_sequence_id、明确method、索引中的source_id、source_application，以及action_start/action_end、具名start_positions/end_positions、axis_side和必要的衔接说明。时间窗须连续覆盖歌曲。这些是组织与连续性草案，不要求光色、焦段或最终导演风格设计。

```powershell
python scripts/organization_workflow.py propose --project PROJECT --beats BEATS.json --index INDEX.json --output PROPOSAL.json
# 修订提案并明确选择结构后：
python scripts/organization_workflow.py select --project PROJECT --draft PROPOSAL.json
# 按已选结构编写导演执行设计并在development绑定返回的binding后：
python scripts/organization_workflow.py bind --project PROJECT
python scripts/storyboard_build.py PROJECT
python scripts/prompt_build.py PROJECT
```

## 来源准备与旧项目兼容入口

在独立Python3.12环境安装`requirements-film-reference.txt`，不使用API服务。

```powershell
python scripts/film_grammar_bridge.py analyze --video BENCHMARK.mp4 --output CUTS.json
python scripts/film_grammar_bridge.py index --ada-repo ADA_REPO --analysis CUTS.json --output INDEX.json
```

本地切点分析为可选来源补充，不负责剧情或人物理解。新项目继续执行上面的organization_workflow入口。

## 旧项目兼容

旧版 `film_grammar_bridge.py propose/preview/adopt` 依赖已有完整设计，用于旧流程的参考补充，不适用于新项目严格顺序。旧版BEATS使用beats分组；新入口使用method/zy_sources/shots，二者不能混用。需要旧项目迁移时重新编写前置故事节拍，走propose/select/bind；本次资料整理不自动迁移旧工程。

## 连续性与来源约束

- 起止位置按实际subject_id区分人物，换观看对象不等于同一人物瞬移。共享人物的相邻坐标、动作前后状态须承接。
- 连续空间换轴侧必须提供桥接设计；跳时/跨场须给出具体过渡依据。检查只能发现数据冲突，不能证明实际镜头拍得成立。
- 提案中的坐标、动作、故事意图、音乐引用和时间窗口必须与现有导演设计一致。不能只修改检查记录而不修改实际设计。
- 采用的来源段落必须逐项等于当前索引中的真实标注；来源包、索引、歌曲、节拍或设计改变即失效。
- source_application说明只借鉴哪些标注经验；叙事职责是本项目的转译，不声称原电影拥有同一人物关系、故事或情绪。
- 采用桥接层的项目在主提示词编译、审片依赖与正式生产前置中重新核验。不采用时保留旧项目兼容。

## 工作流交接与修改范围

新计划使用 `creative_workflow.film_grammar_required=true`。第4步必须存在当前有效的结构选择，随后才交导演完成风格执行及第5步全能参考编译。第2步讨论的是风格方向，逐镜风格执行须服从第4步成立的组织关系。旧计划保留兼容。

交接依次保存来源索引、叙事节拍、待选组织提案，以及采纳后 `state/story_plan.json` 中的 `film_grammar_bridge` 和逐镜 `film_grammar`。预览包供比较，正式下游只消费当前有效的结构选择。结构选择不代表艺术质量批准；若画面验证暴露空间或动作问题，返回第4步修订并重新编译。

用户仅要求修改工作流时，只修改代码、文档和隔离测试；不默认分析旧项目媒体、运行旧项目复核、生成旧项目提案或变更旧审批。以下制作命令仅在具体项目制作获得授权后执行。

ProjectAdA标注采用CC BY-SA 3.0；再分发来源标注及其改编时保留署名和对应许可。Python代码的许可证与电影画面、标注数据分别处理。

启用后的项目约束会在首次实际运行前保存到 `state/creative_workflow.json`。独立提示词编译、审片依赖和生产前置也读取此策略，缺少结构选择或策略格式错误均阻断；仅执行check不写入策略。后续计划省略该选项不会自动解除已启用的约束。
