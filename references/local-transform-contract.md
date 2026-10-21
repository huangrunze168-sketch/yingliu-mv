# 本地裁切、放大与按帧重剪的正式来源

`local_transform.py`只执行冻结返修计划明确允许的本地操作，不覆盖原文件，不创建供应商任务，不把数码裁切当成重新拍摄或新机位。必须先按repair-state-contract.md建立绑定失败媒体和观察的开放局部返修；结构性失败仍返回Scene Master。

## 实际调用

```bash
python scripts/local_transform.py PROJECT --plan TRANSFORM.json
```

返修冻结计划须含`permitted_local_operations`，例如`["crop", "scale"]`。实际操作集合必须是其子集；不能执行后再给旧计划补许可。

变换计划必须明确：

- `source_clip_id`与全新`replacement_clip_id`，源须是该返修原失败素材。
- `repair_id`及`repair_plan_sha256`。
- `frame_ranges`：逐段整数`start_frame`/`end_frame`，半开区间。顺序就是实际输出顺序；禁止重复、重叠、循环或克隆源帧。
- 可选`crop={x,y,width,height}`，为源画面内偶数像素矩形；未给crop就保留源画布。
- 明确`output_width`、`output_height`及`scale_method=lanczos|bicubic`。
- `audio_policy=preserve_selected_source`；源确无声音时须如实写`source_has_no_audio`，不能静音已有音轨或拿母带替换。
- `reason`。源时间重排还须`temporal_reordering_reason`；改变画面比例须`aspect_change_reason`及冻结计划中的`aspect_stretch`许可。

支持操作名为crop、scale、frame_selection、reorder、aspect_stretch。未知字段、任意滤镜/命令、无实际变化的重编码、布尔帧值、越界范围、无依据时间重排均被拒绝。保留原fps和CFR；不变速、不补帧、不以拉长末帧填时长。

## 执行与证据

每次实际运行建立独立`analysis/local_transform_runs/RUN_ID/`，保留原始变换计划和开放返修的字节快照、源probe、实际编译滤镜、输出媒体、执行命令、PID、启动/结束时间、stdout/stderr与退出码。源及输出SHA、当前执行器版本与记录一起绑定；终态执行回执自身的SHA进入来源登记。

命令实际运行后，还会全片解码、核对输出帧数/CFR/画布/声道/采样率。有声音的素材实际比较“原源帧对应选段”和衍生音轨的解码PCM24散列；包括重排时的相同选段顺序，不用响度/波形相关性代替数据匹配。原音轨的编码字节会因裁切及无损封装改变，不能声称保持AAC编码载荷不变。

执行失败保留失败记录，不发布正式来源或覆盖原素材；捕获到中断时终止并等待本次子进程，不能留下仍写文件的任务却报告完成。输入或返修版本在运行中改变，会拒绝发布。

## 待审、闭环与追溯

成功后仅登记`kind=derived`来源与`status=pending`新素材，`artistic_approval=false`、`dynamic_verified=false`。必须对新的精确媒体完成本章动态重审，再运行repair_state.close。close消费有效批准后才把素材登记为reviewed，原批准不能沿用。

来源保留父素材快照、冻结计划、冻结返修与实际执行回执。正式准入沿父链递归核验；多轮修复及用户授权保留的原片也保留各自来源，授权撤销、原片删除或回执替换会阻断。循环及异常深链不能递归自认证。

数码放大会明确记录`upscaled=true`及源/输出像素，不创造真实高分辨率细节，也不改变源透视。裁掉动作、时间重排、人物朝向和声画接点是否合理，仍需基于完整动态媒体审片；机检与散列不能判断这些创作结果。

本地来源回执不是可信身份签名，拥有同样文件权限的执行者仍能手写文件。该模块阻止正常生产入口遗漏执行/版本证据，不宣称能认证观察者真的听看或拦截外部任意调用。真实项目没有被追认历史裁切和批准。
