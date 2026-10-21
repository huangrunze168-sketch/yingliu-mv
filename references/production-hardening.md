# V1.7 生产契约与迁移

机器验证引用、类型、时间、指纹和媒体流，不证明剧作、美术、表演或口型质量。颜色测试片、模拟回执不能冒充真实艺术验收。后端、凭证和能力核验仍由用户及外部适配器决定。

## 执行能力

| 环节 | 状态 | 入口与证据 |
|---|---|---|
| 故事、导演、美术、灯光 | manual / Agent设计 | development、story_plan、crew_bible与真实来源/观察 |
| 时间权威 | external / manual | ASR及逐行确认；fallback仅provisional |
| 提示词、章节与音频资产 | implemented | prompt_build、chapter_*、gate_checks及指纹 |
| 上传与全能参考生成 | external | 真实上传定位、SHA、能力回执、submission_contract |
| 静帧和动态审片 | manual + implemented契约检查 | 声明性观察记录、实际动态媒体和依赖 |
| 本地剪辑 | implemented部分 | hard_cut/match_cut直切、straight/music_hit及带句柄的dissolve；独立音效轨J_cut/L_cut见local-transitions.md，正式MV混轨入口仍阻断声画偏移 |
| 交付 | implemented + manual | 流属性、帧数、母带载荷；审美和口型另审 |
| 电影参考 | implemented聚合 / external语料 | 合法标注、来源版本；未声称真实数百部语料已验证 |

## A06–A36修复与迁移

| ID | 生产不变量 |
|---|---|
| A06 | 按作用域固定必需集合：公共音乐/母带/人物/风格/部门锁＋本章故事、开发、分镜、剪辑、资产、提示词及音频；删除必需项或改本章/公共事实失效。全片绑定所有章节 |
| A07 | 提交绑定绝对project_id、chapter_id、source_package_sha256、prompt_sha256、实际镜头及当前本地审批事实，不以approved字符串放行 |
| A08 | 非空真实镜头列表、唯一ID、完整窗口、timestamp/multi_shot能力、时长/分辨率/预算上限 |
| A09 | 共享REFERENCE_INTENTS；blocking_start_state在蓝图与提交两端一致 |
| A10 | planned音频仅作设计占位，生产须真实定位、匹配SHA及uploaded状态 |
| A11 | chapter_relative；首镜0，末镜章时长，时间有限、正向、连续 |
| A12 | 摄影shot_size与正式shot_type规范化核对，提示词摄影段从crew同源派生 |
| A13 | selected coverage须引用真实正式镜头、同场景及coverage_source_id；window与keyframe_moment须匹配正式镜头 |
| A14 | axes登记几何坐标，摄影position_xy核对camera_side；改名/裸axis_reset不能重置 |
| A15 | 动线只在连续空间/continuity_group比较；边缘与向量分开，time_relation区分时间跳跃 |
| A16 | 场景kind及coverage_requirements逐镜兑现，不强加通用reaction/detail配额 |
| A17 | hook实际布尔、次数阈值实际整数；拒绝字符串、bool计数及小数截断 |
| A18 | 歌词规范化逐行比对、唯一正整数索引、有限正向范围 |
| A19 | gate1报告structural_pass、stage、production_allowed；正式入口统一读取时间权威 |
| A20 | 代表章只解锁批量；正式交付须逐章和full_sequence批准，后者额外绑定选段/剪辑及源/裁切视频 |
| A21 | 正式edit_assemble --render检查样片；仅--technical-preview可绕过，不能冒充正式交付 |
| A22 | stitch使用storyboard.fps与style.aspect_ratio，实测帧数/流属性 |
| A23 | 时长偏差超帧容差返回非零，报告真实成片，不用-shortest掩盖截短 |
| A24 | 输入帧数用真实输入fps/时基解释，异帧率不能流复制 |
| A25 | dissolve须真实执行视频重叠并保持音乐时间；J_cut/L_cut须独立音效轨与时间语义。支持矩阵以local-transitions.md为准，未支持的正式路径仍阻断 |
| A26 | planned_shots_sum与actual_video_duration分离；混音包络不判口型 |
| A27 | self_reference仅DIAGNOSTIC_PASS；正式对齐须真实候选视频与从其提取的候选音轨 |
| A28 | 原始母带声明/首次登记SHA，交付编码载荷或解码PCM24载荷一致性核验 |
| A29 | 门禁先核唯一ID，不能依赖构建器曾经跑过 |
| A30 | 图片实际类型/尺寸/解码核验；agent_inspected=false，记录声明性观察，不伪造看图事实 |
| A31 | 临时文件、写锁、journal、原子替换与回滚；未完成事务阻断生产 |
| A32 | 有限Decimal、event_id幂等、task_id预占/结算/释放、退款关联和现金封顶 |
| A33 | 同层场景统计、类型包络、来源/影片/版本键、统一景别/运镜词表；真实大语料验证需合法数据 |
| A34 | 本文逐环节声明implemented/manual/external，README与Skill不保证无人自动合格 |
| A35 | gate_registry导出实际入口、执行者、依赖和失败码；gate CLI只有1/5/6，检查数读报告 |
| A36 | 预览生成成功与验收失败分开，验收FAIL返回1并保留报告 |

