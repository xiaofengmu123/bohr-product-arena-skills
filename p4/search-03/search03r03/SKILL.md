---
name: literature-screening-ranking
description: 对候选文献集做可复核的相关性判断、分层筛选与稳定可解释排序；处理重复/版本冲突与信息缺失，逐条保留 locator + 逐字 quote 证据；内置确定性自检（同输入两次运行决策内容与 decision_fingerprint 一致）。当需要从一批候选论文中筛选与科研问题相关的文献并排序、去噪、辅助人工复核时使用。
version: 1.1.0
---

# 文献筛选与智能排序（确定性版）

## 1. 角色与何时使用

把"候选文献 → 逐条相关性判断 → 分层筛选 → 稳定可解释排序 → 可复核结果"沉淀为可重复流程。
**何时使用**：输入给出科研问题、一批候选文献（标题/作者/年份/摘要/DOI）与纳排标准，需要判相关、排除噪声、去重、排序并交付可复核结果。
**不适用**：底层数据库接入、全文精读、写作、系统综述完整纳排（PICO 全流程检索）。

**做对的标准**：(1) 相关性标签与可复核标注标准一致；(2) 关键文献排到用户真正会看的位置；(3) 每条保留/待定/排除决定都能被他人复核。故每条决定必须可解释、可溯源，不依赖不可复现的黑箱打分。

## 2. 输入契约

- 运行时原始输入在 `/input/`：`case.json`（任务、候选文献、纳排标准、可能带 `output_contract`）与 `task.md`。
- 文献材料按题目提供：`/input/materials/` 或 `/input/` 下的 PDF/md/jsonl/txt；**PDF 无预抽取答案层，须实际读取**（优先 pdftotext），不得依据文件名/领域标签补造内容。
- 工作目录 `/app/work/`（中间产物）；最终产物写 `/app/output/`。
- **先发现，再假设**：不假设固定键名。`scripts/main.py discover` 自动发现 case.json/task.md/材料，并从 case.json 提取科研问题、候选文献、纳排标准与 `output_contract`（键名按优先级列表探测，缺省容错）。
- 文本按 `utf-8-sig` 读取并逐级降级（BOM、坏编码、坏 JSON 不崩溃）；单文件 >2MB 跳过并记入 `degradations`。
- 若 case.json 带 `output_contract`/`output_schema`/`expected_fields` 等字段，**原样回填全部键**，缺值给 `null`/`[]`，不改名、不删未知键。

## 3. 输出契约

最终必须产出 `/app/output/result.json`，顶层至少包含：

| 字段 | 要求 |
|-|-|
| `case_id` | 输入标识（来自 case.json 或 task.md） |
| `status` | `ok` \| `partial` \| `insufficient_evidence` \| `input_error`，必须诚实 |
| 题目 output_contract 字段 | 原样回填；本 Skill 默认再提供 `results`、`dedupe_groups`、`evidence`（evidence 内嵌在 results 中）、`sources`、`uncertainty`、`change_log` |
| `results` | 每条候选的筛选与排序结果，按最终顺序排列 |
| `dedupe_groups` | 重复/版本变体聚类与裁定 |
| `sources` | 输入文件来源清单（path/kind/role） |
| `uncertainty` | 逐条不确定性说明 |
| `change_log` | 逐步改动记录（ts/step/action） |
| `errors` / `degradations` / `pending_human` | 异常、降级、待人工项 |
| `runtime` | 预算与起止时间 |
| `decision_fingerprint` | 决策内容 SHA-256（确定性核验用，finalize 自动写入） |

每条 `results[i]` 必须含：`record_id`、`title`、`authors`、`year`、`venue`、`doi`、`url`、`source_type`、
`relevance`（direct/indirect/irrelevant/unknown）、`tier`（retain/pending/exclude）、`priority_score`（0–1）、
`rank`、`reasons`（逐条理由）、`evidence`（每项含 `locator` + 逐字 `quote`）、`confidence`（high/medium/low）、
`criteria_matched`、`conflict`（冲突裁定或 null）、`dedupe`（去重角色或 null）。

另建议产出 `/app/output/screening_report.md`（含排序表与逐条理由）便于人工复核；如产图表，须附真实渲染的 PDF/PNG 预览。所有产物可打开、可编辑、可复现。

## 4. 执行流程（预算：900 秒 / 64 次模型请求 / 16384 token·响应，每 case 一次）

**铁律：前 3 分钟内必须落盘一版合法 result.json 骨架，之后只做增量改进；任何中途异常都不能留下空产物。**

