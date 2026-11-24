---
name: literature-screening-ranking
description: 对给定候选文献集做可复核的相关性判断、分层筛选与稳定可解释排序，处理重复/版本冲突与信息缺失，并把每条决定的证据溯源与不确定性显式化。当需要从一批候选论文中筛选与科研问题相关的文献并排序、去噪、辅助人工复核时使用。
version: 2.0.0
---

# 文献筛选与智能排序（含不确定性显式化）

## 1. 角色与何时使用

把"候选文献 → 逐条相关性判断 → 分层筛选 → 稳定可解释排序 → 可复核结果"沉淀为可重复调用的流程。与同类流程的区别在于，本 Skill 把**降级与不确定性当作一等产物**：每条记录除了 `relevance/tier/score`，还必须给出逐字证据、证据完整度、逐条不确定性，并在顶层输出 `limitations`，缺数据时只标注、不编造。

**何时使用**：输入给出一个科研问题、一批候选文献（通常含标题/作者/年份/摘要/DOI）和纳排标准，需要判断相关、排除噪声、去重、排序，并交付能复核的结果。

**不适用**：底层数据库接入、全文精读、写作、系统综述完整纳排（PICO 全流程检索）。

**判断做对的标准**（对应评分）：(1) 相关性标签与可复核的标注标准一致；(2) 关键文献排到用户真正会看的位置；(3) 每条保留/待定/排除决定都能被他人复核；(4) 缺失信息被如实标注而非猜测。

## 2. 输入契约

- 运行时原始输入在 `/input/`：`case.json`（任务、候选文献、纳排标准、可能的 `output_contract`）与 `task.md`（题目说明）。
- 文献材料按题目提供：`/input/materials/` 或 `/input/` 下的 PDF / md / jsonl / txt。PDF 无预抽取答案层，必须实际读取（`pdftotext`），不得依据文件名或领域标签补造内容。
- 工作目录 `/app/work/`（中间产物）；最终产物写 `/app/output/`。
- **先发现，再假设**：不假设固定键名。`scripts/discover.py` 自动发现 case.json / task.md / 材料文件，并从 case.json 提取科研问题、候选文献、纳排标准与 `output_contract`。
- 文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 降级）；单文件 >2MB 跳过并记入 `degradations`。
- 若 case.json 带 `output_contract` / `output_schema` / `expected_fields` / `required_fields`，**原样回填全部键**到 result.json 顶层，缺值给 `null`/`[]`，不改名、不删未知键。

## 3. 输出契约

最终必须产出 `/app/output/result.json`，顶层至少包含：

| 字段 | 要求 |
|-|-|
| `case_id` | 输入标识（来自 case.json 或 task.md 的 challenge_id） |
| `status` | `ok` \| `partial` \| `insufficient_evidence` \| `input_error`，必须诚实 |
| 题目 output_contract 字段 | 原样回填；本 Skill 默认提供 `results`、`evidence`、`status_notes`、`omissions` |
| `results` | 每条候选的筛选与排序结果，按最终顺序排列（字段见下） |
| `dedupe_groups` | 重复/版本变体聚类与裁定（canonical/duplicate/version_variant） |
| `sources` | 输入文件来源清单（path/kind/role） |
| `uncertainty` | 全局不确定性清单（kind/note/affects） |
| `limitations` | **降级与局限显式化**（scope/note；缺数据、缺软件、仅摘要、解析丢失） |
| `pending_human` | 需人工复核/补齐的项 |
| `errors` / `degradations` | 异常与降级记录 |
| `change_log` | 逐步改动记录（ts/step/action） |
| `method` | 打分口径、k 值、平局键、确定性说明（供独立复核） |
| `runtime` | started_at/finished_at/耗时/模型请求预算使用说明 |