## 空间与审片数据

每场景声明kind=narrative/performance/montage/oner、coverage_requirements及`axes={"door_axis":{"start":[0,0],"end":[1,0]}}`。摄影position_xy与camera_side匹配。blocking.entry_edge/exit_edge为left/right/top/bottom/none，direction字段仍表示运动向量。跨场默认不强求方向连续；明确连续空间用continuity_group；时间跳跃用crew.editing.time_relation=ellipsis/flashback/parallel。

axis_reset=true须axis_bridge={shot_id,type,start_sec,end_sec,evidence}，引用当前/上一镜的neutral中性或reestablish建立空间镜，时间相对桥接镜。旧资产、提示词和审片须重新登记，不补一个approved字符串继续使用。

director_review_gate init仅创建pending；完整观看动态媒体后填写技术、九项导演、四项序列证据。默认代表章只解锁批量；--chapter CHxx为逐章，--chapter full_sequence为全片。simulation_only=true不能批准生产。正确顺序：样片批准→正式edit_assemble选段渲染→stitch候选预览→全片审片→最终合成。全片init/check拒绝technical_preview剪辑清单；批准后禁止edit_assemble重写。如需改变，invalidate撤销→修改剪辑→refresh（归档旧记录并建立新pending）→完整重审。

### 剪辑预演与依赖范围修订

首次视频生成前用animatic_review init --media指定实际完整带音乐的分镜预演，手工填写information_progression/climax/music_rhythm/cut_relationships及逐项时间码，check通过才可正式提交。原始音乐、设计、预演媒体或引用图片改变均失效；这个批准不替代动态样片的运动、人物一致性和口型审查。

预演失效后的恢复使用`animatic_review invalidate`→重做预演→`animatic_review refresh --media ...`→完整重审。撤销和刷新均归档旧记录，刷新只绑定当前依赖并重置pending、观看状态、四维度证据及时间码，不自动批准；不得删除旧审片绕过历史。

代表章/逐章依赖使用公共锁＋本章投影：公共音乐、母带、人物/服装、风格及部门bible；本章正式镜头、场景、故事、剪辑设计、提示词、音频、关键帧及父母版。容器JSON按本章内容计算规范化指纹，真实媒体直接哈希；忽略构建器的全文件溯源指纹，不忽略实际创作内容。全片依赖仍覆盖所有章节和选段源/输出视频。旧审片依赖格式不能沿用，须refresh并重审。

## 母带、事务和预算

### 独立交付状态

正式素材准入先于剪辑：generated_clips每份素材须声明chapter_id及shot_ids列表，与chapter_plan唯一归属一致。逐章审片须有效，所选文件路径/SHA必须等于该章节实际批准的reviewed_media；替换素材即使标为reviewed也不放行。不同候选版本需分别审查，不能沿用别的版本批准。仅technical-preview可使用未逐章批准素材。正式按帧裁切同时核验CFR时间戳；不是只相信平均fps。

edit_assembly保存chapter_approvals指纹。全片审片绑定逐章记录，最终stitch再次检查这些准入版本；批准变动不能沿用旧正式选段。

正式stitch在`state/deliveries/<文件名>.json`登记rendering→exported（已输出）→technical_verified（技术验收通过）→ready（可交付）。只有实际媒体技术验收通过且样片、逐章、全片、剪辑预演审批仍有效，才进入ready；技术预览不创建此正式交付记录。

