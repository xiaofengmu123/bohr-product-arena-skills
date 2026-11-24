# 证据表与写作规范

本文定义 `evidence_table.jsonl` 的行结构、证据强度分级、引用格式、降级规则与确定性写作约定。`scripts/selftest.py` 会对行结构做机器校验，语义一致性仍需人工核对。

## 1. 证据行字段（evidence_table.jsonl，每行一个 JSON 对象）

| 字段 | 类型 | 含义与要求 |
|---|---|---|
| `evidence_id` | string | 唯一行号，形如 `S1`、`S2`；`review.md` 中的 `[S#]` 与之一一对应 |
| `source_id` | string | 来源标识，须能解析回 `result.json.sources` 或 `screening_log.json` |
| `locator` | string | 定位：文件路径 + 页码/段落，如 `materials/paper1.pdf#p3` 或 `extracted/paper1.txt#para7` |
| `quote` | string | **逐字原文摘录**，不得改写、拼接或补写 |
| `claim` | string | 该证据所支撑的论断；措辞强度须与 `strength` 一致 |
| `strength` | string | 见 §2 四级 |
| `field` | string | 比较字段：研究对象 / 方法 / 样本量 / 主要结果 / 结论 / 局限 / 未报告项 |
| `notes` | string | 可选：抽样/偏倚/一致性说明 |

行必须按 `evidence_id` 升序写入；同一论断引用多行时用多个 `[S#]`。

## 2. 证据强度四级（strength）

| 值 | 判定 | 正文措辞 |
|---|---|---|
| `established` | 多源一致且方法充分 | 表明 / 证实 |
| `suggestive` | 单源或方法有限 | 提示 / 初步表明 |
| `disputed` | 存在结论相反或方法互斥 | 存在争议 |
| `not_reported` | 来源未报告该项 | 未见报告 |

**"未报告"不等于"不存在"**：不得把缺数据当作反证。仅有摘要而无全文的文献标 `abstract_only`（写入 `notes`），其结论不得高于 `suggestive`，未报告项填 `not_reported`。

## 3. 引用与回链

- 正文每句实质论断以 `[S#]` 结尾，指向 evidence_table 的唯一行。
- 文末 `sources` 表给出真实来源（作者、年份、标题、DOI/链接、定位），不得虚构。
- 回链顺序：论断 → `[S#]` → 证据行 → `locator` + `quote` → 原文片段。任一层断裂都视为可核验性缺陷。

## 4. 筛选记录（screening_log.json）

- `criteria` 必须写在 `decisions` 之前，含四要素：研究对象（population）、纳入条件（include）、排除条件（exclude）、去重规则（dedup_rule）。
- 每条 `decisions` 记录：`source_id`、`decision`（include/exclude/duplicate）、`reason`（可回溯）。
- 重复文献按 DOI / 题名 / 内容哈希判定，重复项写 `duplicate_of`，不静默删除。

## 5. 冲突与空白（conflicts_and_gaps.md）

- 冲突：同一指标两源不一致时**都记录**并标 `source_conflict`，并列呈现，不静默择一。
- 空白：只写"哪些具体问题没有任何文献覆盖"，落到证据缺口，不用套话收尾。
- 不越过证据范围扩展结论；区分材料事实、合理推断与无法确定项。

## 6. 降级规则（诚实标注）

| 情形 | 处置 |
|---|---|
| 仅有摘要/无全文 | `abstract_only` + strength ≤ `suggestive`，不补齐正文数据 |
| 元数据不全 | 写 `not_reported`，不猜测 |
| 同一指标两源不一致 | 都记录并标 `source_conflict` |
| 缺 `pdftotext` | 记入 `degradations`，用已抽取文本继续，不伪造解析结果 |
| 材料不足 | `status=insufficient_evidence`，`uncertainty`/`pending_human` 列缺口 |
| 缺字段/未知字段 | 保留键并填 `null`/`[]`，记入 `errors` |

## 7. 确定性写作约定

- sources 按 `source_id` 排序；evidence 行按 `evidence_id` 排序。
- `change_log` 用递增 `seq`；正文与 JSON 不嵌入墙钟时间戳，统一使用 `run_id`。
- 脚本只依赖 Python 标准库；产物经 `sort_keys` 规范序列化，仅当字节变化才重写。
- 验证命令：`python3 -B scripts/selftest.py --input /input --output /app/output --determinism`。

## 8. 禁止项

- 虚构文献、DOI、数值、样本量或实验运行结果。
- 跨文档串值（把 A 文数值填进 B 文）。
- 把相关写成因果；把摘要当全文证据。
- 编造评测分数、排名或白板对照结果。
