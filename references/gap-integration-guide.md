# 八项功能补齐使用说明

新项目执行顺序以[当前实际执行流程](actual-execution-workflow.md)为准；本文件说明对应阶段的细节。

本次增加执行与核验入口，不为现有影片补造批准。默认视频后端仍关闭。测试媒体及测试签名不代表真实电影或口型验收。

## 十二步持久运行

`python scripts/workflow_run.py run PROJECT --plan PLAN.json`；后续使用`resume`，只检查使用`check`。命令按十二步顺序执行，每次最多新完成一步；人工材料不足停在waiting_review，机检通过仍停在waiting_approval。只有当前步骤获得独立workflow_step签名批准才可进入下一步，第12步也须批准。缺外部信任目录或其启动固定摘要时不放行，详见[逐步批准锁](workflow-approval-locks.md)。对中断或失败且可能写过产物的命令保持recovery_required，保留原始run_id、输入、代码与进程日志，不用旧产物重建完成记录，不自动重派。

完整待填模板见[workflow-plan.template.json](../examples/workflow-plan.template.json)。计划格式如下，steps必须完整包含1至12，不能只填一个示例步骤：

```json
{
  "project_id": "项目绝对路径",
  "output_name": "final_mv.mp4",
  "submission": "state/submission.json",
  "steps": [
    {"step": 1, "inputs": ["input/lyrics.txt"], "outputs": ["state/timeline.json", "gates/gate1.json"],
     "commands": [["workflow_execution.py", "run", "{project}", "--step", "gate1"]]}
  ]
}
```

每步允许的脚本见workflow_run.ALLOWED。参数用列表传入，不通过shell解释。命令必须显式包含`{project}`，运行时替换为项目绝对路径。inputs须为命令读取且不改写的上游文件，outputs为实际产物；不要把会更新的同一个文件同时声明为输入输出。每步保存真实stdout、stderr、退出码、源码指纹、输入和输出指纹；上游失效时撤销后续缓存。check只读，不补建完成记录。第8步核验真实队列完成，第11步有演唱镜头时要求独立口型测量，第12步核验复盘证据。

后续步骤会修改同一产物时，须在其outputs中再次显式声明，例如第5步gate5更新第4步的storyboard.json。历史输出摘要保留；当前核验按最后一个已完成且声明该输出的步骤判断，不把正常提示词指纹更新误判为第一生产者失效。

第2步以`state/style_review.json`等待人工确认，字段为decision=approve、observer、具体evidence，以及当前style_sha256和music_sha256；这是版本绑定的人工声明，不认证实际听看。第3步复用reference_roles的原图/托管回执检查，不提前要求第8步的提交包。第4步检查设计和导演序列；依赖gate5提示词指纹的剧组报告移至第5步复核，避免顺序循环。第7步要求实际代表样片批准。

## 正式J/L同期声混音

镜头`crew.editing.audio_transition`允许J_cut/L_cut，同时填写正整数`audio_transition_duration_frames`；最后一镜必须straight。建立`state/sound_edit_plan.json`，逐镜顺序与分镜一致：

```json
{
  "fps": 24, "master_gain": 0.8, "stem_gain": 0.2,
  "entries": [
    {"shot_id": "SH001", "frames": 48, "audio_path": "input/foley.wav", "audio_sha256": "sha256:实际文件摘要",
     "audio_in_frame": 12, "role": "diegetic", "authorization": "真实声音来源与使用授权",
     "audio_transition": {"type": "J_cut", "duration_frames": 6}},
    {"shot_id": "SH002", "frames": 48, "audio_path": "input/room.wav", "audio_sha256": "sha256:实际文件摘要",
     "audio_in_frame": 12, "role": "diegetic", "authorization": "真实声音来源与使用授权",
     "audio_transition": {"type": "straight", "duration_frames": 0}}
  ]
}
```

