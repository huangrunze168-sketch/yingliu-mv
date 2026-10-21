# 返修执行与依赖重审

`visual_revision.py`检查修复计划内容；`repair_state.py`把计划接到旧媒体、失败观察、提交作业、新媒体及重新批准。登记和闭环均不声明Agent已实际听看，不自动填入艺术批准。

## 建立返修

```bash
python scripts/repair_state.py open PROJECT --clip-id OLD_CLIP --plan PLAN.json --failure-review FAILED_REVIEW.json
```

失败媒体必须已登记来源并实际可解码。失败审片必须绑定该视频路径/SHA、`decision=reject`、`watched_full_duration=true`及视频时长内的正向时间码；`simulation_only=true`不允许。计划中1–3个`actual_drift`主导失败的维度/证据须逐字对应失败观察，保留原因、`change_only`与`preserve_exactly`。结构失败返回Scene Master。

建立记录保存冻结计划、失败媒体、原来源/请求快照和失败审片声明，并原子撤销本章、全片及涉及代表章的旧批准，归档撤销前版本。一个章节只能有一个开放返修；失败不部分撤销。快照保留事实，不认证观察者真的观看过媒体。

## 新任务与闭环

新提交必须带当前`repair_id`和`repair_plan_sha256`，与冻结计划对应。普通prototype/batch标签不能掩盖开放返修。若原提示词不变，冻结计划须另写有据的`same_prompt_retry_reason`；默认要求返回设计编译。该理由是明确声明，不由程序猜测生成失败原因。

`submission_jobs`准备与派发均重验返修状态及版本；准备以后才出现返修或计划改变，旧作业不得派发。未知生成结果继续回查原任务，不因返修另行重提未知任务。

```bash
python scripts/repair_state.py close PROJECT --repair-id REPAIR --clip-id NEW_CLIP
```

新媒体必须来自该返修绑定的新任务，或[实际本地变换](local-transform-contract.md)，属于同章且不同于失败文件；本章重审须批准该精确媒体，并逐项记录原失败维度的实际结果。旧媒体本体必须保留，不能删掉失败证据后闭环。闭环后替换媒体、来源或重审版本改变，旧闭环失效。本地变换结果先pending，close验证本章批准后才登记reviewed，不自动代填审片观察。

同章多轮返修通过`supersedes`及历史SHA关联；新一轮不能抹去上一轮失败事实。所有开放返修都会阻断正式选段。保留DJ等素材仍可正常作为原来源使用；裁切、放大或重剪后必须另走local_transform的执行/父链来源，不冒充新生成任务或无变化的保留素材。任意滤镜、补帧和变速不在当前支持范围。

## 交付前汇总

```bash
python scripts/repair_state.py assess PROJECT
python scripts/repair_state.py check PROJECT
```

assess实际检查当前逐章/全片批准和返修状态，写入`state/repair_assessment.json`。没有登记返修时明确写`no_registered_repairs`，不宣称历史上没有绕行返修；有登记则必须全部闭环。汇总绑定全片审片、选段、素材与返修依赖。交付状态依赖此汇总，缺失或撤销即不能ready。

正式顺序是：新媒体章节重审 → close → 正式选段 → 全片审片 → assess → 正式合成/技术验证 → 最终动态复核及交付包 → ready。没有返修也必须在全片审片后执行assess。来源和返修检查不能替代实际动态/口型审片；交付包另见delivery-package-contract.md。

## 已验证范围

单元反例使用真实本地测试视频和明确的供应商/观察夹具，覆盖重复开放、无效失败媒体/时间码、错误计划、新任务绑定、旧媒体冒充修复、重审缺失、版本改变、多轮历史和交付汇总。完整本地生产契约测试实际运行前置门禁、预算预占、模拟供应商回执、本地媒体登记、新章批准和正式选段；不把测试批准当实际影片评价。真实重制项目未因此补写任何历史返修或审片。
