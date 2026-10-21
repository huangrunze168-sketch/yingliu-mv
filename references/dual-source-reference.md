# ProjectAdA＋MovieChat-SSeg 双来源接入

两个仓库完整克隆于主项目同级github-research目录，无关联电影或视频下载。2026-10-14锁定版本：

| 来源 | commit | 职责 | 标注许可 |
|---|---|---|---|
| ProjectAdA/public | 7e9182897d30d355df7a838e8cd8b3b923a88364 | 视听设计语义标注 | CC BY-SA 3.0 |
| jungucho92/moviechat-sseg | e85354d91b98aaa87cdf3bfe55fe3c70ee213be9 | 镜头节奏、场景结构 | CC BY 4.0；代码MIT |

来源：[ProjectAdA标注目录](https://github.com/ProjectAdA/public/tree/master/annotations)、[MovieChat-SSeg](https://github.com/jungucho92/moviechat-sseg)。遵守各来源署名及共享条件；代码许可不代替数据许可。ProjectAdA署名FU Berlin/HPI的AdA项目及原标注作者；MovieChat-SSeg署名Jungu Cho、Seong Jong Ha、Hae-Gon Jeon，Video Scene Segmentation with Genre and Duration Signals，ICLR 2026。许可原始证据SHA随导入保存，不涉及影片授权。

## 本地入口

```powershell
python scripts/source_reference_import.py --ada-repo ../github-research/ProjectAdA--public --moviechat-repo ../github-research/jungucho92--moviechat-sseg --output references/corpus/dual-source-reference.json
```

导入器使用标准库，读取标注不执行第三方脚本；输出保存来源版本、职责、标注统计及所有实际输入文件SHA，写入前比较输入指纹。其他机器须重新取得对应版本并重建，不能沿用本机绝对路径证据。

## 工作流使用边界

1. Story Visual Development/Scene Master设计可查ProjectAdA人工标注类型作为参考，不复制字幕、对白、镜头文本，不把语义标注持续时间直接当作镜长。当前导入器汇总标注类型，没有完成逐镜景别/运镜对齐和语义抽检，不能宣称导演决策已自动消费所有标签。
2. Coverage/剪辑预演可查MovieChat-SSeg按视频类型分组的镜头帧长和每场镜头数。包含式终点按end-start+1计数，场景是包含式镜头ID区间，必须完整覆盖镜头。没有可靠FPS则保留帧数，不猜24FPS、不生成秒数包络。
3. 两库不得混为同一电影样本，视频片段、人工包、自动包和独立影片分别计数。没有评分、影片身份去重或人工质量验收，不声称建立数百部高分电影基准。
4. 当前输出是独立建议性参考摘要，不直接替换film_grammar_corpus画像schema，不自动开启生产硬门禁，也不改变用户的分镜、后端选择或已批准项目。

实际导入MovieChat为1000视频/116442镜头/7830场景；ProjectAdA为50人工包，137431条时间区间有效的标注，另4条无效区间排除并待质量核查。有效区间只证明可解析，不证明标注语义准确。
