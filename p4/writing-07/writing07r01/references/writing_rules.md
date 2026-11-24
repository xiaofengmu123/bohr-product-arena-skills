# 写作与证据溯源规则（详则）

本文是 SKILL.md 第 6 节的展开，Agent 起草与核对时逐条遵守。

## 1. 四段结构与承接

1. **研究背景**：从领域脉络、具体问题和技术约束入手，说明“为什么现在值得做”。禁止“近年来受到广泛关注/取得了快速发展”式空转；每一句背景句都指向一个具体未解决问题或技术约束。
2. **相关工作**：按问题设定 / 方法路线 / 数据与评价方式分组，组内同维度比较取舍，不按时间顺序罗列。转述他人工作时保留原文结论、适用条件与局限。
3. **研究空白**：只能由已引工作的共同限制、未覆盖场景或相互冲突结论推出；写明本文工作如何填补；无法由文献推出的判断标注“作者判断”。
4. **贡献**：每条贡献压缩为可核对条目，映射到正文的方法、图表或实验结果；写清适用范围，不夸大。

四段之间必须有逻辑承接：背景导出问题，相关工作导出缺口，缺口导出贡献。

## 2. 证据溯源（本版硬要求）

- 正文每个关键结论、数字、术语、引用后挂锚点 `[S#]`（`#` 为递增序号），一个事实句可挂多个锚点。
- `evidence_map.json` 的 `anchors` 里每个 `S#` 至少含：
  - `claim`：正文原句（或该锚点覆盖的分句）。
  - `claim_type`：`fact|paraphrase|citation|number|gap_inference|contribution|author_judgment`。
  - `locator`：材料定位，如 `materials/paper.md#p3`、`materials/paper.pdf#p2`。
  - `quote`：材料中**逐字**片段（不得改写、不得拼接）。
  - `provenance`：`{verbatim_ok, numbers_preserved, terms_preserved, strength}`。
    - `strength` ∈ `verbatim`（数字/术语逐字保留）、`paraphrase`（转述但保留原结论与限定）、`inferred`（由文献缺口推出）、`unverified`（待人工核对）。
- 数字、单位、统计量、限定词（如“显著”“在 X 条件下”）必须逐字复用；材料没有的不补，写 `MATERIAL GAP` 或 `not_reported`。
- 冲突信息不隐藏：同一指标两个来源不一致时，两个都记录，`uncertainty` 标 `source_conflict`。

## 3. 引用规则

- 引用只用材料中真实出现的来源；元数据（作者/年份/DOI）缺失写 `pending_human`，不臆造。
- `citation_map`：引用键 → `{authors, year, locator, quote, anchors, verified}`；`anchors` 列出引用该来源的正文锚点，`verified` 表示元数据已从材料核实。
- 目标期刊格式未知时用作者—年份，并在 `uncertainty` 声明假设；不冒充特定格式。
- 引用落在具体论断上，禁止“一堆文献挂在段落末尾”的空引用。

## 4. 分组与空白推导可复核

- `group_rationale`：每组记录 `{members, dimension, basis}`，`dimension` ∈ 问题设定/方法路线/数据与评价方式，`basis` 说明分组依据，供独立复核。
- `gap_derivation`：`G#` → `{gap, evidence_anchors, derivation_type, filled_by}`，`derivation_type` ∈ `common_limitation|uncovered_scenario|conflicting_result`。
- `contribution_map`：`C#` → `{contribution, evidence_anchors, scope_limitation}`，每条贡献指向正文证据锚点。

## 5. 语言切换

中英互译时：术语、数字、引用键、单位原位保留，仅换表述语；限定强度不变（“表明/证实/初步表明/有望”按证据强度取对应词，不得升格或降格）。

## 6. 核对清单（写作后、回填前执行）

1. 逐条比对 `quote` 与正文 `claim`，确保数字/术语/限定词一致（`verbatim_ok`）。
2. 逐条核对引用元数据（作者/年份/来源）与材料一致，未核实的置 `unverified` 并进 `pending_human`。
3. 空白推导回溯到 `evidence_anchors`，确认是“共同限制/未覆盖/冲突”而非空泛断言。
4. 贡献条目回溯到正文方法/图表/结果，删除无依据或夸大的贡献。
5. 把最终结果写入 `result.json` 合同字段，并同步 `sources`/`uncertainty`/`change_log`。