每条 `results[i]` 必须含：`record_id`、`title`、`authors`、`year`、`venue`、`doi`、`url`、`source_type`、`relevance`（direct/indirect/irrelevant/unknown）、`tier`（retain/pending/exclude）、`priority_score`（0–1）、`rank`、`reasons`（逐条理由）、`evidence`（每项含 `locator` + 逐字 `quote`）、`confidence`（high/medium/low）、`criteria_matched`、`criteria_missed`、`evidence_completeness`（full/partial/abstract_only/missing_abstract/none）、`uncertainty`（逐条，kind/note/affects）、`conflict`（冲突裁定或 null）、`dedupe`（去重角色或 null）。

另建议产出一份可读的 `/app/output/screening_report.md`（排序表 + 逐条理由 + 不确定性），便于人工复核。

## 4. 执行流程（总预算 900 秒、64 次模型请求、16384 token/响应，每 case 一次）

**铁律：前 3 分钟内必须落盘一版合法 result.json 骨架，之后只做增量改进；任何中途异常都不能留下空产物。**

| 步骤 | 动作 | 输入 → 输出 | 时间/请求预算 |
|-|-|-|-|
| 0 | 定位 skill 根目录，读本 SKILL.md 与 `references/criteria.md` | — | ≤1 请求 |
| 1 | `python3 -B scripts/discover.py --input /input --work /app/work --output /app/output` | case.json/task.md → `result.json` 骨架 + `case_normalized.json` | ≤60 秒，≤1 请求 |
| 2 | 读骨架与 `case_normalized.json`，确认科研问题/候选数/纳排标准；若空候选或空目录：status=`insufficient_evidence`，写 `omissions`+`limitations` 后直接进步骤 6 | — | ≤1 请求 |
| 3 | `python3 -B scripts/screen.py --work /app/work --output /app/output` | normalized → `scaffold.json`（确定性词面信号、去重聚类、基线排序） | ≤30 秒，0 请求 |
| 4 | **逐条判断**：读每条候选的标题/摘要（以及材料 PDF），按 `references/criteria.md` 判 relevance/tier/priority_score，写 reasons + evidence（locator + 逐字 quote）+ evidence_completeness + 逐条 uncertainty；批量 3–5 条/请求以省请求 | 材料 → 填 `result.json.results` | ≤480 秒，≤40 请求 |
| 5 | 裁定 dedupe（preprint vs published、同一研究多报道）、冲突与平局；写 `dedupe_groups`、`conflict`、`k` 与平局规则 | — | ≤90 秒，≤6 请求 |
| 6 | `python3 -B scripts/finalize.py --output /app/output --work /app/work` 校验覆盖、字段、证据、status 诚实性；据 `selfcheck.json` 的 errors/warnings 修复后重跑 | result.json → `selfcheck.json` | ≤90 秒，≤6 请求 |
| 7 | 写 `screening_report.md`（建议），回填 `sources/uncertainty/limitations/change_log/runtime`，最终落盘 | — | ≤120 秒，≤8 请求 |

步骤 4 是主体：`relevance/tier/priority_score/reasons/evidence` 只能来自材料，不得由脚本猜。`scaffold.json` 的 `base_score` 仅是词面先验，**不得**直接当最终排序分数。

## 5. 判定标准与自检

- 判定口径见 `references/criteria.md`：三级相关性、三档 tier、0–1 打分口径、去重/冲突规则、证据要求、**不确定性分类与证据完整度规则**。
- 自检清单见 `references/checklist.md`，逐条对应评分维度；交付前 `finalize.py` 必须 `errors=0`，warning 逐条修复或如实说明。
- 确定性：排序用固定平局键 `priority_score desc, evidence_completeness desc, year desc, norm_title asc, record_id asc`；相同输入两次运行结果一致。`finalize.py` 会校验该平局键已在 `method` 中声明。

## 6. 降级与不确定性策略（本版侧重点，严禁编造）

