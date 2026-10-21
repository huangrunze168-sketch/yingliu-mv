# 原生请求、托管来源与提交作业

当前任务禁止使用生成视频后端。缺少项目执行策略默认关闭，预占/派发/回查均先遵守execution_policy；下述接口只作本地校验及离线协议测试，不是启动后端的许可。见[mcp-handoff-contract.md](mcp-handoff-contract.md)。

`submission_contract.py`只校验制作包，成功不等于已经预占预算或允许直接调用供应商。CLI明确输出`dispatch_allowed=false`。视频派发使用`submission_jobs.prepare`和`dispatch`；外部适配器由用户选择，核心不默认供应商或模型。

## 上传与复用

每份绑定须有`upload_receipt_path`，对应当前本地SHA、托管标识、实际原始响应及该标识的JSON Pointer。改变源文件、托管标识或原始响应均使提交无效。

已有URL不必重复上传。`verify_hosted`比较实际GET下载的字节与本地源，并绑定HTTP证据及下载文件；状态是`hosted_verified`，只说明当前URL内容相同，不追认过去的上传过程或角色/艺术批准。仅填写一个URL或把状态写成uploaded不够。

外部上传适配器使用`record_upload`保存实际返回值；回执不是供应商签名，也不能证明拥有相同文件权限的人没有手工伪造。当前没有可信外部执行隔离层，不能将文件哈希夸大为安全认证。

## 原生请求与生成时间

`provider_request`保存实际要发出的请求体，`provider_request_fields`用明确JSON Pointer指向模型、提示词、时长、分辨率和每个参考标识。校验从请求体取值，与制作包/用户模型/实际上传绑定比较，不能另写一个“绑定正常”布尔值。请求体不保存凭证，外部传输层注入凭证。

`duration_sec`始终是音乐章节窗口。模型只接受整数等离散时长时，另外声明`generation_duration_sec`、`generation_trim_tail_sec`及理由；必须来自已核验的`min_duration_sec/max_duration_sec/duration_quantum_sec`。不能修改音乐窗口、变速补齐或把模型上限当剪辑镜长；未计划的额外生成时长阻断并返回正式包设计。台账分别保存请求生成时长和计划剪辑时长。

## 预占、派发、回查与费用

```bash
python scripts/submission_jobs.py prepare PROJECT --contract SUBMISSION.json
python scripts/submission_jobs.py status PROJECT --job-id JOB_ID
```

1. prepare重验全部正式前置、原生请求和上传来源，核对本项目相同账号标识的并发上限，然后先reserve预算，再原子发布prepared作业。发布失败且尚未派发时释放该预占。
2. dispatch只接受prepared作业，重验版本和现存预占后先写dispatching，再调用用户选定适配器。回执须保留原始响应，并把标准化任务ID/状态绑定到真实响应字段。
3. 异常或无可靠ID时记unknown并保留预占，禁止再次generate。pending/unknown且已有任务ID时，poll只回查原ID；跨任务或不匹配响应不能当作完成。没有ID的未知调用须外部查询恢复，不自动重试。
4. settle只接受绑定该供应商任务的实际费用响应，不从估价或完成状态推算成本。已扣费的在途任务可以如实记账，但不能因此变为视频完成。重复同额结算幂等；费用不同须明确对账或退款证据。
5. 已知费用超限或记账失败，保存实际费用和reconciliation_required，不能报结清；新预占/派发被阻断。设计审批撤销只阻止新请求，不阻止记录现有任务的真实收费。

当前并发计数仅覆盖本项目里登记的同一账号标识，不是供应商全账号任务监控；同账号其他项目及绕开此模块的调用不在本地控制范围。外部MCP仍须由受控适配器接入，核心不能拦截Agent直接调用工具。

## 当前实测与未完成项

离线作业测试覆盖预占先于适配器调用、请求/参考漂移、未知结果不重提、同ID回查、结算幂等、实际费用差异和并发阻断。另有完整生产契约集成测试，使用真实本地媒体与合成供应商回执，不调用付费后端。

真实项目9份既有参考已实际下载并逐字节核对，复用回执已保存；MiniMax-H3模型schema及并发上限另有本轮真实工具回执。没有因此提交新视频任务，时间轴仍待核。

请求在prepare逐字节独立归档，派发重验当前原件，已执行的回查/来源使用原归档。MCP本地交接、原任务上下文和迟到恢复已有离线测试，实际后端依当前用户限制不调用。回片与本地变换准入、返修及交付包已有强制契约；可信动态观察和真实项目交付仍未通过。不能把prepared/pending/completed或费用结清当作动态审片、口型批准、ready或十二步完成。
