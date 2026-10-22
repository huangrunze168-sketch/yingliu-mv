# 映流 MV · n8n 工作流（飞书存储 + 模型 API）

## 架构

```
飞书多维表格（唯一状态存储）
        │  定时扫描：业务步=提供素材 且 已确认
        ▼
飞书租户 Token → 查记录 → LLM 生成 ZY 提示词 → 校验 → 提交视频任务
        │                                                    │
        └────────────── 轮询 → 回写业务步/提示词 ─────────────┘
```

n8n 负责编排，**不执行本地命令**；提示词由 LLM 按 ZY 方法构造，出片由视频模型 API 完成，状态全部写回飞书表。Cloud 与本地 Docker 两种部署都适用。

## 本地 n8n 实例（可选）

不想用 Cloud 时，在装了 Docker 的机器上跑：

```powershell
powershell -ExecutionPolicy Bypass -File start-n8n.ps1
```

脚本会拉起 Docker Desktop（未运行的话）、创建或复用 `yingliu-n8n` 容器、等健康检查通过，最后输出 `http://localhost:5678`。数据存在 Docker volume `n8n_data`，重启不丢；首次访问需要创建管理员账号。

容器内导入工作流（不需要 API key）：

```powershell
docker cp yingliu-mv-feishu-modelapi.json yingliu-n8n:/tmp/wf.json
docker exec yingliu-n8n n8n import:workflow --input=/tmp/wf.json
```

## 导入

1. n8n → Workflows → 右上角 `…` → **Import from File**，选择 `yingliu-mv-feishu-modelapi.json`
2. 按下面清单替换占位符
3. 手动执行一次验证，再激活

## 必填占位符（4 个）

| 占位符 | 填什么 | 从哪拿 |
|---|---|---|
| `REPLACE_FEISHU_APP_ID` | 飞书自建应用 App ID | open.feishu.cn 开发者后台 |
| `REPLACE_FEISHU_APP_SECRET` | 对应 App Secret | 同上 |
| `REPLACE_LLM_API_KEY` | LLM API Key | DeepSeek / 其他兼容接口 |
| `REPLACE_VIDEO_API_KEY` | 视频模型 Key | 服务商后台 |

## 视频接口（modelsnexus 实测）

| 项目 | 结果 |
|---|---|
| 模型 | `sd2.0海外版-480p/720p`、`sd2.5海外版-480p/720p` |
| 提交 | `POST https://modelsnexus.org/v1/video/generations`，必填 `model` + `prompt` |
| 时长 | `duration`，不传默认 5 秒 |
| 比例 | 不支持 `size`（返回 `unsupported video parameter: size`），比例要写进 prompt |
| 返回 | `{"task_id": "...", "status": "queued"}`，异步 |
| 查询 | `GET https://modelsnexus.org/v1/videos/{task_id}`，返回 `status / duration / billing_status` |
| 取消 | 不提供取消接口，提交即预留额度，无法撤回 |

## 飞书前置条件

- 自建应用开通多维表格权限：`bitable:app`（读写记录）
- 把该应用添加为这张多维表格的**协作者**，否则接口返回权限错误
- base token `KelhbqVDJadSUcs0H6vcTtGdnCh`、table id `tbl4I2dLkuAi3DSf` 已内置在节点里

## 与 Skill 的字段映射

| 表格字段 | 对应 Skill 产物 |
|---|---|
| 业务步 | 六步流程 |
| 当前阶段 | 六阶段状态机 |
| ZY方法声明 | `design.json` 的 `method` |
| 全能参考绑定 | `global.omni_reference`（asset + role） |
| 当前制作包/提示词 | `prompts/SHxxx.json` |
| 生成任务与回执 | `state/jobs.json` |
| 交付音轨来源 | `project.json` 的 `delivery_audio` |

## 与 Skill 的差别

这套编排**替代不了** `yingliu-mv` Skill：

- 没有本地 FFmpeg，不能拼片、抽帧、做 technical 校验
- 没有审批链（`approve` 的证据绑定与失效机制）
- 没有时间轴对齐校验
- 不能保证片段时间与音乐窗口一致

它的定位是「云端轻编排」：把需求和提示词这一段的重复劳动自动化。成片合成与交付验收仍需本地跑 Skill，或人工剪辑。

## 已知扩展点

- **实时触发**：改用飞书事件订阅 + Webhook，替代 10 分钟轮询
- **失败重试**：给 HTTP 节点加 Retry，并把错误写回「错误/阻塞原因」
- **结果回传**：视频生成完成后上传到飞书记录的「最终MV」附件字段
- **多镜头循环**：`校验并解析` 已输出 `shots` 数组，后续可加 Split In Batches 逐镜提交
