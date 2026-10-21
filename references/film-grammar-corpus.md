# 电影视听语法参考库

## 结论

可以用数百部高评价电影建立参考，但不能把盗版正片拉进仓库，也不能把“像某导演”当作镜头设计。参考库只保存匿名聚合数据：镜头时长分布、景别/运镜频率、相邻镜头转移矩阵和样本来源。基础语法仍由 `crew_pipeline.py` 的空间、轴线、动线、因果和节奏规则负责。

## 推荐公开来源

| 来源 | 可用信息 | 本项目用途 |
|---|---|---|
| [MovieNet](https://movienet.github.io/) / [movienet-tools](https://github.com/movienet/movienet-tools) | 1,100 部电影层级结构、镜头边界、场景、人物、景别与运镜标注；电影文件另受版权限制 | 主语料；优先下载 annotation/meta，不下载正片 |
| [MovieShots](https://movienet.github.io/projects/eccv20shot.html) | 7,000 个预告片中的约 46K 镜头，含景别与运动类型 | 校正 MV/预告片更快的节奏分布 |
| [HISTORIAN](https://github.com/dahe-cvl/historian_dataset) | 98 部历史影片，镜头边界、景别和运镜 | 检查年代/胶片叙事差异；按其条款使用 |
| [TransNetV2](https://github.com/soCzech/TransNetV2) | 自动镜头边界检测 | 分析用户合法持有或获准分析的视频 |
| [Shot2Story](https://github.com/bytedance/Shot2Story) | 多镜头视频、逐镜描述与整体摘要 | 研究镜头序列到段落叙事的映射，不作为电影摄影唯一基准 |

下载前必须阅读各数据集当前许可与用户协议。仓库只提交 manifest、转换代码和聚合画像；不提交电影、预告片帧、字幕或剧本原文。

## 输入协议

`scripts/film_grammar_corpus.py` 接受 JSONL，一行一镜：

```json
{"source_id":"movienet","film_id":"匿名ID","scene_id":"S10","shot_index":4,"duration_sec":2.13,"shot_size":"medium","camera_movement":"dolly","rating":8.1}
```

来源 manifest：

```json
{
  "schema_version": 1,
  "media_policy": "annotations_only",
  "sources": [{
    "id": "movienet",
    "title": "MovieNet annotations",
    "url": "https://movienet.github.io/",
    "license_or_terms": "填写下载时接受的真实协议版本",
    "access_scope": "shot/scene/cinematic-style annotations only"
  }]
}
```

任何来源缺少 URL、许可/条款或访问范围都会被拒绝。`media_policy` 不是 `annotations_only` 也会被拒绝。每条镜头的 `source_id` 必须存在于 manifest；重复镜头键、非法评分和非正时长都会阻断建库。

## 建库与审计

```bash
# 例：筛选 rating >= 7.5；200 部仅为数量阈值，不证明真实语料验收
python scripts/film_grammar_corpus.py build normalized_shots.jsonl source_manifest.json \
  references/corpus/high-score-drama.json --profile-id high-score-drama \
  --min-rating 7.5 --min-films 200

# 默认 advisory：只报告统计异常，不阻断
python scripts/film_grammar_corpus.py audit PROJECT references/corpus/high-score-drama.json

# enforce_envelope：必须提供独立签名验收，证书绑定画像、原始标注和逐镜语义适配
python scripts/film_grammar_corpus.py audit PROJECT references/corpus/high-score-drama.json \
  --mode enforce_envelope --certificate state/film_corpus_review.json
```

输出 `gates/film_grammar_reference.json`。建库始终标记 `exploratory_only` 和 `production_validation.status=unverified`。数量达标单独记录，不会升级生产状态；手填 `production_reference` 或 `verified` 也不能启用硬门禁。`enforce_envelope`要求用户信任目录中的独立验收签名、原始来源、影片版本去重及逐镜语义适配，缺证据继续失败关闭；`advisory`保留建议性统计。签名格式和信任设置见[八项补齐说明](gap-integration-guide.md)。来源条款字段完整只代表声明完整，不等于法律授权或真实标注质量已验证。

## 在 MV 中怎样用

1. 先按曲式、歌词信息量和表演需求设计段落，不以电影平均镜长替代音乐切点。
2. 再选择匹配题材与节奏的画像，例如高分剧情、悬疑或音乐类；不能混成一个“万能平均电影”。
3. 比较项目平均镜长、景别转移与运镜变化率，定位异常重复或拖沓区段。
4. 回到剧组部门修改：导演改信息释放，摄影改覆盖，演员调度改动线，剪辑改切点，美术/灯光改视觉事件。
5. 最后才叠加导演方法；画像数据、片名、导演名和具体镜头不得进入生成提示词。

## 不做什么

- 不自动抓取或保存数百部商业电影正片。
- 不按评分粗暴判断摄影质量；评分只是一种可选筛选字段。
- 不把高频转移当成唯一正确答案；罕见转移可能是有意表达，因此默认仅警告。
- 不从具体电影检索最近似镜头后复刻构图、表演或美术。
- 不用统计画像豁免 30 度规则、180 度轴线、场景地理和人物动线检查。
