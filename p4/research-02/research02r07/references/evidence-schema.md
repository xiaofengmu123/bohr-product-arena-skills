# 证据表与写作规范（复核用）

本文件是 `SKILL.md` §5 的展开，供 Agent 抽取证据与写草稿时逐条对照；也是他人复核结果时核对的字段口径。

## 1. 证据行字段（`evidence_table.jsonl`，每行一个 JSON 对象）

| 字段 | 必填 | 说明 |
|-|-|-|
| `evidence_id` | 是 | 全局唯一，形如 `S001-01`（`source_id` + 序号） |
| `source_id` | 是 | 指回 `inventory.json` 与 `sources` 表中的来源，如 `S001` |
| `locator` | 是 | 文件 + 页码/段落定位，如 `materials/a.pdf#p3` 或 `work/extracted/S001.txt#¶7` |
| `quote` | 是 | **逐字原文摘录**；不得改写、不得拼接不同段落 |
| `claim` | 是 | 该证据支撑的论断（一句话，可作为草稿句子候选） |
| `strength` | 是 | `established` / `suggestive` / `disputed` / `not_reported` |
| `field` | 是 | 比较字段：研究对象 / 方法 / 样本量 / 主要结果 / 结论 / 局限 / 未报告项 |
| `notes` | 否 | 摘录上下文、翻译歧义、为何判该 strength 等 |

## 2. 证据强度与措辞映射

| strength | 依据 | 草稿措辞 |
|-|-|-|
| `established` | 多源一致且方法充分 | 表明 / 证实 |
| `suggestive` | 单源或方法有限（含仅有摘要） | 提示 / 初步表明 |
| `disputed` | 存在相互冲突的结论或互斥方法 | 存在争议 |
| `not_reported` | 文献未报告该项 | 未见报告 |

硬性要求：**"未见报告"不等于"不存在"**；`abstract_only` 来源的结论一律降为 `suggestive` 或 `not_reported`。

## 3. 来源元数据（`result.json` 的 `sources`，每项）

至少含 `source_id`、`title`、`authors`、`year`、`doi`、`path`、`locator`（可定位范围）。缺失项写 `not_reported`，**不得编造** DOI/题名/页码。同一来源在 `sources`、`screening_log`、`evidence_table` 中用同一 `source_id` 对齐。

## 4. 筛选标准四要素（先于任何纳排决定写明）

1. 研究对象（population / phenomenon / setting）；
2. 纳入条件；
3. 排除条件；
4. 去重规则（本包默认按内容 SHA-256 去重，`inventory.json` 记录 `duplicate_of`）。

每篇文献的纳排决定写入 `screening_log.json` 的 `decisions`，决定值与理由可回溯。

## 5. 冲突与空白（`conflicts_and_gaps.md`）

- 冲突：两篇及以上文献结论相反或方法互斥，并列呈现并标 `disputed`，不抹平叙事。
- 空白：只落到**具体的证据缺口**（如"无任何文献报告长程随访的 X 指标"），禁止套话收尾。
- 无法确定项：来源信息不足、跨研究不可比、材料未覆盖等，写入 `uncertainty`/`pending_human`。

## 6. 绝对禁止

- 虚构文献 / DOI / 数值 / 页码；
- 跨文档串值（把 A 文数值写进 B 文的论断）；
- 把相关写成因果；
- 把摘要当全文证据；
- 编造评测分数、运行结果或缺失的软件状态。
