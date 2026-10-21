# 本地有声审片工作台

真实本地播放器支持原曲、对标与候选片，歌词跳转、逐行起止和演唱者复核、带时间码的逐镜观察、设计及导演序列草稿编辑。播放一个媒体时暂停其他播放器，避免串音。视频保持自身原音，不为无声片伪造原生音轨。

此功能补齐听看和记录入口，不赋予语言模型听觉或完整动态理解能力。加载成功、播放事件、观察声明不能证明完整审片，也不自动认证时间、口型、艺术或交付。

## 使用

```powershell
python scripts/review_workbench.py build PROJECT NEW_BUNDLE --media CANDIDATE.mp4 --reference BENCHMARK.mp4
python scripts/review_workbench.py serve NEW_BUNDLE --port 8765
```

打开 `http://127.0.0.1:8765/`。服务只监听本机，只读，无写入、审批或后端调用接口。支持单区间 HTTP Range 以便拖动和跳转，不列目录、不跟随目录外链接。编码不被浏览器支持时显示错误，不能据此批准。原曲和视频时间分别显示；可开启原曲与候选同步、校准候选偏移并选择单路监听。同步设置保持待核，详见gap-integration-guide.md。

构建复制实际媒体，绑定原文件和副本 SHA，不转码、不变速、不覆盖正式项目。必须使用新目录保留旧会话。包内含原媒体与本地路径，仅本地使用，不自动上传。

逐行修改候选时间、演唱者和边界观察。逐镜可记录主唱口型、DJ搓碟声画、群众反应及镜长、正反打轴线、运镜路径、光色美术和情绪推进。

设计编辑器读取已有正式文件，缺失时给未填草稿，不按镜数或时长制造创作：

- development：导演提案、来源、参考职责、空间、物理灯光与材质、ZY路由。
- story_plan：八部门逐镜交接及 Continuity Bible。
- sequence_direction：来源、窗口、信息、机位、人物路径、切点、光色、美术、表演。
- music_structure：真实听辨的段落、cue和情绪；能量不等于音乐语义。
- style：用户风格锁；草稿不能擅自转成 accepted。
- visual_edit_plan：逐镜音乐锚点、主唱、切点关系与剪辑目的；导演批准保持关闭。

导出按钮下载全部歌词、观察、播放事件和设计的待核 JSON。离开前先导出；当前无浏览器持久保存，不自动写项目。随后运行：

```powershell
python scripts/review_workbench.py import-draft PROJECT NEW_BUNDLE EXPORTED_DRAFT.json
```

导入核验项目、会话、输入版本、媒体副本、歌词身份及有限且位于原曲内的时间；原子落盘前再次比较版本。原项目新增或改变设计使旧会话失效。新记录保存到 `state/review_workbench/`，独立随机文件名保留历史。

外层始终 `decision=pending`、`agent_inspected=false`、`dynamic_verified=false`、`artistic_approval=false`。导入入口拒绝 approve，不修改正式状态。正式采纳使用独立事务入口：

```powershell
python scripts/workbench_adoption.py timing PROJECT NEW_BUNDLE IMPORTED_RECORD
python scripts/workbench_adoption.py design PROJECT NEW_BUNDLE IMPORTED_RECORD
```

时间采纳保留官方歌词，检查边界和独立间奏，仍保持逐行待核。携带机器对齐来源时，逐项匹配实际成功任务、原曲和歌词指纹、输出指纹及边界；机器成功不授予时间批准。设计须六份同时通过结构编译，才原子写入正式设计并归档旧版；撤销下游旧批准及过期执行记录，重新生成提示词并重审。不能直接改名草稿冒充已批准生产文件。

独立MMS对齐已接入正式时间来源校验：采纳时写入`boundary_source=forced_alignment`、实际任务ID、原结果指纹和逐行`forced_alignment_word_range`。正式门禁保留原ASR来源校验，边界改用当前独立对齐的完整词范围；缺失、串项目、输入/输出替换、局部词冒充整句和旧复核均阻断。原曲与官方歌词必须与实际执行一致，捕获时的timeline作为历史快照保留；新边界采纳后不因其自然变化形成循环依赖。机器置信分数不代替听辨声明，采纳后`decision=pending`。

重建页面仅恢复与当前时间轴SHA和逐行身份、边界一致的复核说明，并绑定该复核文件版本；旧说明不能错套到新行。页面保留已有间奏并支持删除/重填，拒绝重叠区间。旧版导出缺少`interludes`字段代表未编辑，保留既有间奏；明确传空数组才代表删除。间奏仍为待核草稿，不随点击获得批准。

删镜或合镜后，提示词编译器将过期SH制作包及keyframes/motion包复制到`state/prompt_history/<运行ID>/`，在同一文件事务发布新包并移除旧活动文件。删除必须绑定旧文件版本，期间文件集合或内容改变则拒绝；写入失败恢复原包。此机制只清理制作包，不删除原始图片、音视频或供应商任务记录。

## 本地机器观察与对齐

`local_perception.py` 使用本地 Qwen2.5-Omni-3B 对真实媒体分段推理，保存模型、媒体指纹、抽取命令、张量形状和原始响应。完整音频覆盖与稀疏视频采样分别标注；输出只为机器观察候选，不证明逐帧口型或艺术批准。

`music_capture.py` 实际计算起音、节拍、能量候选，不能凭能量填写音乐语义段落。`forced_alignment.py` 使用中文罗马化和 MMS-FA 强制对齐官方歌词，保存逐词分数；低分、与旧边界冲突之处仍须复核。三者使用隔离 Python 环境与本地模型，不调用视频生成后端；环境依赖见脚本说明和实际运行回执。
