# 本地观察环境与实际执行边界

本轮使用独立Python3.12环境`.perception-venv`，不修改全局Python。实际安装版本由`requirements-perception.txt`保存；CUDA包使用PyTorch官方cu124索引。模型与环境在.gitignore中，不进入代码提交。安装和首次模型下载需要网络；local_perception使用已下载模型离线推理。

主要兼容组合：torch/torchaudio 2.6.0+cu124、torchvision 0.21.0+cu124、transformers4.57.6、librosa0.11.0、av12.3.0、qwen-omni-utils0.0.9、uroman。该组合已在RTX4060Ti16GB实际完成推理；不能保证其他硬件与驱动兼容。早期av19及librosa1.0导致的实际失败记录保留。

```powershell
py -3.12 -m venv .perception-venv
.\.perception-venv\Scripts\python.exe -m pip install --extra-index-url https://download.pytorch.org/whl/cu124 -r requirements-perception.txt
.\.perception-venv\Scripts\python.exe scripts/music_capture.py PROJECT
.\.perception-venv\Scripts\python.exe scripts/forced_alignment.py PROJECT --model-dir models/mms_alignment
.\.perception-venv\Scripts\python.exe scripts/local_perception.py PROJECT SOURCE --model models/Qwen2.5-Omni-3B
```

Qwen2.5-Omni-3B本轮固定模型revision为`f75b40e3da2003cdd6e1829b1f420ca70797c34e`。原图可重复传`--identity 名称=完整本地路径`；纯原曲观察传`--audio-only`。默认10秒区间、1秒重叠、视频4fps；完整音频范围与视频稀疏取样分别记录。局部运行可使用`--start`、`--end`，局部成功不得声称全片已看完。

MMS-FA使用官方torchaudio多语强制对齐模型，官方歌词经uroman后对齐；首次get_model会下载到指定目录。歌词正确不保证时间准确，分数低或与其他候选冲突必须保留待核。music_capture只允许首次捕获，已有音乐结构的修改须通过版本绑定采纳，不能覆盖语义设计。

这三种执行均保存真实输入、输出、原命令/退出码和指纹；原始模型摘要不作为逐帧唇形测量，模型自报confidence不当作校准准确率。审批仍走原正式流程，不自动放行生成、样片、全片或交付。