声音必须有真实前延/后延素材。所有来源、增益和转场计划进入审片依赖；变化须重新选段和全片复核。正式stitch自动渲染：第一音轨保留原始音乐母带且为默认，第二音轨为无损ALAC同期声混音。这样可分别核验原母带载荷和实际混音PCM，不用混音冒充母带一致。

最终审片须在播放器或剪辑软件中选择第二音轨，完整听看后登记`"sound_mix_review":{"audio_track":1,"watched_full_with_mix":true}`。交付核验会重渲染期望混音并比较第二音轨实际PCM。原始母带版和现有原生声音诊断版仍打包；混音轨位于母带版容器中，声音计划随ZIP交付。

## 独立口型复核与事件测量

本实现提供完整逐帧口部图像、原始声音、独立验收签名和实测声画事件偏移。没有宣称实现自动音素识别模型。可信复核者仍须实际对照演唱内容、嘴型和主唱身份；签名只认证持钥方及所签材料，不证明人确实观看，也不消除误判。

先为每个口型镜头人工确定口部ROI，填写`{"shots":[{"shot_id":"SH001","roi":[x,y,width,height]}]}`，按镜头顺序覆盖全部口型镜头。ROI须在图像内，跟拍或大幅头动时应使用合适窗口并完整复核，不以静态ROI自动定位人脸。

```powershell
python scripts/lip_measure.py prepare PROJECT --video PROJECT/output/final_mv.mp4 --plan PROJECT/state/mouth_roi.json
python scripts/lip_measure.py check PROJECT
```

prepare实际导出每一帧PNG及对应音频，保存源版本、命令、帧范围和文件摘要，状态保持awaiting_independent_review。复核者对最终媒体建立以下payload，实际填写后签名为`state/lip_measure_review.json`：

```json
{
  "purpose":"lip_measure", "measurement_sha256":"sha256:逐帧材料记录摘要", "media_sha256":"sha256:最终媒体摘要",
  "full_audio_video_review":true, "tolerance_ms":80,
  "shots":[{"shot_id":"SH001", "phonetic_match":"pass", "observation":"具体逐帧音素与嘴型观察",
    "events":[{"video_frame":24,"audio_time_sec":1.0,"phoneme":"实际音素","lyric_line":0},
              {"video_frame":48,"audio_time_sec":2.0,"phoneme":"实际音素","lyric_line":0}]}]
}
```

每镜至少两个不同事件，覆盖每行演唱歌词；video_frame和audio_time_sec均为全片绝对位置。测量输出最大绝对偏移与中位偏移，全部事件须在设定容差内，容差不能大于100毫秒。唇形是否匹配由独立复核者负责，不从这些偏移值单独推导。存在口型测量文件时，交付包强制核验且绑定最终媒体，并打包测量记录与签名。十二步新编排的第11步对口型镜头强制要求这一证据；既有独立交付入口保留原人工审片兼容，不将旧记录升级为独立认证。

## 信任目录与签名

`python -m pip install -r requirements-validation.txt`。独立复核者持有Ed25519私钥；不要把私钥加入项目或版本库。启动者设置`MV_REVIEW_TRUST_STORE`为经其核验的外部公钥目录文件：

```json
{"reviewers":{"reviewer-key-id":{"public_key":"32字节原始公钥的Base64","purposes":["film_corpus","lip_measure"],"revoked":false}}}
```

待验文档不能自行指定信任目录或用自己携带的公钥认证。签名前须完成实际观察/标注验收：

```powershell
python scripts/signed_evidence.py sign REVIEW_PAYLOAD.json --private-key PRIVATE_KEY.pem --key-id reviewer-key-id --output CERTIFICATE.json
python scripts/signed_evidence.py verify CERTIFICATE.json --purpose lip_measure
```

## 电影语料认证与语义适配

默认advisory不变。enforce_envelope需要development.grammar_reference中的`certificate_path`，指向独立验收者签名文件。payload包含：

