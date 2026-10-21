# 后端无关的本地转场

## dissolve：已接入选段和合成

在出镜镜头crew.editing中指定transition_type=dissolve和正整数transition_duration_frames。edit_assemble保留选段out_frame后的真实后延帧，下一镜音乐窗口不移动；stitch_chapters调用transition_render.join_picture从切点起叠化。后延不足、末镜叠化、叠化超过下一镜窗口拒绝，不自动补冻结帧或缩短音乐时间轴。修改分镜或清单仍须重新审片。

## J_cut/L_cut：独立声音轨与正式额外混音轨

命令：python scripts/transition_render.py audio_plan.json diegetic.wav。

audio_plan包括fps、sample_rate、channels以及entries。每个entry要求frames、audio_in_frame、audio_path、audio_sha256；出镜audio_transition可声明type=J_cut或L_cut及duration_frames。J_cut提前下一镜声音、L_cut延续上一镜声音，读取真实前/后延声音，不改变总时长。输出为独立PCM24同期声/音效轨，拒绝覆盖现存文件。

独立工具仍可输出同期声轨。正式流程现通过sound_mix接入：state/sound_edit_plan.json逐镜绑定真实声音、授权、SHA、转场和增益；选段/审片依赖、stitch及交付共同核验。交付第一音轨保留原始音乐母带，第二音轨提供无损J/L混音，逐项比较实际PCM载荷。最终复核须明确选择第二音轨并完成完整有声观看。配置见[八项补齐说明](gap-integration-guide.md)，不把混音版冒充原母带一致。

真实本地测试验证红/蓝混合帧、96帧时间轴、440/880Hz声音提前/延后、前延不足及旧文件保护；不证明艺术设计或供应商效果。