| 步骤 | 动作 | 输入 → 输出 | 预算 |
|-|-|-|-|
| 0 | 定位 skill 根目录，读本 SKILL.md 与 `references/criteria.md` | — | ≤1 请求 |
| 1 | `python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output` | case.json/task.md → `result.json` 骨架 + `case_normalized.json` | ≤60 秒，≤1 请求 |
| 2 | 读骨架与 `case_normalized.json`，确认科研问题/候选数/纳排标准；空候选或空目录：status=`insufficient_evidence`，写 omissions 后跳步骤 6 | — | ≤1 请求 |
| 3 | `python3 -B scripts/main.py screen --work /app/work --output /app/output` | normalized → `scaffold.json`（词面信号、去重候选聚类、基线排序） | ≤30 秒，0 请求 |
| 4 | **逐条判断**：读每条候选标题/摘要（以及材料 PDF），按 `references/criteria.md` 判 relevance/tier/priority_score，写 reasons + evidence（locator + 逐字 quote）；批量 3–5 条/请求省请求 | 材料 → 填 `result.json.results` | ≤480 秒，≤40 请求 |
| 5 | 裁定 dedupe（preprint vs published、同一研究多报道）、冲突与平局；写 `dedupe_groups`、`conflict`、k 与平局规则 | — | ≤90 秒，≤6 请求 |
| 6 | `python3 -B scripts/main.py finalize --work /app/work --output /app/output` 校验覆盖、字段、证据与 status 诚实性；据 `selfcheck.json` 的 errors/warnings 修复后重跑 | result.json → `selfcheck.json` | ≤90 秒，≤6 请求 |
| 7 | 写 `screening_report.md`（建议），回填 `sources/uncertainty/change_log/runtime`，最终落盘 | — | ≤120 秒，≤8 请求 |

步骤 4 是主体：`relevance`/`tier`/`priority_score`/`reasons`/`evidence` 只能来自材料，不得由脚本猜。
`scaffold.json` 的 `base_score` 仅是词面先验，**不得**直接当最终排序分数。

## 5. 判定口径与确定性

- 判定口径见 `references/criteria.md`：三级相关性、三档 tier、0–1 打分、去重/冲突规则、证据要求。若 case.json 自带标准，**以输入标准为最高优先级**。
- 确定性排序键（固定，禁止改动）：
  `tier(asc) → priority_score(desc) → evidence_completeness(desc) → year(desc) → norm_title(asc) → record_id(asc)`
- `finalize` 强制按此键排序并重写 `rank=1..N`，保证同输入两次运行决策一致；`decision_fingerprint` 只覆盖决策字段（不含时间戳/运行时长），用于跨运行核验。

## 6. 自检

- 自检清单见 `references/checklist.md`，逐条对应评分维度（实际效果40/使用增益25/创新增益15/可靠体验10/生态贡献10）。
- 交付前 `finalize` 必须 `errors=0`；warning 逐条修复或如实说明。
- 运行 `python3 -B scripts/selftest.py` 做确定性自检（双跑一致、空输入、坏 JSON、缺元数据），必须全部 PASS。

## 7. 降级策略（严禁编造）

- **空输入/空候选**：退出码 0，写 `insufficient_evidence`/`input_error` + omissions/pending_human，列出缺什么，不生成示例文献。
- **坏 JSON/坏编码**：捕获写入 `errors`/`degradations`，用 task.md 文本继续，其余材料照常处理。
- **缺摘要/年份/DOI**：对应字段 `null`，`relevance=unknown`、`tier=pending`、`confidence=low`，写入 `uncertainty`，不猜测。
- **缺软件/权限/接口异常**：`status` 降级 `partial` 或 `input_error`，`errors` 记录，`pending_human` 说明待人工。
- **PDF 无法解析**：先 pdftotext 重试，仍失败则 `degradations` 记 `not_reported`，不补造内容。
- 任何结论、数值、DOI、引用必须来自材料；无据写 `pending_human` 或 `not_reported`。

## 8. 脚本清单（相对本 SKILL.md 所在目录；仅标准库，Python 3）

| 脚本 | 用途 |
|-|-|
| `scripts/main.py` | discover（发现/容错解析/写骨架）、screen（词面信号/去重聚类/基线）、finalize（契约校验/确定性排序/指纹） |
| `scripts/selftest.py` | 确定性自检：双跑一致 + 空输入 + 坏 JSON + 缺元数据 |

若 cwd 不是 skill 根目录，先 `cd` 到 SKILL.md 所在目录（或 `$SKILL_ROOT`）；`python3` 不可用则回退 `python`。
`--input/--work/--output` 默认 `/input`、`/app/work`、`/app/output`。

最小调用示例（示意输出须标注"示例"）：

```bash
python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output
python3 -B scripts/main.py screen   --work /app/work --output /app/output
# （Agent 依据材料填写 result.json 的 results/dedupe_groups）
python3 -B scripts/main.py finalize --work /app/work --output /app/output
python3 -B scripts/selftest.py
```

示意 result.json 片段（示例，非正式案例结果）：

```json
{"case_id": "example_case", "status": "partial",
 "results": [{"record_id": "rec_0000", "title": "...", "relevance": "direct",
   "tier": "retain", "priority_score": 0.93, "rank": 1,
   "reasons": ["研究对象、方法与结局均匹配纳入条件 X"],
   "evidence": [{"locator": "case.json#papers[0].abstract", "quote": "..."}],
   "confidence": "high", "criteria_matched": ["X"], "conflict": null, "dedupe": null}],
 "sources": [], "uncertainty": [],
 "change_log": [{"ts": "2026-01-01T00:00:00Z", "step": "discover", "action": "skeleton"}]}
```

## 9. 限制与维护

本 Skill 面向有限快照候选集（如固定 9 篇）的逐条筛选与排序，不声称全局召回、不接入实时数据库、不声称真实人工复核。维护时更新 `references/criteria.md` 的判定口径与 `scripts/main.py` 顶部的字段发现键名即可；改动排序键须同步更新 criteria.md 第 7 节与 selftest 断言。
