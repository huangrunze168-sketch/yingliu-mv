# 当前实际执行流程与资料入口

当前创作主链为：**歌曲与故事 → ZY 故事开发＋电影语料辅助组织 → 连续性设计 → ZY 导演执行 → 全能参考编译 → 视频生成与审片**。

本文件统一新项目的执行顺序。十二步业务流程保留，第2步仅确定总体视觉方向，第4步细分故事组织、连续性结构选择和导演执行，第5步编译与预演，第7/8步样片及批量。历史审计和旧兼容示例不作为新项目的启动顺序。

## 六阶段执行与责任

| 阶段 | 输入与实际执行 | 交接产物与阻断条件 |
|---|---|---|
| 1. 歌曲与故事 | 原始母带、官方歌词、故事意图；完成timeline和music_structure。故事意图由用户与创作角色确定 | 歌词/音乐引用可追溯，正式制作时间依据有效；未知内容保持待核 |
| 2. ZY故事开发＋电影参考组织 | 创作角色读取ZY故事方法，编写独立BEATS；选择真实标注段落并说明转译。organization_workflow propose不读取最终导演文件 | 待选组织提案：故事目标、观众信息、逐镜职责、来源、音乐引用、动作和空间草案；pending不自动采用 |
| 3. 连续性设计 | 修订人物位置、动作状态、轴侧及跳时/跨场依据；明确reviewer与accept_structure，运行select | state/organization_structure.json和结构binding；数据冲突、来源不一致或依赖过期则拒绝选择 |
| 4. ZY导演执行 | 创作角色读取已选结构及适用ZY导演方法，编写story_plan、visual_edit_plan、sequence_direction、development。development.organization_structure填写select返回的binding，运行bind | film_grammar写入正式故事；director_execution.json绑定结构与执行版本。动作、音乐、坐标或绑定不符则拒绝。随后构建storyboard，ZY视觉开发与剧组检查再核验执行设计 |
| 5. 全能参考编译 | prompt_build编译场景、关键帧与运动包；完成门禁与音乐预演。实际审查母版和关键帧后，chapter_plan/audio/prompt编译章节与参考职责 | 组织约束进入direction、motion、章节beats与omni_reference.timeline；结构或导演执行版本改变即阻断。模型中立制作包不等于后端原生请求 |
| 6. 视频生成与审片 | 用户授权且后端策略允许后，外部适配器核验实际模型能力、上传与原生请求，先代表样片动态审片，再批量与逐章审查 | 实际任务回执、下载来源、媒体SHA与当前审片。样片未批准不扩批；成片另经技术验收、有声复核与交付包核验 |

阶段2和4中的创作由执行任务的智能体/创作者承担，Python负责确定性编译、检查和版本约束。ProjectAdA提供标注，PySceneDetect提供切点；两者不会自动理解原片剧情。ZY文件指纹证明来源版本，不能证明创作角色已经正确理解方法。

## 新项目执行入口

复制并填写 [十二步计划模板](../examples/workflow-plan.template.json)，保留：

```json
{"creative_workflow":{"film_grammar_required":true,"organization_order":2,"reference_mode":"creative_advisory"}}
```

模板中的commands为空，须按项目材料填写；编排器不会自动撰写故事、选择导演或生成影片。实际run保存项目策略，check只读。十二步现在每步完成都停在waiting_approval，独立签名同意后才能进入下一步；第12步也须批准。操作及权限边界见[逐步批准锁](workflow-approval-locks.md)，不能把独立CLI或MCP当作绕锁捷径。

```powershell
python scripts/workflow_run.py run PROJECT --plan PLAN.json
python scripts/workflow_run.py resume PROJECT --plan PLAN.json
python scripts/workflow_run.py check PROJECT --plan PLAN.json
```

第4步的核心动作顺序：

```powershell
# 先由创作角色完成独立BEATS，准备真实来源INDEX。
python scripts/organization_workflow.py propose --project PROJECT --beats BEATS.json --index INDEX.json --output PROPOSAL.json
# 修订连续性并在提案明确选择结构后：
python scripts/organization_workflow.py select --project PROJECT --draft PROPOSAL.json
# 再编写导演执行四份设计文件，development绑定select返回的binding后：
python scripts/organization_workflow.py bind --project PROJECT
python scripts/storyboard_build.py PROJECT
python scripts/visual_development.py PROJECT
python scripts/crew_pipeline.py PROJECT
python scripts/prompt_build.py PROJECT
```

上述命令展示交接顺序，不能替代完整十二步及真实门禁回执。章节编译须等待正式时间、门禁、资产与参考职责满足要求。详见[视觉开发契约](visual-workflow-contract.md)与[逐环节清单](workflow-run-checklist.md)。

## 变更与退回

- 故事、音乐或来源改变：重新propose/select，随后重做受影响导演设计与bind。
- 已选结构改变：旧导演执行失效，重新绑定新结构并重编译。
- 导演光色、摄影或其他执行文件改变：旧执行记录失效，重新bind、重建分镜并重编译；若改变组织事实，先回结构阶段。
- 实际画面发现动作或空间不成立：返回结构/导演设计，重新验证对应素材和审片，不能只改文字记录沿用旧批准。

哈希绑定约束当前产物之间的依赖，不能证明人的历史操作、实际观看或艺术质量，也无法拦截绕过仓库直接调用供应商的行为。

## 资料阅读顺序

| 资料 | 用途 |
|---|---|
| 本文件 | 当前执行总览和阶段职责 |
| [SKILL](../SKILL.md) | 执行者主指令与十二步纪律 |
| [电影组织交接](film-grammar-creative-bridge.md) | BEATS契约、propose/select/bind和来源准备 |
| [ZY集成](zy-cinematic-integration.md)、[导演Scene Master](director-scene-master.md) | 导演方法与逐镜画面执行 |
| [全能参考规范](omni-reference-video.md) | 素材职责、分时镜头和模型编译 |
| [工作流清单](workflow-run-checklist.md) | 各阶段实际产出与核验入口 |
| [当前验证记录](current-validation-status.md) | 最新隔离验证与能力边界；旧章节为历史检查点 |
| [CHANGELOG](../CHANGELOG.md) | 修改历史；不作为当前操作顺序 |
| [八项集成说明](gap-integration-guide.md) | 独立工具参数；语料签名认证是额外模式，不是普通创作参考前提 |

当前任务只整理工作流与隔离验证，不启用视频后端、不处理旧工程媒体、不补写实际项目审批。

## 本次核对依据

| 代码入口 | 本次确认的实际约束 |
|---|---|
| organization_workflow.propose | 依赖仅为timeline、music_structure、BEATS、索引及其真实来源，不读取最终四份导演设计 |
| organization_workflow.select | 检查连续性与当前来源，保存结构并启用organization_order=2 |
| organization_workflow.bind / film_grammar_bridge.adopt | 必须绑定先前结构，核对正式设计并原子提交执行版本记录 |
| film_grammar_bridge.require | 核验已选结构、来源、逐镜保留与导演执行文件版本 |
| prompt_build / chapter_prompt_build | 编译前调用require；章节构建实际生成omni_reference及reference_bindings |
| production_preflight / submission_contract | 正式提交重新检查当前设计、门禁、资产和后端提交契约 |

本次重新执行七组隔离回归，共145项通过（27.965秒）；检查上述整理文档的60个本地链接及代码围栏均通过，Python编译与diff空白检查通过。没有执行真实项目的媒体制作或实际视频生成。隔离夹具验证的是交接顺序、版本失效和数据传递，不是艺术效果。
