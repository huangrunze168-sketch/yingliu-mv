# 实际交付包与ready边界

编码成功、解码成功、技术验证通过与完整交付是不同状态。`stitch_chapters`实际输出并验证后保留`technical_verified`；缺少当前交付包时返回失败，不将其打印成ready，不删除已输出候选片，也不自动登记用户接收。

## 正式执行顺序

1. 权威时间、当前门禁、正式选段、逐章/全片审片及返修汇总通过。
2. `stitch_chapters.py`实际输出成片、运行流属性/母带验证，进入technical_verified。首次缺包的非零返回不是需要重复渲染的信号，应继续打包。
3. `python scripts/delivery_package.py PROJECT --output-name final_mv.mp4`。缺少`state/final_media_review.final_mv.mp4.json`时只创建pending模板并阻断；模板绑定刚输出的实际文件，不能代填观看声明。
4. 对该最终文件实际完整有声审片后逐镜记录表演、声画动作和连续性；口型镜头须有具体口形时间点，不能用音轨包络代理。填写者须清楚声明观察身份及范围。
5. 再运行同一打包命令，实际生成两版媒体和证据、核验ZIP，最后调用delivery_state.promote。全部检查通过后才ready，`user_received`仍为false。

当前本地观察字段仍是绑定媒体的观察者声明，**不认证其真的观看过，也不构成独立嘴型测量**。包与音频报告保持`lip_sync_verified=false`，不夸大自动验收范围。真实影片缺少可信动态观察时仍须待核。

## 实际产物

- `master.mp4`：当前实际导出的母带版，逐字节绑定output_sha256。
- `embedded_audio.mp4`：同一画面载荷，按正式选段帧范围从原生成素材提取内嵌声音，作为诊断/交叉核对版本。无原生音轨或音轨未覆盖选段即失败，不用母带、补静音或变速伪造第二版。诊断版暂统一48kHz双声道无损封装；母带版维持原采样率、声道与载荷。
- `storyboard.json`、`timeline.json`、`lyrics.txt`、`lyrics.lrc`：官方歌词与正式逐行时间。LRC重新核验实际原曲时长、官方原文、ASR来源及时间批准；不从fallback生成正式歌词文件。
- `verification.json`：当前导出任务、规格和成片绑定的实际流/母带检查。
- `alignment.json`、`alignment_execution.json`：实际从母带和最终视频分别提取16k PCM，运行固定align_verify命令，保存命令、退出码、日志、输入及报告SHA。候选音轨必须确实来自候选视频，self_reference/AUDIO_ONLY_PASS/DIAGNOSTIC_PASS不可代替正式对齐。
- `final_media_review.json`：绑定该最终视频、分镜和时间轴的逐镜动态复核声明，完整覆盖全部镜头；口型与非口型分别处理。
- `embedded_audio_execution.json`、`repair_assessment.json`：原生音轨选段依据与实际返修汇总。
- `cost_audit.json`：登记作业、记录的原费用字段及台账一致性，估价/预占/未证实退款不代替实付；缺证据或改变即阻断ready，见production-cost-contract.md。不是供应商签名账单认证，未登记历史费用不推算。
- `manifest.json`与`delivery.zip`：明确成员集合及逐项SHA；核对ZIP实际内容、重复/缺失/额外成员、包内外清单及当前项目版本。

恒定音色或高度周期音乐下，包络相关搜索可能无法唯一决定滞后。两份实际提取PCM在窗口内逐样本完全相同时，align_verify明确记录`exact_pcm_samples`及零滞后；否则继续执行包络搜索。这证明该音频窗口相同，不证明人物嘴型正确。

## 版本与失败行为

每次交付状态读取都重新验证包，删除或修改ZIP、LRC、动态复核、源依赖或执行回执均不能沿用旧ready。进入ready的原子提交同时绑定已核验源文件、包及清单版本，旧任务不能批准另一个导出。

包使用导出ID与独立生成ID目录，不覆盖旧包。打包失败保留实际对齐执行目录，不批准候选包；需要修媒体时返回对应来源/返修链，不通过重写清单隐藏错误。当前命令没有自动通知、上传、发布或登记用户已接收。

## 当前验证范围

本地测试实际运行ffmpeg、ffprobe、align_verify、ZIP读写和完整正式剪辑链；固定本地裁切/缩放/按帧重剪另见local-transform-contract.md。最终观看/口形内容及供应商响应在测试中明确为合成夹具，不是实际影片评价。真实MCP适配器、可信动态观察、真实项目交付及复盘发布仍须完成，不能由交付包模块存在推导十二步完成。
