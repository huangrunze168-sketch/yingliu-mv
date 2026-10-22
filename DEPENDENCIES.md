# 依赖清单与排查

## 本地运行

| 依赖 | 何时需要 | 检查方式 |
|---|---|---|
| Python 3.10+ | 所有 CLI 命令 | doctor |
| 第三方 Python 包 | 运行时无需安装 | 仅使用标准库，无需 pip install |
| FFmpeg 与 FFprobe | 抽帧、预演、正式合成、最终技术检查 | doctor --smoke |
| libx264、ALAC、PNG、MJPEG 等能力 | 视频编码、无损音轨、图片读写 | doctor 列出缺失组件 |
| 项目目录写权限、足够磁盘 | 建项目、缓存、渲染临时文件 | init/实际渲染时检查；磁盘需求取决于全片素材大小 |

FFmpeg 二进制不随本包分发。可从操作系统软件源或 FFmpeg 官方下载入口获取适合系统的构建；确认同时包含 ffmpeg、ffprobe 及 libx264。Windows 将 bin 目录加入 PATH 后重新打开 Agent/终端；macOS/Linux 同样保证两个可执行文件在 PATH。无需 CUDA、GPU、PyTorch、Transformers、Qwen、Pillow 或 OpenCV。

doctor 会实际启动程序并检查能力列表；--smoke 使用不到一秒的合成媒体验证 H.264/ALAC 封装、滤镜、PNG/JPEG 抽帧、拼接与探测。临时文件在本包内，结束清理，不调用联网服务、不读取真实项目素材。请将分享包解压到可写目录。默认超时每个检查 30 秒。

检查命令：

```powershell
python scripts/mv.py doctor --smoke
python scripts/mv.py doctor ../我的MV
```

local_ready=false 时 CLI 返回退出码 1；它不表示设计、compile 等纯 Python 命令也不能使用。local_ready=true 只代表本地工具与包完整性通过，外部能力仍须验证。项目参数可选；配置存在不证明凭证、视觉或后端可用。

## 完整制作所需外部能力

本工作流是一套 Agent Skill 加本地工具，不内置视觉模型、ASR 服务、图片生成或视频生成适配器。

- 图片观察：用户当前 Agent 实际提供的看图工具；没有时由用户查看。
- 动态/有声审查：Agent 确实支持视频/音频，或用户实际完整观看。只支持图片的 Agent 不能独自完成动态验收。
- 时间轴：用户已有对齐工具及逐行复核，或用户提供有依据的人工时间轴。普通 ASR 文本不自动等于准确对齐。
- 预演图片：用户现有素材/草图即可，不强制购买图片服务。
- 视频素材：用户已有视频可直接使用；新生成时需要已有生成工具、真实适配器、凭证、授权和预算。填写 backend 名称并不会自动连接服务。

接收者首次使用时，Agent 应说明这些能力的实际来源，缺失项标为待提供。CLI 不会自动安装模型或猜测接口。没有生成后端仍可做创意、分镜和现有图片预演。

## 开发与验证

运行 unittest 不需要第三方 Python 包；媒体集成测试需要 FFmpeg。skill-creator 的 quick_validate.py 是开发工具，不在分享包内，通常依赖 PyYAML；接收者使用本工作流无需安装它。agents/openai.yaml 由宿主读取，本地 CLI 不解析 YAML。
