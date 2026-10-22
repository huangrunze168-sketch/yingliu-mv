# 验证记录

日期：2026-10-22。版本：1.0.2。

- `python -X utf8 -m unittest discover -s tests -v`：12 项通过（含依赖缺失、路径、缓存失效、帧量化和媒体链路）。
- skill-creator `quick_validate.py`：Skill is valid!
- `python scripts/mv.py doctor --smoke`：当前环境 Python 3.14.3、FFmpeg/FFprobe 8.1，以及所需编码器、解码器、滤镜、封装能力均通过；实际完成合成、ALAC音频、抽帧、拼接。
- 模拟未安装、无法启动和缺少编码器的环境，均返回 local_ready=false。外部能力保持 unverified。
- 核心脚本通过 Python 3.10 语法兼容检查；实际执行环境为 Python 3.14.3，未在其他系统或 Python 3.10 解释器上运行。
- 实际使用 FFmpeg 生成两秒合成音频、图片与视频，完成预演、抽帧、样片记录、选段、母带合成和交付状态链。
- 验证原曲变化使批准失效、静帧观察不能作为动态通过、媒体替换使观察过期、图片变化使旧预演失效、项目初始化不覆盖已有目录、项目路径越界拒绝。

所有审查者和批准均为隔离合成测试声明，不代表真实观看、真实口型通过或真实影片验收。未运行付费生成、真实 ASR 或宿主 Agent 视觉；未测量真实制作 token 节省比例。未修改或发布原工作流，未安装到全局 Skill 目录。

新增 ZIP 新用户演练：使用独立模拟主目录安装、在另一目录调用 CLI，完成六阶段模拟；8秒、640×360、24fps、192帧实际出片，完整解码成功，成片解码音频与母带哈希一致。具体操作见 references/new-user.md，修复记录见 NEW_USER_AUDIT.md。模拟状态固定为 simulation_completed，不作为真实生产批准。
