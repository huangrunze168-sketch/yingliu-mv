# Agent 原生视觉、样片与批量

## 能力与观察

项目配置 `agent_capabilities` 是能力声明，不是自动探测结果；启动时核对当前工具。默认 unknown。实际支持看图时直接使用宿主看图工具，不启动 Qwen 或其他本地视觉服务。

每次观察先按素材 SHA 查询 state/observations 中的有效记录。相同素材、相同参考版本且观察范围足够时复用；更换图片、职责、参照或审查问题时补看。`observe` 导入实际观察记录：

```json
{
  "asset_id": "SH001",
  "media": "clips/pilot.mp4",
  "observer": "实际 Agent 或用户名称",
  "scope": "sampled_frames",
  "evidence": "实际查看 frames 清单中 0、2、4 秒图片；不是完整视频",
  "findings": [],
  "unverified": ["motion_continuity", "lip_sync"],
  "references": ["input/person.png"],
  "frame_manifest": "state/frames/对应任务/manifest.json"
}
```

scope 可为 image / sampled_frames / full_video / audiovisual。最后两种只在实际工具/用户已完成相应观察时填写；evidence 说明覆盖区间与方法。CLI 不能认证观看真实性。

`frames` 按时长均匀抽取最多 24 帧，保存源视频指纹、时间码和帧路径；Agent 通过图片工具看这些图片，不能把抽帧进程成功当作已看。发现动作问题再请求动态检查，不反复无差别抽帧。

## 后端与预算

在 project.json 记录用户选定 backend、budget 和授权依据；密钥只用宿主凭证存储或环境变量。正式付费提交前检查真实模型能力、时长、引用上限、音频条件、单价与剩余预算；本 CLI 不代替这些供应商检查。

工具调用由用户 Agent 使用已有适配器完成。任务先记 planned，再记真实 ID 和 submitted/completed/failed/unknown。unknown 只能按原 ID 回查，不直接重提；不能用本地取消记录冒充供应商取消或退款。素材登记实际路径、来源任务和下载依据。

jobs.json 每项：id、shot_id、status、provider、cost、currency、receipt、media；cost 未知填 null。费用在同币种分别累计，禁止跨币种直接相加。扩批前用户确认本次范围和预算；工作流不自动扩张授权。

先最具代表性和难度的样片，再批量。pilot_review.json 记录样片、观察记录路径、decision、evidence、unverified。逐段审查写 clips_review.json；观察依据必须绑定当前素材。没有动态观察时保留 pending，不把图片分析升级为通过。

返修只改失败主因，保护已成功事实；修改 design 后重新 compile。用 pack --shot 获取公共锁、本镜和邻镜，不重写全曲。全片公共人物/服装/风格变化时扩大复核范围。

## 提示词构造（强制）

分镜阶段必须按 [ZY 集成](zy-cinematic-integration.md) 把 Scene Master、Creative Grammar 与连续性方法映射到 `design.json` 的 `global` / `scenes` / `shots`；口型、歌词与切点仍以 timeline/design 为准。本包不内置供应商提示词模板，`compile` 产出的 `prompts/` 只是本地制作包，转码到真实后端时按该后端 schema 另行处理。