记录绑定绝对项目ID、最终文件SHA、实际导出规格、验收报告SHA、整片依赖及审批记录/观看对象SHA。重新导出先撤销旧ready；渲染/技术验收失败为failed。文件、依赖、审批或报告改变，`delivery_state.py check`返回invalidated并持久化失效。没有后台文件监视器；交付动作必须调用require_ready或CLI check实时核验，不直接相信JSON里的旧ready字符串。

交付记录升级schema_version=2，包含唯一export_id和规格SHA；验收报告绑定同一任务ID、规格、最终文件SHA与实测流属性。规格必须符合项目权威时长、fps、母带及比例，不能修改记录尺寸后继续沿用旧报告。旧schema1记录不自动可交付，须重新执行当前验收链。

所有状态/报告写入在项目写锁内比较读取版本后原子提交（CAS），旧完成回调、失败回调、状态检查不能覆盖新导出。正式媒体先在独立临时目录生成，再通过当前任务版本检查后发布到最终路径；旧任务也不能覆盖新媒体。事务保护保证失败时不误标ready，不是后台任务调度器或数据库级大媒体回滚系统。

检查期间发现新任务版本时，返回`superseded`（本次检查已过期），不能返回旧ready或把旧检查错误写入新任务；请重新检查当前版本。`superseded`仅为检查结果，不改新任务持久状态。

持久化失效还须匹配本次请求的项目与文件名；错配记录只阻断自身检查，不能把错误回写到另一份成片记录。

剪辑采用版本目录`clips/edit/<generation_id>/`：全部源素材先体检，全部选段在临时目录生成并实测帧数/fps，最后比较分镜、选段、素材、审批及旧清单版本后发布新目录和清单。中途编码失败或输入改变不覆盖旧片段，不发布半套清单。旧成功目录保留供回溯；极端中断可能留未引用目录，不自动清理用户资产，也不宣称跨所有媒体的数据库事务。

```bash
python scripts/delivery_state.py check PROJECT --output-name final_mv.mp4
```

命令仅在当前可交付时退出0，已输出或仅技术通过不算可交付。`user_received=false`不会因合成、验收或ready自动变为true；本轮没有实现发送文件或用户接收确认功能，ready不等于已经交付或用户接受。

timeline.master_audio_sha256是原始上传声明；旧项目master_audio_lock首次观察只能证明登记以后未变化，不能证明历史上传身份。改变母带需显式重登记并重审。分析16k单声道文件不作交付母带；align_verify须同时绑定--candidate-video及从其提取的--candidate-wav，口型另审。

见到.pipeline-transaction.json或.pipeline-write.lock先停生产。确认无在途进程后：

```bash
python scripts/pipeline_contracts.py PROJECT/.pipeline-transaction.json --confirmed-no-writer
```

journal只能恢复其目录内文件，无journal但有锁须人工核查，不自动删可能属于活进程的锁。大媒体生成不是跨进程数据库事务；未完成媒体无成功manifest和匹配hash，不得交付。

导演审片记录的init/invalidate/refresh统一通过update_review执行读取版本比较。版本冲突须重新读取后由调用者决定，禁止无条件重试覆盖。invalidate归档原始记录并撤销批准及完整观看标记；refresh仅接受pending，清空媒体引用、审片维度证据及时间码，必须重新观看审片。手动修改JSON不受写入口CAS约束，生产入口仍须重新核验批准及依赖。

导演审片与剪辑预演approved入口在媒体实测前后复核审批字节SHA、媒体SHA和作用域依赖，并检查在途事务。检查期间变化或删除失败关闭，不沿用旧通过结果，也不回写新记录。返回结果只代表本次前后复核，不能替代发布时的版本比较，不能证明跨文件快照或检测改变后又恢复原字节的ABA变化。

外部调度器须先reserve、后settle/release；重试新task_id；fail_refund关联已支付任务且不超额。台账约束已付加在途预占，现金封顶在首条记录后锁定，单个任务不能抬高；旧台账需显式迁移。代码不声称控制供应商未登记消费。

```bash
python scripts/ledger.py add state/ledger.jsonl reserve CH01 chosen 0 5 0 --event-id R1 --task-id T1 --cash-limit-cny 100
python scripts/ledger.py add state/ledger.jsonl settle CH01 chosen 0 4 4 --event-id S1 --task-id T1 --cash-limit-cny 100
python scripts/gate_registry.py --output references/gate-registry.json
python -m unittest discover -s tests -v
```
