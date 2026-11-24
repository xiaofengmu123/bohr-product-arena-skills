# 复核清单与字段规范（references）

本文件供 Agent 与复核人使用，与 SKILL.md 第 5–7 节一致；冲突时以 SKILL.md 为准。

## 1. IR 片段（/app/work）与最终产物（/app/output）的对应

| 工作片段（编辑） | 最终产物（渲染，勿手改） | 作用 |
|---|---|---|
| `screening.json` | `screening_log.json` | 筛选标准在前、逐篇决定在后 |
| `evidence.jsonl` | `evidence_table.jsonl` | 证据行（回链最小单位） |
| `themes.json` | `themes.md` | 主题/子主题 + 支撑文献 + 边界 |
| `claims.json` | `review.md` | 每句一个 claim，渲染器追加 `[E#]` |
| `conflicts_gaps.json` | `conflicts_and_gaps.md` | 冲突并列 + 空白落证据缺口 |
| `meta.json` | `result.json`（诚实字段） | status / uncertainty / pending_human / change_log |
| `case.json` | `result.json`（contract 字段） | case_id + 全部 output_contract 字段 |

规则：**只编辑左侧片段，右侧由 `render` 单向生成**，保证正文与证据不脱节。

## 2. 字段规范

- `evidence_id`：`E01` 递增，全局唯一。
- `source_id`：`S01` 递增，与 `inventory.json` 的 `source_id` 一致。
- `locator`：`文件名#页码/段落` 或 `文件名#L行号`，如 `paper_a.pdf#p3`。
- `quote`：**逐字原文**，不做改写；不能从记忆补全。
- `strength`：`established | suggestive | disputed | not_reported`。
- `field`：`object | method | sample | result | conclusion | limitation | not_reported`。
- 筛选决定 `decision`：`include | exclude | abstract_only`。

## 3. 提交前逐条核对（对应评分维度）

- [ ] 四步跑完、产物齐全、结论与研究问题和证据范围一致（完成度）
- [ ] 每条实质论断 → `evidence_id` → `source_id` + `locator` + `quote`（正确性）
- [ ] 主题、方法路线、正反证据、分歧、空白均有覆盖（完整性）
- [ ] 文献清单、筛选记录、证据表、引用定位可独立复核（可核验性）
- [ ] `result.json` 含 `case_id` + 全部 contract 字段 + 诚实字段（易用性）
- [ ] 缺全文/元数据不全/重复/冲突/空检索/超上下文均有处置（异常）
- [ ] `check` 退出码 0；`selftest.py` 全部 PASS（稳定性）

## 4. 常见错误与处置

| 现象 | 处置 |
|---|---|
| `[E#]` 指向不存在的证据行 | 修正 `claims.json` 的 `evidence_ids`，或先补 `evidence.jsonl` |
| 同指标两源数值不一致 | 两条都保留，`notes` 标 `source_conflict`，草稿写 `disputed` |
| 仅有摘要无全文 | `fulltext_status` 标 `abstract_only`，strength 降至 `suggestive` |
| PDF 无文本层 | 记 `degradations`，改用 OCR 工具（运行时可用）后重抽 |
| 契约字段未知类型 | 保留键，缺值填 `null`/`[]`，不删键、不改名 |
| 检索为空 | `status=insufficient_evidence`，`pending_human` 列缺口 |
