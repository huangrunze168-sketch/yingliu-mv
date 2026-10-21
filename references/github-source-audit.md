# 本地 GitHub 源码研究索引

2026-10-14追加两个完整仓库：ProjectAdA--public与jungucho92--moviechat-sseg，均非浅克隆且git fsck --connectivity-only通过。新来源及真实标注导入入口见dual-source-reference.md；此前16仓库清单是历史批次，不能当作追加后总数。

2026-10-14 按“先把相关项目代码下载到本地，再梳理细节”的要求，16 个主仓库保存在主项目的同级目录：

`<WORKSPACE>/github-research`

研究交付：

- `../github-research/README.md`：逐仓库名称、提交、文件数量、工作文件大小和子模块状态。
- `../github-research/repositories.json`：实际 Git 提交、远端、许可证文件指纹与完整性清单。
- `../github-research/DEEP_REVIEW.md`：关键源码路径、项目适用范围、许可证区分、当前 MV 十一项差距和后续验收。

研究材料位于本机同级目录，未作为核心运行依赖。其他机器应按真实远端与锁定提交重新取得源码。主仓库不嵌入第三方 checkout。

关键发现：电影画像生产入口还不完全一致；MovieNet 包含式终点帧需要加一；平均镜长和单镜分位数不能直接对比；越轴重置需实际桥接镜证据；人物运动向量与入出画边缘需拆开；艺术部门填表通过仍需画面验收。

完整克隆的最新状态见 `../github-research/full-clone-verification.json`，其中逐仓库/子模块记录完整历史、Git对象及LFS对象核查。`workflow-coverage.json` 和 `workflow-run-checklist.md` 覆盖28个工作环节，记录代码入口、输入输出与外部执行要求，包含风格冻结、人物上传、交付打包和复盘发布等SOP环节。

本次未执行第三方应用安装、模型推理、大规模电影下载或上述差距的代码改造。后续优先修复统一预检、轴线/动线和剪辑执行语义，再做空间预演、美术灯光 look test 和真实语料导入。
