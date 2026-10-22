# 剪辑与交付

先完成逐段实际审查，再填写 edit.json。每段保留实际路径、in、duration、来源回执；未完成镜头不得用黑帧静默补足。正式 render 要求前五阶段当前批准有效，选段总时长与权威时间轴一致。

render 使用 FFmpeg 将选段统一到项目画幅和帧率，按序拼接；不自动变速、插帧或生成内容。交付音轨默认取每个片段自带的音频（视频模型生成的演唱与环境声），统一转 AAC 48kHz 后拼接；母带不重贴，只作为时间基准与备用。若某段素材没有音轨，render 直接报错；确需回退到母带时把 `project.json` 的 `delivery_audio` 设为 `master`，此时按原曲编码复制或 ALAC 无损封装。视频统一 H.264，需要相应 FFmpeg 编码器。

剪辑决定仍由 Agent/用户完成。当前本地渲染器支持硬切，音轨可取自片段音频或母带主轨，不实现 J/L 额外声音轨、转场特效、字幕烧录或专业调色。当前自动验收链要求本地 render 产生的 technical.json 输入指纹；外部剪辑输出需另做可追溯导入适配，不能只改媒体文件冒充同一次渲染。

输出 output/final_mv.mp4 后，实际完整有声审查并写 final_review.json：media、observations、decision、evidence、unverified。final_review 必须引用当前视频的 audiovisual 观察；若仍有未核实项，阶段保持待核。口型只有实际检查后才可写结论。

运行 report 生成文件清单与状态；最终用户确认后再次 report。只有六阶段当前确认有效才标 completed。工作流不把 FFprobe 技术信息当作艺术或口型验收。

项目复盘简要记录效果、费用、失败原因和下一次改进，不要求修改工作流源码或发布新版本。
