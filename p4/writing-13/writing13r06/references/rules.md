# 规则与规范（rules）

本文件是 `SKILL.md` 的细则：七要素写法判据、四问映射、事实-来源映射格式、对齐规则、禁止修饰语、plan.json schema 与冲突处理。

## 1. 七要素写法判据（写作时逐条满足）

| 要素 | 可观察判据（写不到位 = 判据不满足） |
|---|---|
| 立项依据 | 含"领域现状 → 关键瓶颈 → 申请人前期观察"三节；明确"别人做到哪一步、还差什么、为什么是我们"；背景论断逐条有来源；无政策口号堆叠 |
| 科学问题 | 一句话可回答、可分解、有边界；写明"研究的变量/对象 + 待验证关系"；问题大小与方法错位时拆分 |
| 研究目标 | 数量适度、层次清楚、可考核；每个目标可对应到内容与指标 |
| 研究内容 | 与研究目标一一对应；每条内容写清"做什么、产出什么" |
| 技术路线 | 按研究内容组织步骤；每步写清输入输出、关键节点、可行性依据、风险与备选方案；不罗列方法名 |
| 创新点 | 每条写"与哪项具体已有工作的差别 + 为什么难 + 凭什么做得成"；不用无法核验的修饰 |
| 研究基础与可行性 | 组成"能力—证据—与本研究的关系"证据链；前期成果可定位到材料；平台/团队分工写清 |

## 2. 四问 → 判据与回检动作

- **立得住**：读立项依据草稿，逐句问"这句话是必要性论证还是口号"。口号句改写为"瓶颈 + 缺口 + 我们的切入"。
- **说得准**：对每条创新点问"差别对象是谁、难在哪、凭什么成"。出现 HARD_BAN 修饰语 → 删除或替换为具体比较对象。
- **对得上**：目标、内容、路线、指标四列逐行对齐。行数不一致或某行缺项 → 视为对齐空洞，必须在 `alignment_matrix` 修复。
- **查得到**：数字/术语/引用/前期成果逐条给出 `source_ref`（file+loc+quote）。无法核实 → `unmapped` + `pending_human`，不补造。

## 3. 事实-来源映射格式

每条事实在 `evidence_ledger.json` 与 `fact_source_map` 中的格式：

```json
{
  "claim": "断言的完整表述",
  "kind": "number|term|citation|figure|claim|policy",
  "source_ref": {"file": "材料相对路径", "loc": "页/段/表/图", "quote": "材料逐字片段"},
  "status": "mapped|unmapped|conflict",
  "note": "unmapped 时说明原因；conflict 时记录双方依据"
}
```

规则：
- `quote` 必须是材料**逐字**片段，不得转述；无法取到逐字片段时 `quote` 置空并标 `unmapped`。
- `status=unmapped` 必须有 `note` 说明"缺在哪、为何无法核实"。
- 同一指标两个来源不一致 → 两条都记录，`status=conflict`，不擅自选一。

## 4. 目标—内容—路线—指标对齐

`plan.json` 的 `alignment_matrix` 每行一条目标：

```json
{"objective": "目标 O1", "content": "内容 C1", "route_steps": ["步骤1", "步骤2"],
 "indicator": "考核指标 K1", "evidence_ref": "file:loc"}
```

约束：目标数 = 内容数 = 路线条数（按目标计）= 指标数；任何一行四字段缺一即为空洞，`verify` 会标红。

## 5. 禁止修饰语清单

- HARD_BAN（无法核验，出现即须删除或改为具体比较对象）：填补空白、国际首创、世界首创、世界第一、国内外首创、全球领先、国际空白、国内空白。
- REVIEW_FLAG（须给可核验比较对象，否则标 pending_human）：首次、国际领先、国内领先、突破性、颠覆性、遥遥领先、唯一、首创、率先、引领。

处置原则：创新点表述优先用"与 [具体已有工作/文献] 相比，本工作在 [具体维度] 上 [具体差别]"；不用"首次/领先/填补空白"作结论词，除非紧随其后给出可核验比较对象。

## 6. 已完成 vs 拟开展

- `done_vs_planned.done`：材料中明确为申请人**已完成**的前期成果/数据/平台（写"已完成"只能引用材料原文）。
- `done_vs_planned.planned`：本研究**拟开展**的内容（用"拟""将""计划"表述）。
- 材料缺失真实申请人证据时，明确写入 `material_gaps` 与 `pending_human`，不得把外部作者成果写成申请人已完成。

## 7. plan.json schema

```json
{
  "schema_version": "1.0.0",
  "case_id": "...",
  "task_type": "fund_proposal_section",
  "section_target": "写哪一节或 full",
  "genre": "章节稿/立项依据/研究方案", "audience": "...", "format": "md/tex/docx",
  "word_budget": "...", "citation_style": "...", "language": "...",
  "seven_elements": {"<七要素>": {"status": "draft|pending|skipped", "claims": ["论断+source_ref"]}},
  "alignment_matrix": [{"objective":"", "content":"", "route_steps":[], "indicator":"", "evidence_ref":""}],
  "done_vs_planned": {"done": [], "planned": []},
  "novelty_claims": [{"claim":"", "diff_against":"", "difficulty":"", "feasibility":"", "evidence_ref":""}],
  "material_gaps": [], "manual_confirm": [], "anomalies": []
}
```

正文必须从该结构渲染；渲染后不得另写一套说法。

## 8. 冲突与不编造红线

- 冲突：记录双方依据与判据，不擅自选一、不擅自改分。
- 不编造：不虚构数字、术语、引用、DOI、卷期页码、图表内容、运行结果；缺就写 `not_reported` 或进 `pending_human`。
- 不把文件名/领域标签当事实；不把未读部分声称已核对。
