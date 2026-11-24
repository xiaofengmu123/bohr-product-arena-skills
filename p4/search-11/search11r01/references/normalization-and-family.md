# 号码规范化与同族归并规则

本文件是可被他人复用的字段口径说明，与 `scripts/main.py normalize|extract|family` 的行为一一对应。所有规则确定性执行：同一输入重复运行，分析字段结果一致。

## 1. 号码类型（number_type）

| number_type | 含义 | 常见形式 |
|-|-|-|
| `publication` | 公开号（含 A 类公告/审查公开） | `WO 2020/123456 A1`、`US 2019/0123456 A1`、`CN 109 123 456 A` |
| `grant` | 授权公告号（B/C 类） | `US 12,345,678 B2`、`EP 3 456 789 B1` |
| `application` | 申请号（序列号） | `US 16/123,456`、`PCT/US2019/012345` |
| `provisional_priority` | 临时申请/临时优先权（62/） | `US 62/123,456` |
| `priority` | 优先权号（显式标签） | 标注“优先权号/priority”的号码 |
| `unknown` | 无法判定 | 缺少国家码或数字 |

判定优先级：显式标签 > kind code > 斜杠/62/ > 裸数字（低置信）。
每个结果同时给出 `type_candidates[]`（每条含 type/reason/confidence），保留多种解释，供人工复核，不静默择一。

## 2. kind code 参考（WIPO ST.16 常用子集）

| kind code | 通常含义 | number_type |
|-|-|-|
| `A1`/`A2`/`A3`/`A4`/`A8`/`A9` | 公开/审查公开 | publication |
| `A` | 公开（旧格式或未分级） | publication |
| `B1`/`B2`/`B3`/`B4`/`B8`/`B9` | 授权公告 | grant |
| `B`/`C` | 授权公告（旧格式） | grant |
| `U`/`Y`/`T`/`S` | 实用新型/检索报告等 | publication（标注需复核） |

不在上表内的尾部字母不当作 kind code（避免把 “filed” 等英文单词误判为 kind）。

## 3. 规范化（normalized）

- 大写、去分隔符（空格、逗号、句点、斜杠、连字符），保留字母与数字。
- `number_body` = 国家码 + 数字主体（不含 kind code）；`kind_code` 单独存放。
- 规范化不改写原号码：`raw` 始终保留输入原文，便于回官方库复核。

### 示例与边界

| 输入 | normalized | kind_code | number_type |
|-|-|-|-|
| `US 12,345,678 B2` | `US12345678B2` | `B2` | grant |
| `US 2019/0123456 A1` | `US20190123456A1` | `A1` | publication |
| `US 16/123,456` | `US16123456` | — | application |
| `US 62/123,456` | `US62123456` | — | provisional_priority |
| `WO 2020/123456 A1` | `WO2020123456A1` | `A1` | publication |
| `WO 2020/123456A1`（OCR 粘连） | `WO2020123456A1` | `A1` | publication |
| `EP 3 456 789 A1` | `EP3456789A1` | `A1` | publication |
| `CN 109 123 456 A` | `CN109123456A` | `A` | publication |
| `PCT/US2019/012345` | `PCTUS2019012345` | — | application |
| `公开号: CN 109,123,456 A` | `CN109123456A` | `A` | publication |

`confidence` 口径：有国家码+kind code=high；仅国家码=medium；其余=low。

## 4. 号码抽取（extract）

- 从自由文本/OCR 文本用确定性正则匹配「国家码 + 数字主体（可选两字母受理局/斜杠）+ 可选 kind code」。
- 每条候选输出 `raw`、`normalized`、`locator`（`input_text#start-end` 字符区间）、`context`（±40 字符原文）、`confidence`。
- 抽取只是候选：**逐条回官方库核对**后才写入 `patent_records`，不得把候选当定论；OCR 粘连处记 `ocr_uncertain`。

## 5. 同族归并（family）

- 归并键：规范化后的 `application_number` 与 `priority_number`（共用一个即视为同一族）。
- 实现：并查集（union-find），对每条记录取 `application_number`/`priority_number`（含 `application_no`/`priority_no`/`application`/`priority` 别名）规范化后建邻接，连通分量即一族。遍历顺序固定，族号 `F1..Fn` 稳定。
- 输出 `family_type: observed`，`exhaustive: false`，并注明 `basis_note`：观测到的共用申请/优先权链接，**不是**权威 INPADOC 简单族。
- `linkage_basis` 记录是用哪个字段（application/priority）连成的，供他人复核归并依据。
- 简单族（同一发明，成员同优先权）与扩展族（经优先权链关联）需人工/官方库确认后才能升级口径；本 Skill 默认只给 `observed`。
- 冲突或口径不明时，写 `uncertainty`（`family_scope_unclear`），不强行判定。

## 6. 法律状态核验

- 状态字段固定为：`status`、`effective_date`、`source`（官方登记页）、`as_of`（查询日期）、`staleness_risk`、`confidence`。
- 官方状态未知时写 `status: unknown`，不推断“有效”。
- 转载/聚合页不作为登记依据；双方冲突时并列记录并标 `source_conflict`，写 `staleness_risk` 与 `pending_human`。

## 7. 证据溯源（本变体核心）

每条关键结论都必须在 `conclusions[]` 中声明，并通过 `evidence_ids` 指回 `evidence_map` 里的逐字证据。`evidence_map` 键为 `evidence_id`，值为：

```json
{
  "conclusion_id": "C1",
  "claim_brief": "公开号规范化后为 US12345678B2",
  "locator": "materials/uspto_scan_1.pdf#p2",
  "locator_type": "pdf_page",
  "quote": "United States Patent 12,345,678 B2",
  "quote_sha256": "<对压缩空白后的 quote 计算的 sha256>",
  "source": {"engine": "uspto-scan", "url": null, "retrieved_at": "2026-11-24T00:00:00Z"},
  "verification_status": "verified_verbatim",
  "as_of": "2026-11-24",
  "note": null
}
```

`verification_status` 取值与含义：

| 值 | 含义 |
|-|-|
| `verified_verbatim` | quote 与本次实际读取的源逐字一致 |
| `source_located` | 已定位来源，quote 为可得的最佳文本 |
| `ocr_uncertain` | 来自 OCR，边界可能粘连 |
| `paraphrase` | 转述而非逐字引用，证据较弱，需补 quote |
| `unverified` | 尚未读取源（缺省值，finalize 自动补齐） |

`quote_sha256` 由 `scripts/main.py evidence|finalize` 自动计算（对压缩空白后的 quote 做 sha256），用于防篡改与跨次运行比对。`evidence[]` 与 `evidence_map` 两种视图由 reconcile 自动互建，二者内容一致。
