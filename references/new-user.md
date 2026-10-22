# 新用户：从解压到本地出片

## 安装与验证

解压 ZIP，将 `yingliu-mv` 整个目录放入自己的技能目录，刷新技能列表后使用 `$yingliu-mv`。也可以不安装到全局，直接让 Agent 读取解压目录的 SKILL.md 并运行绝对路径下的 scripts/mv.py。

本地需要 Python、FFmpeg、FFprobe。安装具体方法见 DEPENDENCIES.md。以下命令假定终端位于 Skill 目录；PROJECT 可用绝对路径，带空格时加引号。从其他目录运行时，把 scripts/mv.py 替换为绝对路径。

```powershell
python scripts/mv.py doctor --smoke
python scripts/mv.py init "../我的MV"
python scripts/mv.py status "../我的MV"
python scripts/mv.py pack "../我的MV"
```

init 不覆盖已有项目。项目建好后直接 status/pack/resume 人工工作，无需再次 init；本版没有名为 resume 的 CLI 命令。

## 六阶段的实际交接

1. **准备**：把原曲、歌词放到 input；在 project.json 设置路径、画幅及实际工具能力。查询 status；机器条件满足后，由用户确认当前准备产物，再运行 approve。没有生成后端不影响这一步。
2. **创意**：Agent 实际分析歌曲/歌词和参考，填写 concept.json；对齐工具或用户提供有依据的 timeline.json，并保存 evidence_path 指向的原始依据文件。不能靠修改 verified 绕过实际复核。status 通过后由用户确认。
3. **分镜**：以 examples/design.json 为字段参考填写实际全曲设计，不照抄示例时间。先准备每镜 image 指向的现有图/草图，compile，render --mode preview。对完整预演作实际动态审查，按下方说明登记观察、创建 animatic_review；用户确认后进入样片。
4. **样片**：选用户已有素材，或在用户授权下用已有工具生成代表样片。本次离线演练只使用 FFmpeg 合成素材。实际审查后登记 pilot_review，再由用户确认。frames 的图片分析只作补充，不能直接替代动态审查。
5. **制作**：每段视频放在 clips，保存来源回执。填写 edit.json（media/in/duration/receipt）和 clips_review.json（reviews 数组，每项为当前素材的审查记录）。来源可以是用户自带、本地合成或真实供应商任务。外部任务另记 jobs.json，未知费用保持待核。status 会在确认前检查选段越界和时长；用户确认后可正式合成。
6. **交付**：render --mode final，实际完整有声审查，登记 final_review。status 条件满足后用户确认，再运行 report。没有实际视听能力时，输出技术候选并保留待审，不能宣布艺术/口型通过。

每阶段确认命令：

```powershell
python scripts/mv.py approve "../我的MV" --reviewer "实际用户" --evidence "用户对当前版本的明确确认原文"
```

## 登记观察和审查

在项目内建立 `state/drafts/observe.json`，字段参考 examples/observation.json。observer、scope、evidence、findings、unverified 填实际观察；原生工具看完整视频填 full_video，完成有声审查才填 audiovisual。没有看的内容保留 unverified。

```powershell
python scripts/mv.py observe "../我的MV" --record state/drafts/observe.json
```

`--record` 相对路径按项目目录解析，绝对路径按实际路径读取；记录内媒体及参考路径始终相对项目。返回 observation 路径后，用它创建待审模板：

```powershell
python scripts/mv.py review-init "../我的MV" --stage storyboard --media output/animatic_preview.mp4 --observations state/observations/实际返回的ID.json
```

storyboard 对应 animatic_review.json，pilot 对应 pilot_review.json，delivery 对应 final_review.json。模板初始为 pending；实际审查完成才填写 decision=approve、evidence 和未核实项。review-init 不覆盖已有非空审查；返修直接编辑原审查并重新观察、更新引用。制作阶段 clips_review.json 使用同样结构组成 reviews 数组，不能只写一个 approved 字符串。

典型审查记录：

```json
{
  "media": "clips/SH001.mp4",
  "observations": ["state/observations/实际ID.json"],
  "decision": "pending",
  "evidence": "填写实际审查证据",
  "unverified": ["待实际动态审查"]
}
```

## 一键离线演练

不调用 ASR、视觉模型、图片/视频后端，不需要真实用户素材。使用现有 FFmpeg 生成测试图、视频和音频，模拟审查仅为夹具，最终状态是 simulation_completed，不能用于真实制作批准。

在 Skill 目录运行（修改 ZIP 和新输出目录路径）：

```powershell
python scripts/simulate_new_user.py --archive "../yingliu-mv-v1.0.4.zip" --output "../全新离线演练"
```

输出目录必须尚不存在；脚本不删除或覆盖已有目录。它在输出目录里建立模拟用户主目录并从 ZIP 安装，在其他工作目录执行所有 CLI，覆盖六阶段、横竖/异帧率素材、非整帧切点、越界反例、失效复核、完整解码和母带音频哈希比较。

输出 `simulation-result.json`、`command-log.json`、项目下 output/final_mv.mp4。它验证当前机器的新目录安装和技术链路，不模拟全新操作系统安装 Python，不验证宿主界面中 Skill 注册，也不证明 Agent 的真实视觉/审美或口型能力。
