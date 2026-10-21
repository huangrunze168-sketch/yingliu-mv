# 正式回片来源与素材准入

`media_origin.py`接通生成任务完成回执、实际HTTP下载、文件SHA和正式剪辑素材登记。来源登记仅证明可核对的来源关系，不批准表演、运镜、口型或艺术质量。仍需逐章动态审片及全片审片。

## 新生成素材

1. 使用`submission_jobs.prepare -> dispatch -> poll`保留原供应商任务。仅原任务明确completed且原始响应ID/状态一致时可下载；pending、unknown和failed均不能登记正式回片。
2. 使用`python scripts/media_origin.py PROJECT --job-id JOB --output-pointer /实际输出字段`实际GET任务响应中的URL。输出字段由当前真实模型响应确定，不能猜字段或另找URL替代。
3. 下载器保留原始请求URL、重定向后的URL、HTTP状态、响应头和实际下载SHA；失败保留failed_unregistered记录及已下载文件，不重新提交生成任务。HTTP下载失败与生成失败分开。
4. `register_download`也接受显式外部下载适配器的真实HTTP证据，核对同任务URL、200状态、路径、SHA、原提交契约和实际可读视频帧。此接口是适配器交接，不是HTTP证据真实性的签名认证。
5. `generated_clips.json`每条正式素材增加`origin_receipt_path`，指向生成的`state/media_origins/*.json`。镜头归属来自原契约`edit_shots`，不能任意扩大到其他章节或镜头。

下载器不自动写`reviewed`，不自动创建审片批准。登记失败、解码失败或源文件改变，均不能靠填写旧SHA继续使用。

## 用户允许保留的素材

调用`register_retained`必须提供实际视频、章节/镜头、授权文本文件、逐字授权摘录、记录者和用途。适用于用户明确允许沿用的DJ搓碟等素材。记录标注`recorded_statement_not_authenticated_user_identity`，保留授权来源的边界：本地文字不是已认证的用户身份，不得用自行编写的授权替代人类原话。

授权文件、视频或归属改变即失效。保留素材同样必须通过当前项目逐章动态审片，不追认旧片为合格，也不伪造一次新的生成任务。

## 正式入口

- `chapter_admission.require`逐条核当前媒体、来源、任务/授权与章节批准，缺少来源登记即拒绝。
- `edit_assemble --render`记录`origin_versions`并在原子提交时核验全部来源文件版本，防止检查后替换。
- 正式`stitch_chapters`重新准入，并与选段时的来源版本比较。
- `delivery_state.dependencies`重新准入并绑定来源依赖，旧ready不能忽略来源撤销或删除。
- `workflow_audit --phase before-delivery`单独报告`media_origins`检查。

技术预览仍可明确绕过正式准入，不能转换成正式交付。完整任务JSON也纳入版本，因此任务记录（包括成本信息）改变会要求重新核对正式选段依据；不会自动改变素材身份或自动认可收费结果。

## 验证范围与剩余工作

专项测试实际运行本地HTTP服务器并GET下载真实测试视频；覆盖pending、错误任务/URL、文件/HTTP证据替换、镜头归属伪造、无来源、授权撤销和非视频文件。完整剪辑链测试使用真实门禁、预占和实际本地媒体，供应商响应与HTTP记录明确为测试夹具，不冒充付费后端实测。

新生成及[固定本地变换](local-transform-contract.md)返修已接入[返修状态契约](repair-state-contract.md)，双版本/LRC/ZIP已接入[交付包契约](delivery-package-contract.md)；仍缺受控真实MCP适配器、动态听看真实性保障和真实项目全链交付。拥有同样文件权限的执行者仍可能手写记录；本地指纹与测试不能证明已拦截所有外部调用或所有虚假报告。真实重制项目没有被补写历史任务或审批。
