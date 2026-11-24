# 引用管理规则与中间表示（IR）规范

本 Skill 的脚本不做语义判断，而是把稿件与文献库解析成**结构化中间表示（IR）**，再由 Agent 在 IR 上做语义决策，最后把 IR **确定性地渲染**成可编辑产物。正文标记与文末列表都是同一 IR 的投影，因此不可能漂移。本文件是语义阶段的权威规则。

## 1. IR 文件与职责

| 文件 | 位置 | 谁写 | 职责 |
|---|---|---|---|
| `plan.json` | `/app/work/plan.json` | 脚本 | 材料清单、目标样式、预算、阶段检查点 |
| `citation_model.json` | `/app/work/` 与 `/app/output/` | 脚本 | 单一事实源：claims / references / citations / evidence / numbering / consistency |
| `agent_edits.json` | `/app/work/agent_edits.json` | Agent | 语义决策覆盖层：引用匹配、去重确认/拆分、来源核验、冲突与待确认 |

脚本每次运行都会：解析材料 → 重建机械事实 → 叠加 `agent_edits.json` → 把指向被合并条目的引用解析到 canonical → 重算编号与一致性 → 重新渲染。因此 Agent 只需编辑小而聚焦的 `agent_edits.json`，其余全部由脚本幂等重算。**不要直接编辑 `citation_model.json` 或渲染产物。**

## 2. `citation_model.json` 关键结构

- `claims[]`：每个论断句一条。含 `id`（`c001` 起）、`start/end`（原稿字符偏移，支持原位替换）、`kind`（`prose|list|caption`，heading 不参与引用）、`text`（去标记文本）、`original`（原文）、`markers[]`（抽取到的引用标记）、`needs_citation`（启发式）、`decision`（`pending|matched|not_needed|insufficient|conflict`）、`ref_ids[]`。
- `references[]`：文献库条目，`id`（`r001` 起）、`source`、`locator`、`key`、`fields`（`title/authors/year/doi/journal/volume/issue/pages/...`）、`status`（`canonical|duplicate`）、`canonical_of[]`、`duplicate_of`、`merge_evidence`。
- `citations[]`：`claim_id -> ref_ids[]` 的论断级匹配，含 `match_basis`、`confidence`、`position`（默认 `sentence_end`）、`style`。
- `evidence[]`：逐条证据，含 `claim_id`、`ref_id`、`locator`、`quote`（逐字）、`match_basis`、`status`。
- `numbering`：`order[]` + `id_to_number{}`，算法为“首次引用顺序编号”。
- `merge_proposals[]`：去重提案（含 `criterion`、`confidence`、`member_indexes`、`evidence`）。
- `consistency`：一致性检查结果（见 §6）；`conflicts[]`、`pending[]` 绝不隐藏。

## 3. `agent_edits.json` 契约（Agent 唯一需要写的文件）

```json
{
  "schema_version": "1.0",
  "semantic_verified": false,
  "claims": {"c001": {"decision": "matched", "ref_ids": ["r001"], "note": ""}},
  "citations": [
    {"claim_id": "c001", "ref_ids": ["r001"],
     "match_basis": "所引文献即该方法来源", "confidence": "high",
     "position": "sentence_end", "style": "gbt7714"}
  ],
  "evidence": [
    {"claim_id": "c001", "ref_id": "r001",
     "locator": "library.bib:entry@smith2020", "quote": "逐字摘录原文",
     "match_basis": "主题与结论一致", "status": "verified"}
  ],
  "merge_overrides": {"merge": [["r001", "r005"]], "split": [["r002"]]},
  "reference_notes": {"r001": "DOI 已核验"},
  "conflicts": [{"type": "source_conflict", "detail": "两来源结论相反",
                  "locators": ["a.bib:entry@x", "b.bib:entry@y"]}],
  "pending": [{"item": "r009 DOI 无法解析", "reason": "需人工核验"}],
  "notes": "本轮语义判断摘要"
}
```

