# 工作流逐环节核验

新项目执行顺序以[当前实际执行流程](actual-execution-workflow.md)为准；本文件说明对应阶段的细节。

当前任务禁止使用生成视频后端；项目策略默认关闭，实际项目已明确禁用。只能本地分析、已有素材处理和明确合成夹具测试；不得为跑通流程启用真实后端，也不能把离线协议测试报告成实际生成。见[MCP与后端策略](mcp-handoff-contract.md)。

最终导出technical_verified后，必须对实际输出完成逐镜有声复核并运行delivery_package生成双版本、权威LRC、实际对齐执行及ZIP；promote通过才ready。完整包缺失或任何版本改变，读取ready也会阻断。执行边界见[交付包契约](delivery-package-contract.md)。

回片后的强制交接：原任务completed原始响应 → 实际GET输出URL → HTTP/视频SHA登记 → `generated_clips.origin_receipt_path` → 当前章节动态批准 → 正式选段。允许保留的DJ等素材须另附用户授权原文及来源；来源登记不批准艺术质量。任一来源改变，旧选段与ready不能继续沿用。执行入口与失败范围见[素材来源契约](media-origin-contract.md)。

本清单与 `workflow-coverage.json` 对应。源码完整性见本机 `../github-research/full-clone-verification.json`；逻辑缺口见 `full-gate-audit.md`。使用时每环节记录实际输入/输出路径、SHA-256、门禁结论、观察证据和完成状态；仅有源码或非空字段不算完成。

| 阶段 | 必须取得的实际产出 | 进入下一步依据 |
|---|---|---|
| 输入 | 原始母带、分析音频、官方歌词、人物原图 | 原始文件指纹与职责已记录 |
| 风格冻结 | 用户确认的色彩服装比例分辨率与style指纹 | gate2目前是SOP环节；实际用户决定有据可查 |
| 人物上传 | 原图哈希、托管资产标识、重复上传缓存 | gate3目前依赖外部适配器；文本@不能代替真实绑定 |
| 时间轴 | ASR与人工校正歌词时间、待核清单 | gate1；口型阶段另外要求 production_timing_verified |
| 音乐分析 | 曲式、重音、气口、能量和音乐cue | cue有时间与分析证据 |
| 编剧 | treatment、source ledger、人物目标阻力转折 | 区分事实/解释/提案，逐镜观众信息新增可读 |
| 导演 | 场景目标、表演节拍、信息释放策略 | 每镜存在功能及可观察验收 |
| 场景美术 | 空间层级、历史痕迹、色材方案、可行动区域 | 母版look test实查，空间与道具可用 |
| 人物美术 | 五官、发型、耳饰、服装身份锁 | 实际参考图审查与 likeness/wardrobe 权限 |
| 灯光 | 光源、方向、光质、遮光、曝光、材质响应 | 母版与人物落位实际观察 |
| 调度 | 起止点、路径、朝向、视线、空间连续性 | 机位变动不会重建人物与房间位置 |
| 摄影 | 覆盖、景别、焦段、机位、画框目的 | 连续性机检及相邻镜头首尾帧审查 |
| 剪辑设计 | visual_edit_plan、信息节奏、切点和衔接 | gate6 + director final cut |
| 电影镜头组织 | 来源标注、故事节拍、逐镜信息职责、空间与动作承接、结构选择 | 新计划 film_grammar_required=true；来源与设计版本有效，连续性无数据冲突后交导演风格化；不以签名或统计量代替创作 |
| 风格方法 | 主导演文件、四轴决定和应用理由 | 基础已核验，风格不覆盖身份/空间/音乐事实 |
| 提示词 | Scene Master/Continuity/Shot Deltas及版本 | gate5 + 全部上游指纹有效 |
| 静帧素材 | 真实母版、关键帧文件与审片记录 | visual_assets当前有效；人物一致与空间可读 |
| 章节音频 | 从原始母带裁出的实际WAV | 音频来源窗口/文件哈希对应章节 |
| 用户视频后端 | 真实能力schema、参考资产绑定、提交包 | submission_contract；项目/章节匹配另核查 |
| 代表样片 | 完整动态视频、时间码审片证据 | director_review approve，依赖当前有效 |
| 批量生成 | 各章回执/素材/成本/错误与续跑记录 | 已批准样片；外部调度器实际执行预算与并发 |
| 返修 | 主导实际漂移、保留事实、受影响依赖 | visual_revision，返修产物重新观察 |
| 素材选段 | 各镜素材入出帧与状态连续证据 | edit_assemble规划；帧率/帧数实测 |
| 合成 | 目标画布/帧率视频与原母带交付 | 所有片段当前有效；实际转场与设计一致 |
| 验收 | 成片指纹、实际时长、音轨、口型、全片艺术审片 | 候选成片音频验证；音频PASS不能代表口型PASS |
| 交付/复盘 | 视频、分镜、歌词时间轴、报告与版本 | 所有必要阶段都有产出和证据；已知缺口不冒充通过 |

## 当前逐步核查入口

正式流程须先通过 `workflow_execution.py run PROJECT --step gate1`、`--step gate5`、`--step gate6` 保存当前有效的实际进程回执；`workflow_execution.py check PROJECT`核对回执与输入/报告/代码版本。只读审计、孤立报告或手写通过状态不能代替执行。现有执行器尚未覆盖十二步全部外部动作，剩余工作见mandatory-workflow-progress.md。

首次生成：`python scripts/workflow_audit.py PROJECT --phase before-generation --submission SUBMISSION.json`。

批量生成：`python scripts/workflow_audit.py PROJECT --phase before-batch --submission SUBMISSION.json`。

交付前：`python scripts/workflow_audit.py PROJECT --phase before-delivery --output-name final_mv.mp4`。

命令只读项目，输出JSON；退出1表示当前阶段有阻断。需要保存时将stdout重定向到独立审计报告，不覆盖输入。首次生成不要求尚未产出的动态样片批准；批量生成要求。交付前复核逐章、全片和delivery状态，不把视频存在当作可交付。

各步标记 `machine_checked_review_scope_required` 只说明对应机器检查通过；没有可机验依据的返修、接收和复盘保持 `evidence_required`。工具不推断历史先后，不新增观看事实，不补旧审批，不自动改写成ready；`all_steps_completed`不会自动成为true。电影镜头组织是新工作流第4步的交接；电影语法统计画像为可选建议，显式绑定须核对真实文件和指纹，不能以样本数或自报状态认证生产。

本地正式提交入口已共用剧组报告指纹核验，不能沿用旧制作桌状态。绕开仓库直接调用供应商或临时脚本仍不受Python拦截，须由外部执行者遵守本清单；不能声称所有外部行为都已被自动强制。
