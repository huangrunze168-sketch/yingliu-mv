# 代码功能缺口审计（2026-10-16）

> 后续修复：用户已授权补齐下列八项。新增workflow_run、sound_mix、lip_measure、corpus_validation、batch_scheduler、release_evidence、工作台同步及ASR依赖环境；使用与兼容边界见[补齐说明](gap-integration-guide.md)。下文保留修复前发现及原验收结果，不能当作最新实现状态。独立口型仍需实际人工逐帧复核，生产语料仍需真实独立验收，代码不会自动产生可信艺术结论。

后续最终验证：最新源码434项完整回归通过（227.112秒），新增专项17项；干净Python3.12 ASR实际运行成功。完整证据见[current-validation-status.md](current-validation-status.md)。

## 范围与方法

主项目为 `mv-auto-pipeline-fusion`。本次清点并解析 scripts 下全部60个Python文件，共13,143行、384个函数；另检查MCP宿主JavaScript及37个测试模块的回归结果。重点人工核对执行编排、提交、观察、转场、交付和工作台入口，结合项目契约与真实项目只读审计。清点和语法解析不等于逐行形式化验证。

`github-research`为参考仓库集合，未将其功能视为本项目已实现；`output`为项目媒体及状态。本次不调用生成视频后端，不更改审批或制作输入，不修补业务源码。

## 已确认的代码与集成缺口

优先级为建议补齐顺序，并不表示当前受控阻断都是程序错误。

| 优先级 | 缺口 | 代码依据与影响 | 完成条件 |
| --- | --- | --- | --- |
| P1 | 十二步缺少统一的持久执行编排 | `scripts/workflow_execution.py:18`仅注册gate1/gate5/gate6及两项剧组命令；`scripts/workflow_audit.py:165`明确不证明历史顺序。其他步骤有独立模块，但没有十二步统一运行、恢复和阶段完成判定。 | 建立步骤级状态、输入输出版本、人工等待状态、失败恢复和依赖顺序校验；生成步骤遵守后端禁用策略。 |
| P1 | 正式MV未接入J_cut/L_cut声音轨 | `scripts/transition_render.py:48`已有独立声音轨渲染；`scripts/pipeline_contracts.py:104`对非straight/music_hit保持阻断。正式合成与交付尚不能消费该声音轨。dissolve已实现，不列为缺失。 | 增加声音轨选择、混音和来源绑定，定义独立声音与原始母带的验收方式，重新审片并验证交付。 |
| P1 | 缺少独立的口型测量与观察真实性核验 | `scripts/delivery_evidence.py:59`及`:74`验证观察者声明；`:120`明确lip_sync_verified=False。`scripts/local_perception.py`提供实际多模态推理，但视频为稀疏采样，不能据此认证逐帧嘴型。 | 明确可接受的人工观察流程；若要求自动口型验收，需独立测量、阈值和误差验证。播放日志、哈希和模型自报不能单独代替。 |
| P2 | 电影语料生产认证与逐镜语义指导缺失 | `scripts/film_grammar_corpus.py:223`对enforce_envelope始终阻断；`scripts/source_reference_import.py`导入结果保持exploratory_only。建议性统计可用，但不能作为认证的生产导演模型。 | 核验影片身份与版本、标注质量、独立验收以及逐镜语义映射后，再启用生产模式。 |
| P2 | 完整批量调度仍依赖外部调用方 | `scripts/submission_jobs.py:338`和`:441`分别处理单作业回查及派发；`scripts/mcp_broker.py:206`和`:220`桥接单作业。已有预算、并发准入、未知结果和恢复约束，尚无完整章节队列调度器。 | 增加队列、并发运行、回查退避、重启恢复和汇总入口；未知提交结果不重新派发。用户禁用后端时只做离线验证。 |
| P2 | 复盘发布没有机器完成判据 | `scripts/workflow_audit.py:39`第12步checks为空；`:157`起将其列为evidence_required，无法核验复盘、回归、版本和发布之间的绑定。 | 为复盘及所选发布方式建立实际证据契约与只读核验；检查功能不应自动发布。 |
| P2 | 工作台缺少原曲与候选片同步对照 | `scripts/review_workbench.py:259`播放一个播放器时暂停其他播放器；`:262`歌词跳转仅定位原曲。可独立听看，尚无共享时间轴、偏移校准和联动跳转。 | 增加可校准偏移的同步播放及跳转，区分原曲时间、候选时间与对标时间。 |
| P2 | ASR依赖安装说明不完整 | `scripts/asr_capture.py:47`必需faster_whisper；当前requirements-perception.txt没有faster-whisper，README环境段仍笼统描述纯标准库。其他本地感知依赖已有单独清单与环境说明，不能称为完全没有依赖管理。 | 补齐ASR可选依赖和兼容环境说明，在干净环境验证README给出的实际ASR命令。 |

## 文档与实现存在偏差

- `references/architecture.md:69`称gate5只读、不修改数据；实际`gate_checks.py:687`成功后执行persist_prompt_fingerprints。需区分只读audit和会持久化结果的CLI。
- `references/architecture.md:70`称脚本互不依赖、只依赖标准库；实际模块存在跨脚本导入，本地ASR、音乐分析和多模态观察有额外依赖。应按基础工具与可选感知环境分别说明。
- `references/workflow-coverage.json`部分条目仍描述需要实际MCP适配器，而仓库已有lovart_adapter与MCP桥接。应区分“已有特定模型适配”“外部调度仍缺少”“真实后端尚未验收”。当前适配器限定模型是显式范围，不应一概称为没有适配器。

## 真实项目状态缺口

对 `output/诸葛亮VS周瑜_重制` 实际运行before-delivery只读审计，退出1。原始JSON保存于 `.test-tmp/audit-project.json`。

通过：事务状态、设计、导演序列、剧组报告、提示词及读取一致性。

阻断：

1. gate1没有成功回执；时间轴缺少逐行真实音频复核声明，仍非正式生产权威。
2. 口型窗口因待核时间轴被阻断。
3. 静帧检查首先报SH001缺少同场景分镜关键帧；该检查遇首个失败退出，不据此声称已逐镜统计所有缺失图片。
4. 预演未获完整观看与四维度时间码批准。
5. 缺少director_review.json、generated_clips.json、repair_assessment.json、chapter_plan.json及final_mv.mp4对应交付记录，相关动态审片、来源、返修、全片及交付校验失败。

这些属于当前项目尚未完成或未登记的生产环节，不能直接当成同名Python功能不存在。已有视频文件也不能替代当前版本的准入和交付证据。

## 本轮验证

- 60个Python脚本AST解析成功；清单保存在 `.test-tmp/audit-code-inventory.json`。
- `python -m compileall -q scripts tests`及`node --check scripts/mcp_host_bridge.js`通过。
- 完整回归命令：`python -m unittest discover -s tests -v`，TEMP/TMP定向至仓库.test-tmp；实际退出0，417项通过，耗时225.724秒。
- 原始测试日志：`.test-tmp/audit-unittest.log`。
- 未安装依赖、下载模型或调用供应商。本轮测试不作为真实影片艺术质量或口型验收依据。

建议先补十二步状态编排和真实项目时间复核入口的使用闭环，再处理正式声音转场、审片同步及复盘证据；电影语料认证按可选能力单独推进。
