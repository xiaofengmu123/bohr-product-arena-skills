---
name: results-discussion-writing
description: >
  结果与讨论章节写作 Skill。把给定的科研材料（数据表、图、图注、统计输出、参考文献）
  写成可投稿的结果段与讨论段，并用三层中间表示（plan.json / figure_model.json /
  evidence_map.json）保证图表读数、趋势与差异、讨论证据、因果边界与局限可逐条复核；
  区分“实测/推断/文献引用”与“已完成/拟开展”，在 /app/output 交付可复核 result.json。
  Use for writing/revising Results & Discussion sections grounded in provided
  figures, tables, statistics and references, with verifiable intermediate
  representations and honest source mapping.
version: 1.0.0
---

# 角色与何时使用

本 Skill 把散乱科研材料转成“可投稿的结果段 + 讨论段 + 证据映射”，而非写作建议或
提纲。满足任一即调用：

- 任务含“结果与讨论 / Results / Discussion / 图表读数 / 趋势与差异 / 因果边界 /
  局限性 / 章节写作”等；
- 要求依据给定材料产出可编辑章节稿、改稿、引用/格式或结构化产物；
- 要求保留来源、区分已完成/拟开展、交付 result.json。

不适用：纯文献泛读、测试题生成、非写作类数据分析。只依据实际读到的内容写作，
不依据文件名/领域标签补造数据。

# 输入契约（运行时，Linux 固定路径）

- `/input/case.json`：case_id、任务说明、`output_contract`（result.json 必需字段）、
  材料清单、受众/文体/格式约束。
- `/input/task.md`：题目与评分要求，属事实底稿一部分。
- `/input/` 其余文件：材料原文（PDF/文本/CSV/表格等）。PDF 用平台
  `pdf-text-tool`/`ocr-tool` 解析后回填，脚本不读二进制；脚本自动读文本/CSV。
- 工作目录 `/app/work/`（中间产物）；最终产物 `/app/output/`（含 result.json）。

先读文件再写。第一步运行（相对脚本路径，从 SKILL.md 所在目录执行）：

`python scripts/main.py --case /input/case.json --task /input/task.md --input-dir /input --work /app/work --output /app/output`

产出 `/app/work/input_summary.json`、`plan.json`、`figure_model.json`、
`evidence_map.json` 与 `/app/output/result.json`（partial 骨架）。

# 输出契约（必须满足）

`/app/output/result.json` 必须包含：

1. `case_id`：与 /input/case.json 一致；
2. `output_contract` 声明的**全部字段**，字段名原样保留、不增删改名；若未声明，
   至少含 `deliverable`（可编辑章节稿内容与文件路径）、`facts_sources`（数字/
   术语/引用/图表/关键论断逐条来源映射）、`version_boundaries`（修改差异、人工
   确认项、材料缺口、异常）；
