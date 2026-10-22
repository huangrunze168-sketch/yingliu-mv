# 数据格式与文件职责

所有路径以项目目录为基准，必须位于项目内。素材先复制进 input/clips；CLI 不读取项目外素材。JSON 使用 UTF-8，时间单位秒。init 输出有待填写字段的模板；空值不会自动获得批准。

| 文件 | 字段 |
|---|---|
| project.json | name、audio、lyrics、reference_video、reference_voice、delivery_audio、width、height、fps、budget、backend、authorization、agent_capabilities |
| state/timeline.json | duration、verified、evidence_path、segments（start/end/text）；连续覆盖全曲，verified 为真实复核声明 |
| state/concept.json | treatment、visual_direction、music_sections |
| state/design.json | global、scenes（ID 到设定）、shots |
| state/animatic_review.json | media、observations、decision、evidence、unverified |
| state/pilot_review.json | 同上 |
| state/clips_review.json | reviews（逐条采用上述审查结构） |
| state/edit.json | clips（media、in、duration、receipt） |
| state/jobs.json | jobs（真实任务与离线费用记录） |
| state/final_review.json | 审查结构；要求 audiovisual 观察 |

design 顶层必须声明 `"method": "zy-cinematic-realism"`（按 ZY 方法库完成；未声明时 compile、render --mode preview、pack --shot 直接阻断）。每镜必填 id、scene、start、end、action、camera、cut_reason、lip_sync；image 是预演图片路径。允许 blocking、lighting、performance、sound、continuity、before、after 等补充字段。镜头必须连续覆盖 timeline.duration；scene 必须存在，镜头 ID 唯一。global/scenes 是对象，字段由创作需求决定。

审查 decision=approve，evidence 非空、unverified=[]、引用的 observations 当前有效才通过。observations 为 observe 输出的项目相对路径。animatic/pilot/clips 需要 full_video 或 audiovisual；最终审查需要 audiovisual。

脚本输出 prompts/SHxxx.json 和 prompts/index.json，仅为本地制作包，不是供应商原生请求。编译器不会提交生成；后端适配器要按真实 API 再编译。

approvals.json 绑定前序输入、当前阶段文件、引用素材、观察及其参考文件与工具版本。更改这些文件会使相关审批过期；脚本不会删除旧素材，也不会自动重生成。审批只证明登记的确认与当前证据版本匹配，不证明观察真实性或独立身份。

`init --simulation` 在项目中设置 simulation=true；此类观察必须显式携带 simulation=true，最终状态为 simulation_completed，delivery_report.production_acceptance=false。普通项目默认 false，禁止复用模拟观察。模拟标识是本地流程隔离，不是防恶意篡改机制。

渲染按累计音乐边界计算每镜帧数，technical.json 保存 frame_plan；切点最多约半帧量化误差，不逐镜累加漂移。不足一帧的镜头拒绝渲染。原音频不因画面帧量化而变速。
