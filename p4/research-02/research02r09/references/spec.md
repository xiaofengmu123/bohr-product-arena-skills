# 证据规范与检查清单

本文件是 `SKILL.md` §5–§7 的细则，Agent 在 Phase 1–4 写入产物时对照执行。

## 1. 筛选（先于写作）

纳入/排除标准必须含四要素，且在第一条纳排决定**之前**写进 `screening_log.json` 的 `criteria`：

- `research_question`：来自 `case.json` 的研究问题（无则写 `not_reported`）。
- `inclusion`：研究对象、文献类型、信息充足度等纳入条件。
- `exclusion`：低相关、方法不匹配、仅有摘要等排除条件。
- `dedup_rule`：默认 SHA-256 内容哈希去重；同哈希只保留排序路径最前一条。

每篇文献的 `decision` 取以下值之一，`reason` 必须可回溯：

- `include`：全文或摘要足以支撑至少一条比较字段。
- `exclude`：与问题无关或触发排除条件。
- `abstract_only`：仅有摘要，结论降为 `suggestive`/`not_reported`。
- `duplicate`：哈希或 DOI/题名与已纳入文献重复。

## 2. 证据行字段（evidence_table.jsonl，每行一个 JSON 对象）

| 字段 | 含义 | 要求 |
|-|-|-|
| `evidence_id` | 形如 `S#` 的稳定编号 | 与 `review.md` 中 `[S#]` 一一对应 |
| `source_id` | 指向 `result.json.sources` | 必须存在 |
| `locator` | 文件 + 页码/段落，如 `materials/x.pdf#p3` | 可定位到原文位置 |
| `quote` | 逐字原文摘录 | 不得改写、不得拼接 |
| `claim` | 该证据支撑的论断 | 一句，可被 quote 直接支持 |
| `strength` | `established/suggestive/disputed/not_reported` | 见 §3 |
| `field` | 比较字段：对象/方法/样本量/结果/结论/局限/未报告项 | 七选一或自定义 |
| `notes` | 冲突、来源冲突、仅摘要等备注 | 可选 |

## 3. 证据强度与措辞对照

| strength | 判定条件 | 正文措辞 |
|-|-|-|
| `established` | 多源一致且方法充分 | 表明 / 证实 |
| `suggestive` | 单源或方法有限或仅摘要 | 提示 / 初步表明 |
| `disputed` | 两源及以上结论相反 | 存在争议 |
| `not_reported` | 材料未报告该项 | 未见报告 |

**"未报告"不等于"不存在"**：写 `not_reported`，不得据此推断反面结论。

## 4. 冲突与空白

- 同一指标两源数值不一致：两条都记录，`notes` 标 `source_conflict`，不得静默择一。
- 结论相反：并列呈现，`strength=disputed`，正文标注"存在争议"。
- 研究空白必须落到**具体证据缺口**（哪个问题无任何文献覆盖），不得用"有待进一步研究"套话收尾。

## 5. 禁止事项

- 虚构文献、DOI、作者、数值、页码。
- 跨文档串值：把 A 文的数值填进 B 文的结论。
- 把相关性写成因果。
- 把摘要当全文证据，或在仅有摘要时补齐正文数据。
- 编造评测分数、运行结果或"预抽取答案层"。

## 6. 落盘纪律

1. Phase 0 先跑 `scripts/main.py`，此后任何失败都有合法 `result.json`。
2. 每 Phase 结束增量写盘，禁止最后一次性倾倒。
3. 只覆盖自己填过的字段；不删 `main.py` 已写好的键。
4. 缺数据/缺软件一律记入 `errors`/`degradations` 或 `pending_human`，如实降级。

## 7. 复核要点（对应评分维度）

- 任务完成度：筛选→主题→证据→草稿四步是否全跑完、五个产物是否齐全。
- 结果正确性：每条实质论断能否指回 `source_id + locator + quote`，引述强度与原文一致。
- 结果完整性：主要主题、方法路线、正反证据、结论分歧、研究空白是否覆盖。
- 结果可核验性：文献清单、筛选记录、证据表、引用定位能否独立复核。
- 调用易用性：`result.json` 是否含 `case_id` + 全部 contract 字段 + `status/sources/uncertainty/change_log`。
- 异常处理：缺全文、元数据不全、重复、冲突、检索为空、超上下文是否有处置。
- 运行稳定性：脚本只用标准库、重复运行结构一致、`selftest.py` 退出码 0。