- `claims`：只写需要改变的 claim；`decision` 取值 `matched | not_needed | insufficient | conflict | pending`。
- `merge_overrides.merge`：把成员合并到首项（首项为 canonical），用于确认候选合并或补充遗漏。
- `merge_overrides.split`：撤销误合并（同名不同文献必须 split）。
- `evidence.status` 取值：`verified | not_verified | conflict | insufficient`。
- 只有全部关键引用完成核验、`semantic_verified=true` 且一致性 `clean` 时，脚本才把 `result.json.status` 置为 `ok`。

## 4. 去重判据（可解释，两级置信）

优先级从高到低，全部写进 `merge_evidence`：

| 判据 | 置信 | 处置 |
|---|---|---|
| DOI 归一化相等（大小写不敏感，去 `https://doi.org/` 与 `doi:` 前缀） | confirmed | 脚本自动合并 |
| arXiv id 相等（规范化 `arxiv:` / URL 前缀） | confirmed | 脚本自动合并 |
| 题名 NFKC 归一化 + 第一作者姓 + 年份 三者相同（无 DOI/arXiv） | candidate | 只提案，**须 Agent 确认**后才合并 |

- 中英文题名、预印本与正式版、不同数据库导出的同一条：优先以 DOI/arXiv 对齐；无标识符时靠题名+作者+年份候选，并人工核验。
- **严禁误合并同名作者的不同文献**：仅当 candidate 且证据充分时才在 `merge_overrides.merge` 确认；把握不足写 `pending` 并标 `not_verified`。
- 引用若指向被合并条目，脚本会自动解析到 canonical（`resolve_duplicate_citations`），避免正文编号悬空。

## 5. 编号与渲染规则

- **顺序编码制**：按 claims 文档顺序，首次出现的 `ref_id` 依次编号；同一 ref 只编一次，多处引用复用同一编号。
- **范围压缩**：连续 ≥3 个用 `[1-3]`；恰好 2 个用 `[1,2]`；不连续用 `[1,3]`。
- **原位替换**：claim 有 `start/end` 偏移，脚本在原稿上原位替换；标题/列表/表格/代码块等非 prose 内容原样保留，只有被语义决策命中的 claim 才重写标记。
- **一句多引**：一个 claim 的多个 `ref_ids` 渲染为 `[n-m]` 或 `[n,m]`；**多句一引**复用同一编号。
- **样式**：`gbt7714`（顺序编码制，默认）与 `apa`（著者—出版年制）。GB/T 7714—2015 文献类型标识：`[J]` 期刊、`[M]` 专著、`[C]` 会议、`[D]` 学位论文、`[R]` 报告、`[Z]` 未出版、`[EB/OL]` 电子资源。映射尽力而为，缺字段时保留已有字段而不补造。
- **未引用条目**：不编 `[?]`，列在文末“未引用条目（保留，未编号）”小节，避免与正式编号混淆。

## 6. 一致性检查（脚本自动执行，写入 `consistency_report.json`）

- `unresolved_citations`：引用指向不存在的 `ref_id`。
- `uncited_references`：文献库中未被引用的条目（正常，但必须列出）。
- `pending_claims`：`decision == "pending"` 的论断。
- `missing_citation_claims`：需要引用却无引用的论断（`decision != "not_needed"`）。
- `numbering_gaps`：编号不连续。
- `duplicate_still_cited`：被标记 duplicate 却仍被引用（解析失败时的兜底提醒）。
- `field_missing`：条目缺 `title` 或 `year`。
- `unconfirmed_merges`：待 Agent 确认的候选合并。

## 7. 证据溯源与来源核验

- 每个关键结论/数值/引用都要能指回材料的**逐字片段**：`locator`（`manuscript.md:L12` 或 `library.bib:entry@smith2020`）+ `quote`（原文逐字）。
- 逐句判定“正文论断 ↔ 所引文献主题/结论”是否真匹配：`verified`（匹配）、`insufficient`（证据不足）、`conflict`（冲突，两来源不一致时都记录并标 `source_conflict`）、`not_verified`（无法核验）。
- DOI 解析失败、条目歧义、疑似虚构条目 → 标 `not_verified`/`pending_human`，**不得**声称已核验。

## 8. 诚实底线

- 缺失/冲突/无法判断分别写 `not_reported`、`source_conflict`、`pending_human`，不编造 DOI、元数据、检索结果或实验数据。
- 材料不足时输出“材料不足”（`insufficient_evidence`），不用示例填充。
