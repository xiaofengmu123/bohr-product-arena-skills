---
name: thesis-longdoc-writing
description: 面向学位论文与长文档的学术写作 Skill：围绕给定科研材料完成章节规划、撰写/改写/校对、术语与引用统一、跨章节一致性检查、可回退修改记录与证据溯源，交付可编辑稿件、证据映射和 result.json。当任务要求"依据材料产出或改写长文档、并保留事实与来源"时使用。
version: 1.0.0
---

# 学位论文与长文档写作 Skill

## 1. 角色与何时使用

本 Skill 把"给定科研材料 → 可编辑长文档产物 → 可复核证据记录"这条流程固化为可重复执行的工作流。适用任务：学位论文章节、综述、方法/结果/讨论章节的撰写、改写、校对、术语与引用补全、格式化与结构化写作。

不适用：一次性脚本、与给定材料无关的自由创作、无材料可依的泛泛写作建议。核心目标对应题目四个问题：

- **立得住**：章节规划对齐提纲、论证链与字数分配，不堆砌段落。
- **连得上**：跨章节术语、定义、符号、编号、图表引用与结论保持一致，改动可定位影响面。
- **改得动**：局部修改只动应动处，保留作者原意，改动可回退、可比较、可说明。
- **查得清**：每条论断、引用、改动可追溯到原文片段或版本记录，他人可独立复核。

## 2. 输入契约（先发现，再假设）

运行时原始输入在 `/input/`，产物写到 `/app/output/`，工作中间态放 `/app/work/`。

1. `/input/case.json`：任务与约束，可能含 `output_contract`（输出契约）、`case_id`、`task`、`materials` 清单。字段名不固定，必须扫描识别，不能假设键名。
2. `/input/task.md`：任务说明、文体、受众、格式约束、评分证据范围。
3. 材料：`/input/` 下的 PDF、`.md`、`.txt`、`.json`、`.docx` 等；PDF 只给原文，无预提取答案层，必须实际读取原文，不得按标签猜内容。
4. 文本一律按 `utf-8-sig` 优先、多编码容错读取；单文件超过 2MB 跳过并记录；坏 JSON、坏编码捕获后继续处理其余材料。
5. 若 `case.json` 存在 `output_contract`：其全部键必须**原样回填**进 `result.json`，缺值补 `null`/`[]`，不改名、不删未知键。

## 3. 输出契约（必须满足）

必产物 `/app/output/result.json`，结构：

```json
{
  "case_id": "<从 case.json 提取，缺失填 'unknown'>",
  "<output_contract 的全部原始键>": "缺值 null，集合类键补 []",
  "status": "ok | partial | insufficient_evidence | input_error",
  "artifact": "<主产物相对/绝对路径>",
  "artifacts": ["稿件", "证据映射", "自检报告等路径"],
  "evidence": [{"claim":"","locator":"","quote":"","source":"","kind":""}],
  "sources": [{"id":"","path":"","role":""}],
  "uncertainty": ["具体不确定点"],
  "change_log": [{"action":"","target":"","before":"","after":"","reason":"","status":""}],
  "pending_human": [], "errors": [], "degradations": [], "runtime": {}
}
```

主产物要求"可打开、可编辑、可复现"：稿件给 Markdown（`.md`）；引用/术语/改动说明给 `.md`/`.json`/`.csv`；需要渲染预览的 Office/图，另存真实 PDF/PNG。`status` 必须诚实：材料不足写 `insufficient_evidence`，不得编造数据、引用、DOI 或结论。

## 4. 执行流程（总预算 900s / 64 次模型请求 / 每响应 16384 tokens）

原则：**前 3 分钟内必须先落盘一版合法 result.json 骨架，之后只做增量改进**；每次工具写盘用原子写，中途异常也不留半成品。

- **步骤 0 · 发现与骨架（≤90s，约 2 次请求）**
  输入：`/input/`。执行 `python3 -B scripts/main.py scan --input /input --output /app/output --work /app/work`，输出 `discovery.json`；再执行 `python3 -B scripts/main.py scaffold ...`，立即写出一版 `status=partial` 的 `result.json`（含 case_id 与全部契约键）。此刻 3 分钟红线已满足。