3. 附加字段：`status`（`partial|complete|insufficient_evidence|failed`）、
   `sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、
   `degradations`、`runtime`、`intermediates`。

章节稿正文写入 `/app/output/`（`manuscript.md` 或 output_contract 指定文件名），
result.json 以路径引用。可编辑 Office/幻灯片/图示须附真实渲染的 PDF/PNG 预览。

# 执行流程（总预算 900s / 64 次模型请求 / 16384 token 每响应）

时间纪律：**前 3 分钟内必须落盘一版 partial 骨架 result.json**，此后只做增量
改进；每次改动立即 `merge_patch` 写回，不得只在会话里“记住”。保留约 20% 请求与
时间余量用于异常重试；超时即冻结现状，诚实标注 partial/failed。

1. **读取与归一化（≤60s，1–2 次请求）**：运行
   `python scripts/main.py --phase bootstrap ...`（discover → skeleton →
   scaffold）。通读 case.json/task.md/材料，记下受众、文体、格式、output_contract
   字段。输入→输出：原始输入 → input_summary.json + 三个中间骨架 + partial
   result.json。
2. **落骨架（≤120s，1 次请求）**：bootstrap 已生成含 case_id + 全部
   output_contract 字段（null 占位）+ 保留字段的 result.json。**此步完成即达成
   “可提交的最小诚实交付”。**
3. **要素规划与映射（≤240s，8–12 次请求）**：按四问规则（读得准/分得清/说得住/
   核得了，见 `references/four-questions-and-rules.md`）回填 plan.json 的段落与
   claim 槽位、figure_model.json 的图面板与统计量、evidence_map.json 的 claim→
   证据。输入→输出：三个中间骨架 → 填充后的中间产物。
4. **成稿（≤240s，10–16 次请求）**：写 manuscript.md 到 /app/output，正文每段
   挂 claim id；回填 deliverable/facts_sources/version_boundaries，`merge_patch`
   写回 result.json。每段可回溯到材料位置（文件+页码/行）。
5. **自检与回修（≤180s，6–10 次请求）**：运行
   `python scripts/main.py --phase validate ...` 与
   `python scripts/selftest.py`；按自检清单逐项修复，把结论写回 change_log。
6. **收尾（剩余时间）**：补渲染预览、写 uncertainty/pending_human、置
   status=complete 或 partial。

# 自检清单（逐条勾选，括号内为对应评分维度）

- [ ] 任务完成度【实际效果40】：交付可编辑、可投稿的结果段与讨论段，而非建议/提纲。
- [ ] 结果正确性【实际效果40】：每个数字/单位/误差棒含义/统计量/引用都能在材料
  找到原文位置，无材料外新增事实。
- [ ] 结果完整性【实际效果40】：图表、阴性结果、不一致文献与局限如实交代，厚度匹配任务。
- [ ] 结果可核验性【实际效果40】：正文→claim→证据三级对应，第三方能独立判断某句该不该写。
- [ ] 一致性·对得上【使用增益25】：figure_model 数值 ↔ 正文数值 ↔ facts_sources 逐条对应。
- [ ] 效率【使用增益25】：脚本先归一化/搭骨架/自动回检，减少起草-核对-返工往返。
- [ ] 来源可溯·查得到【使用增益25】：facts_sources 每项带 locator + 逐字 quote，
  区分“实测/推断/文献引用”与“已完成/拟开展”。
- [ ] 方法改进【创新增益15】：中间表示→成稿→一致性回检→禁词→来源回检由脚本强制执行。
- [ ] 无空话修饰·说得准【创新增益15】：全文无“首次/国际领先/填补空白/颠覆性”等无据措辞。
- [ ] 调用稳定【可靠体验10】：换图/换学科/换数据量、重复运行仍产出口径一致产物。
- [ ] 异常已处理【可靠体验10】：材料缺失/冲突/超长/引用无法核实/格式冲突已写入
  version_boundaries 或 uncertainty。
- [ ] 资源可复用【生态贡献10】：SKILL.md、四问规则、中间表示说明、降级规则、自检脚本齐全。
- [ ] 结构合法【全部】：result.json 含 case_id + 全部 output_contract 字段 +
  status/sources/uncertainty/pending_human/change_log，可解析。
- [ ] 未编造【全部】：status/sources/uncertainty/change_log 与真实执行一致，未写任何
  评测分数或伪造运行结果。

# 降级策略（禁止编造）

- 材料缺失/字段不明：在 uncertainty 或 version_boundaries 标注“缺失/无法判断”，
  字段留空或 null，不补造数值、文献、作者、政策口径。
- 图无法读取（无 pdf-text-tool/ocr-tool 输出）：figure_model 标
  `unread_placeholder`，写 limitations，不假装已读数。
- 引用无法核实：保留原文位置并标 `unverified`，不伪造 DOI/URL。
- 输入超长/跨度过大：截取关键段落并在 uncertainty 说明“已截断/未覆盖”。
- 软件/工具不可用（office 渲染等）：如实写 degradations，交付纯文本/结构化产物。
- 运行失败：status=failed，change_log 记录失败原因与已完成中间产物，result.json
  仍须落盘。

# 脚本与资源（相对路径，从 SKILL.md 所在目录执行）

- `scripts/main.py`：`--phase bootstrap|validate|numeric|selftest`，标准库，确定性。
- `scripts/core_impl.py`：发现/归一化/骨架/中间表示/合并/校验，可被直接 import。
- `scripts/selftest.py`：内置 fixture 自检，空输入/坏 JSON/超长文件各跑一次。
- `references/four-questions-and-rules.md`：读得准/分得清/说得住/核得了规则与检查表。
- `references/intermediate-representation.md`：三层中间表示 schema 与可复核数据流。

# 最小调用示例（示意，非正式案例结果）

示意输入 `/input/case.json` 含 `case_id="demo-case"`、`output_contract=
{"deliverable":..., "facts_sources":[...], "version_boundaries":{...}}`。执行六步后
`/app/output/result.json` 示意结构（值均为示例）：

```json
{
  "case_id": "demo-case",
  "deliverable": {"file": "manuscript.md"},
  "facts_sources": [{"locator": "materials/paper.md#L12", "quote": "12.5 ± 0.3 mg/mL (SEM, n=6)"}],
  "version_boundaries": {},
  "status": "complete",
  "sources": [], "uncertainty": [], "pending_human": [], "change_log": [],
  "intermediates": {"plan": "/app/work/plan.json", "figure_model": "/app/work/figure_model.json", "evidence_map": "/app/work/evidence_map.json"}
}
```
