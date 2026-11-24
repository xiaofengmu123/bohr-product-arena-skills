# 归一化与追踪规则（normalization.md）

本文件定义 `scripts/main.py dedup` 与 `finalize` 所用的确定性规则。规则是纯函数：相同输入必得相同输出，便于任意一次增量复算。Agent 的实质判断写入 `findings.json`，脚本只做可复现的合并与记账。

## 1. 去重键（canonical key）

优先级：**DOI > arXiv 编号 > 归一化标题哈希**。命中更高优先级即停止。

| 键类型 | 归一化 | 说明 |
|-|-|-|
| `doi` | 去掉 `https://doi.org/`、`doi:` 前缀；去掉首尾句读符；转小写 | 最高优先，忽略大小写与 URL 形态 |
| `arxiv` | 去掉 `arxiv:` 与 `/abs/` `/pdf/` 前缀；剥离 `vN` 版本后缀；转小写 | 新旧式编号都识别（`2301.12345`、`hep-th/9901001`） |
| `title` | `NFKC` 归一 → 小写 → 去 HTML 标签 → 非字母/数字/CJK 字符折叠为单空格 → 去首尾空白 | 取 SHA-1 前 16 位，标题级 fallback |

canonical_id 形式为 `doi:10.1000/xyz`、`arxiv:2301.12345`、`title:sha1:<16hex>`。完全无法识别的记录归入 `unkeyed:<序号>`，保留待人工复核，不自动合并、不推送提醒。

## 2. 版本链（version chain）

`version_marker(record)` 返回可比较三元组 `(阶段秩, 日期, 版本串)`，阶段秩升序代表推进：

```
unknown(-1) < preprint(0) < published(1) < correction(2) < retraction(3)
```

同一 canonical_id 下：

- 只存在 preprint → 判为预印本；出现 published → 视为"预印本→正式版"推进。
- correction / erratum / corrigendum → 版本更新（更正）。
- retraction / withdrawal → 撤稿，单独标记，不与新增混排。

代表性条目取 `version_marker` 最大（最进阶）者；其余成员记为 `suppressed`（重复/转载合并）。

## 3. 时间窗

- 窗口起止用 UTC ISO-8601（`YYYY-MM-DDTHH:MM:SSZ`）；无时区日期按 UTC 归一并记 `uncertainty`。
- 闭区间连续无缝：下一窗口起点 = 上一窗口终点；跨窗边界条目只归入一侧，避免双计。
- 延迟入库条目（publication date 早于窗口、但本次才被索引）按"刚被索引的旧文献"处理：单独标记 `late_ingested`，**不作为真新增推送**，仅在候选集中保留。

## 4. 快照与增量判定

`snapshot` 为 `{canonical_id: latest_version_marker}` 字典，存上一次窗口的状态水位。

对当前分组做差：

| 情况 | 判定 |
|-|-|
| canonical_id 不在快照 | `new` |
| canonical_id 在快照但 marker 不同 | `updated`（版本推进/更正/撤稿） |
| canonical_id 在快照且 marker 相同 | `unchanged`，不推送 |
| 快照有、当前无 | `missing_in_current`，仅记录不删除 |

`build_snapshot(groups)` 由当前分组反推快照，供下一窗口复用。

## 5. 提醒抑制

只推送三类提醒：`new`（真新增）、`version_update`（预印本→正式版等推进）、`correction`/`retraction`（更正/撤稿）。以下一律抑制并记 `suppressed`：

1. 同一 canonical_id 的多源重复、转载（去重合并）；
2. `unkeyed` 无法识别身份的条目；
3. `late_ingested` 刚被索引的旧文献；
4. `unchanged` 与快照一致的条目。

抑制台账写入 `tracking.suppressed`，每条含 `canonical_id` 与 `reason`，供回放核对。

## 6. 幂等与复算

- 所有序列化使用 `sort_keys=True` 的稳定 JSON；集合转列表前先排序。
- 同输入 + 同 `findings.json` 两次 `finalize` 产生字节一致的 `result.json`（`selftest.py` 用例 5 验证）。
- 失败重试只重放确定性的记账步骤，不重复推送：提醒基于快照差，重跑不改变已判定的 new/updated。

## 7. 来源边界

判定"新增/更新/撤稿"只依据 `/input` 下的实际文件（Crossref/EuropePMC/出版社响应、引用图、增量导入状态与原文）。不宣称实时联网覆盖或"全球最新"，不伪造全文/引文/状态。超出材料可见范围的结论写入 `uncertainty`。