- **步骤 1 · 读材料建索引（≤90s，约 3 次请求）**
  通读 task.md 与全部材料，给每份材料记 `source` 条目；PDF 用 pdftotext 提取；把关键事实的位置记为 locator（如 `materials/paper.md#p3`）。
- **步骤 2 · 规划章节（≤60s，约 3 次请求）**
  写 `workplan.md`：研究问题、各章要回答什么、需要哪些证据、字数分配；只写计划，不写正文。
- **步骤 3 · 逐章撰写/改写（约 480s，约 40 次请求）**
  每完成一章就追加证据，执行 `python3 -B scripts/consistency.py --manuscript <file> --out /app/output/consistency_report.json` 做增量检查；发现术语/编号/引用不一致立即修复。
- **步骤 4 · 证据与改动归集（约 90s，约 8 次请求）**
  用 `python3 -B scripts/main.py add-evidence ...`、`add-change ...` 把每条论断、引用、改动写进 `result.json`；冲突信息两个都记并标 `source_conflict`。
- **步骤 5 · 终检与交付（≤90s，约 6 次请求）**
  执行 `python3 -B scripts/main.py finalize ...` 校验契约键齐全、case_id 存在、status 合法、产物文件存在，写 `selfcheck.json`；剩余约 2 次请求用于修复终检发现的问题。

请求预算留 64 次内的冗余：写作用约 40 次，工具/检查约 16 次，修复/重试约 8 次。

## 5. 自检清单（交付前逐条勾选，对应评分维度）

- [ ] 实际效果：产物是按提纲真正写出的可用章节，而非通用建议（完成度）。
- [ ] 每条论断与其引用文献对得上；数据、术语、符号、章节与图表编号与原文一致（正确性）。
- [ ] 提纲要求的研究问题、方法、结果、讨论、局限，及图表/参考文献/附录齐全（完整性）。
- [ ] 章节文字可追溯到提纲条目、原始材料、文献与版本记录（可核验性）。
- [ ] 使用增益：术语/编号/交叉引用统一由脚本自动检查，减少人工重读（效率）。
- [ ] 改动只动应动处，`change_log` 记录 before/after/reason（可编辑性）。
- [ ] 创新增益：章节规划、一致性检查、证据溯源由脚本可复现执行，非纯 LLM 口头承诺。
- [ ] 可靠体验：空输入返回 `insufficient_evidence` 且退出码 0；坏 JSON/坏编码降级不崩溃；重复运行结果一致。
- [ ] 生态贡献：脚本只用标准库、路径相对、`--help` 退出码 0；说明与实际版本一致。

## 6. 降级策略（缺什么标什么，绝不编造）

- 缺材料/材料不足 → `status=insufficient_evidence`，`pending_human` 列出缺口，不示例填充。
- 引用找不到原文、DOI/文献元数据缺失 → 写 `not_reported`/`pending_human`，不虚构 DOI 或页码。
- 同指标来源冲突 → 全部记录并标 `source_conflict`，在 `uncertainty` 说明。
- 缺软件（pdftotext 不可用等）→ `degradations` 记录，改用已有可读文件，缺失部分标 `limitations`。
- 写盘失败 → 降级写最小合法 JSON（case_id + status + errors），不让进程崩溃。

## 7. 脚本与路径（相对 Skill 根目录，Linux 下可执行）

- `scripts/main.py`：`scan / scaffold / add-evidence / add-change / set-status / finalize`。
- `scripts/consistency.py`：确定性跨章节一致性检查（编号、术语、引用、重复定义）。
- `references/checklist.md`：逐项自检清单与评分维度对照。
- `references/evidence-rules.md`：证据溯源与 quote 规范化规则。

缺省参数：`--input /input`、`--output /app/output`、`--work /app/work`。所有脚本只依赖 Python 标准库，用 `python3 -B scripts/consistency.py --help` 验证。