- purpose=film_corpus；profile_path、profile_sha256绑定实际统计画像。
- artifacts：原始合法标注和来源清单的项目内相对路径→SHA-256。
- annotation_review=pass、review_method：独立标注质量复核结论与具体方法。
- films：逐片canonical_film_id与edition，严格去重，数量须与画像film_count及minimum_films相符。
- semantic_mapping：与当前分镜等长的逐镜列表，含shot_id、scene_id、window、scene_kind、decision=applicable、rationale和reference_annotation。scene_kind须与当前镜头类型一致。

证书位于画像之外，防止自引用指纹循环。签名、画像、源标注或镜头适配变化均须重验；没有独立签名继续阻断，不因电影数量够多而自动认证。验收者真实性与标注可靠性仍取决于用户建立的信任目录和独立验收工作。

独立审计CLI可使用`python scripts/film_grammar_corpus.py audit PROJECT PROFILE.json --mode enforce_envelope --certificate state/film_corpus_review.json`，同样验证信任目录和当前材料，未签名或篡改证书返回非零。

## 批量队列

```powershell
python scripts/batch_scheduler.py init PROJECT --contract state/CH01_submission.json --contract state/CH02_submission.json --concurrency 2
python scripts/batch_scheduler.py tick PROJECT
python scripts/batch_scheduler.py run PROJECT --timeout-seconds 300
python scripts/batch_scheduler.py resume PROJECT --timeout-seconds 300
python scripts/batch_scheduler.py check PROJECT
```

也可用重复`--job-id`导入已准备的作业。tick只处理当前可运行的一轮，保存队列、原任务ID、回查次数和下次时间；run/resume持续处理直至完成、需人工介入或达到时限。超时保留pending，可继续resume。它按槽位准备下一批，复用现有预算和实际供应商并发限制，网络调用并发执行。pending按指数退避回查；unknown无任务ID、dispatching或polling中断要求先recover，不自动重派。完整队列核验须覆盖当前全部章节，包括已有代表样片作业。实际任务完成不等于来源下载、成本已结算或动态审片通过。

后端禁用时tick在任何准备、预算预占和网络调用前拒绝。本次没有启用真实项目后端。

## 复盘与发布核验

`python scripts/release_evidence.py test PROJECT`实际运行全量测试，绑定当前scripts/tests及Git HEAD。发布验收要求scripts/tests已提交；工作区尚在修改时用普通unittest验证，不把未提交代码声称为已发布版本。建立`state/retrospective.json`，填写project_id、regression_sha256、commit，以及非空字符串列表findings、changes、remaining_limits。

publication可为`{"kind":"local","commit":"实际HEAD"}`，或`{"kind":"remote","remote":"origin","ref":"refs/heads/实际分支"}`。remote模式只接受仓库已配置的远端名称，用git ls-remote只读核验实际发布的提交，不自动推送；local只证明本地版本，不声称远端发布。

`python scripts/release_evidence.py check PROJECT`核验材料。源码、测试、复盘或版本变化使旧结果失效；workflow_audit第12步现已关联该检查。

## 审片同步与ASR环境

工作台新增原曲与候选同步开关、候选偏移秒、当前播放位置校准、单路声音监听和时间显示。定义候选时间=原曲时间+偏移；歌词跳转、播放、暂停和时间拖动联动。对标片独立播放。同步设置进入待核草稿，不能代替正式时间批准。

基础校验与剪辑仍使用Python标准库和ffmpeg。全量回归还需安装requirements-validation.txt以验证独立签名；Node可用时执行真实工作台JavaScript交互测试。ASR独立使用Python3.12环境：

```powershell
py -3.12 -m venv .asr-venv
.asr-venv/Scripts/python.exe -m pip install -r requirements-asr.txt
.asr-venv/Scripts/python.exe scripts/asr_capture.py PROJECT --audio ORIGINAL_AUDIO --lyrics OFFICIAL_LYRICS --model-path LOCAL_FASTER_WHISPER_MODEL_DIR
```

模型目录必须已有model.bin；不自动下载模型。多模态观察继续使用requirements-perception.txt及local-perception-environment.md中的独立环境。
