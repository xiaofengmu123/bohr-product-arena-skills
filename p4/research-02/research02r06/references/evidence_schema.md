# 证据与中间模型字段规范

本文档定义本 Skill 的结构化中间表示（`plan.json`、`claims.json`、`evidence_table.jsonl`）的字段语义，供 Agent 写入、`validate.py` 校验与他人复核使用。所有字段名按此约定，未知字段保留不删。

## 1. evidence_table.jsonl（每行一条证据）

一行一个 JSON 对象，不包裹数组。字段：

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `evidence_id` | string | 是 | 全局唯一，如 `E01`、`E02` |
| `source_id` | string | 是 | 指向 sources 中的来源，如 `S1` |
| `locator` | string | 是 | 文件 + 页码/段落，如 `materials/paper1.pdf#p3` |
| `quote` | string | 是 | 逐字原文，不得改写、不得拼接跨文档 |
| `claim_ids` | string[] | 是 | 被该证据支撑的 claim_id 列表 |
| `field` | string | 是 | 比较字段：`object` / `method` / `sample` / `result` / `conclusion` / `limitation` / `not_reported` |
| `strength` | string | 是 | `established` / `suggestive` / `disputed` / `not_reported` |
| `notes` | string | 否 | 上下文、抽象-only、疑似偏倚等说明 |

**strength 语义**：`established`=多源一致且方法充分；`suggestive`=单源或方法有限；`disputed`=存在冲突（两个来源都保留并互标）；`not_reported`=该来源未报告此字段。"未报告"≠"不存在"。

## 2. claims.json（论断-证据图，权威）

顶层 `{"claims": [...]}`，每条：

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `claim_id` | string | 是 | 全局唯一，如 `C1`、`C2` |
| `text` | string | 是 | 一句可被证据支持的实质论断 |
| `strength` | string | 是 | 同证据表四级 |
| `evidence_refs` | string[] | 是 | 指向 evidence_id，≥1 条 |
| `relation` | string | 否 | `support` / `refute` / `qualify` |
| `theme_id` | string | 否 | 归属主题，对应 plan.json 的 theme_model |
| `boundary` | string | 否 | 该论断的适用边界（对象、条件、指标） |

**规则**：`review.md` 中的 `[C#]` 必须能在 claims.json 找到；任何段落不得引入 claims.json 之外的实质论断。claim 强度决定草稿措辞，见 SKILL.md §6。

## 3. plan.json（筛选协议与主题模型，权威）

| 字段 | 语义 |
|---|---|
| `research_question` | 从 case.json 提取的研究问题 |
| `screening_protocol.inclusion_criteria` | 纳入条件（研究对象、研究类型、结局/时间窗） |
| `screening_protocol.exclusion_criteria` | 排除条件（非原始研究、无全文、不相关） |
| `screening_protocol.dedup_rules` | 去重规则（SHA-256 / DOI / 题名） |
| `screening_protocol.frozen` | 布尔；决定前必须 `true` |
| `theme_model.themes` | 主题列表，每项含 `theme_id`、`title`、`supporting_sources`、`boundary` |
| `theme_model.method` | 聚类依据，须写明是自下而上（对象/方法/结果/分歧） |
| `coverage` | `covered_sources` 与 `gaps`（无任何文献覆盖的具体证据缺口） |

## 4. locator 格式

- PDF：`<相对路径>#p<页码>`（页码按 `pdftotext` 产物的 `\f` 分页符计数，从 1 起）。
- 文本/题录：`<相对路径>#L<行号>` 或 `<相对路径>#<段落标题>`。
- 路径相对 `/input` 根，不写机器绝对路径，保证可移植复核。

## 5. sources 表（写入 result.json.sources）

每项至少含 `source_id`、`type`（`pdf`/`text`/`record`）、`path`、`fulltext_available`（布尔）、`sha256`（若有）；DOI、题名、作者、年份、期刊等在真实存在时才填，缺失写 `not_reported`。禁止虚构 DOI/数值。