- **空输入/空候选**：退出码 0，写 `insufficient_evidence` + `omissions` + `limitations`，列出缺什么，不生成示例文献。
- **坏 JSON/坏编码**：捕获写入 `errors`/`degradations`，用 task.md 文本继续，其余材料照常处理。
- **缺摘要/年份/DOI**：对应字段 `null`；`evidence_completeness` 降为 `partial/abstract_only/missing_abstract`；缺摘要时 `relevance=unknown`、`tier=pending`、`confidence=low`，并写逐条 `uncertainty`（`label_missing`/`abstract_insufficient`），不猜测。
- **摘要不足/只有标题**：不强行定级；`relevance=unknown`、`tier=pending`，写 `uncertainty.abstract_insufficient`，并在顶层 `limitations` 说明仅凭摘要无法读全文。
- **信息冲突**（标注分歧、预印本 vs 正式版、同一研究多报道、多来源不一致）：`conflict` 记录两方与裁定，`dedupe` 记录角色；无法裁定则升级 `pending_human`。
- **缺软件/权限/接口异常**：`status` 降级 `partial` 或 `input_error`，`errors` 记录，`limitations`+`pending_human` 说明待人工。
- **PDF 无法解析**：先 `pdftotext` 重试，仍失败则 `degradations` 记 `not_reported`，不补造内容。
- 任何结论、数值、DOI、引用必须来自材料；无据写 `pending_human` 或 `not_reported`。**不允许**用空值占位掩盖缺失，也不允许把词面先验伪装成证据。

## 7. 脚本清单（相对本 SKILL.md 所在目录；仅标准库，Python 3）

| 脚本 | 用途 |
|-|-|
| `scripts/discover.py` | 输入发现、容错解析、契约回填、写 partial 骨架与 `case_normalized.json` |
| `scripts/screen.py` | 确定性词面信号、去重聚类、基线排序脚手架（0 模型请求） |
| `scripts/finalize.py` | result.json 契约校验 + 机器自检报告（产出 `selfcheck.json`，含 errors/warnings） |

执行前 `cd` 到 SKILL.md 所在目录（或 `$SKILL_ROOT`）；`python3` 不可用则回退 `python`。`--input/--work/--output` 默认 `/input`、`/app/work`、`/app/output`。

## 8. 最小调用示例（示意输出须标注"示例"）

```bash
python3 -B scripts/discover.py --input /input --work /app/work --output /app/output
python3 -B scripts/screen.py   --work /app/work --output /app/output
# （Agent 依据 materials 与 references/criteria.md 填写 result.json 的 results）
python3 -B scripts/finalize.py --output /app/output --work /app/work
```

示意 result.json 片段（示例，非正式案例结果）：

```json
{"case_id": "example_case", "status": "partial",
 "results": [{"record_id": "rec_0000", "title": "...", "relevance": "direct",
   "tier": "retain", "priority_score": 0.93, "rank": 1,
   "reasons": ["研究对象、方法与结局均匹配纳入条件 X"],
   "evidence": [{"locator": "case.json#papers[0].abstract", "quote": "..."}],
   "confidence": "high", "criteria_matched": ["X"], "criteria_missed": [],
   "evidence_completeness": "full", "uncertainty": [], "conflict": null, "dedupe": null}],
 "sources": [], "uncertainty": [], "limitations": [], "pending_human": [],
 "errors": [], "degradations": [], "omissions": [],
 "change_log": [{"ts": "2026-01-01T00:00:00Z", "step": "discover", "action": "skeleton"}]}
```

## 9. 限制与维护

本 Skill 面向有限快照候选集（固定 9 篇/案例）的逐条筛选与排序，不声称全局召回、不接入实时数据库、不声称真实人工复核；输出为"辅助人工复核的候选排序"，非最终系统综述结论。维护时更新 `references/criteria.md` 的判定口径、不确定性分类与 `scripts/` 的字段发现键名即可。未知输出契约键一律透传，避免破坏组织方对 result.json 的校验。
