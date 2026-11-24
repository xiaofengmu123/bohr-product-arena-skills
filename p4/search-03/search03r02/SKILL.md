---
name: evidence-literature-screening
description: 对给定候选文献集做可复核的相关性判断、分层筛选、稳定可解释排序与去重，并为每条决定保留逐字证据，同时以"先落盘 partial 骨架、再增量改进"的时间预算保证任何中断都不留空产物。当需要从一批候选论文中筛选与科研问题相关的文献并排序、去噪、辅助人工复核时使用。
version: 2.0.0
---

# 证据可溯的文献筛选与智能排序

## 1. 角色与何时使用

本 Skill 把"候选文献 → 逐条相关性判断 → 分层筛选 → 稳定可解释排序 → 可复核结果"
沉淀为可重复调用的流程。**何时使用**：输入给出科研问题、一批候选文献（标题/作者/
年份/摘要/DOI）与纳排标准，需要判断相关、排除噪声、去重、排序并交付可复核结果。
**不适用**：底层数据库接入、全文精读、写作、系统综述完整纳排（PICO 全流程检索）。

**做对的标准**（对应评分）：(1) 相关性标签与可复核标注标准一致；(2) 关键文献排到
用户真正会看的位置；(3) 每条保留/待定/排除决定能被他人复核。故每条决定必须可解释、
可溯源，不依赖不可复现的黑箱打分。**本版核心取舍**：时间预算与早落盘——把 900 秒 /
64 次请求拆成阶段预算，前 3 分钟内先写一版合法的 partial 结果骨架，之后只做增量改进，
任何中途异常都不留下空产物。

## 2. 输入契约

- 运行时原始输入在 `/input/`：`case.json`（任务、候选文献、纳排标准、可能的
  `output_contract`）与 `task.md`（题目说明）。
- 文献材料按题目提供：`/input/materials/` 或 `/input/` 下的 PDF / md / jsonl / txt。
  PDF 无预抽取答案层，**必须实际读取**（优先用 `pdftotext`；正文不可读时如实记录）。
- 工作目录 `/app/work/`（中间产物）；最终产物写 `/app/output/`。
- **先发现，再假设**：不假设固定键名。用 `scripts/main.py discover` 自动发现
  case.json / task.md / 材料文件，并从 case.json 提取科研问题、候选文献、纳排标准与
  `output_contract`，写入 `case_normalized.json`。
- 文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 降级）；单文件 >2MB 跳过并记
  `degradations`。
- 若 case.json 带 `output_contract` / `output_schema` / `expected_fields` 等字段，
  **原样回填全部键**：缺值给 `null`/`[]`，不改名、不删未知键。

## 3. 输出契约

最终必须产出 `/app/output/result.json`，顶层至少包含：

| 字段 | 要求 |
|-|-|
| `case_id` | 输入标识（来自 case.json 或 task.md） |
| `status` | `ok` \| `partial` \| `insufficient_evidence` \| `input_error`，必须诚实 |
| 题目 output_contract 字段 | 原样回填；本 Skill 默认提供 `results`、`evidence`、`status_notes`、`omissions` |
| `results` | 每条候选的筛选与排序结果，按最终顺序排列（字段见下） |
| `dedupe_groups` | 重复/版本变体聚类与裁定 |
| `sources` | 输入文件来源清单（path/kind/role/bytes） |
| `uncertainty` | 逐条不确定性说明 |
| `change_log` | 逐步改动记录（ts/step/action/detail），含各阶段落盘时间戳 |
| `errors` / `degradations` / `pending_human` / `runtime` | 异常、降级、待人工、运行记录 |

每条 `results[i]` 必须含：`record_id`、`title`、`authors`、`year`、`venue`、`doi`、
`url`、`source_type`、`relevance`（direct/indirect/irrelevant/unknown）、
`tier`（retain/pending/exclude）、`priority_score`（0–1）、`rank`、`reasons`、
`evidence`（每项含 `locator` + 逐字 `quote`）、`confidence`、`criteria_matched`、
`conflict`（冲突裁定或 null）、`dedupe`（去重角色或 null）、`locator`、`source_file`。

另建议产出一份可读的 `/app/output/screening_report.md`（含排序表与逐条理由），便于
人工复核；如产出图/表，须附真实渲染的 PDF/PNG 预览。所有产物可打开、可编辑、可复现。

## 4. 执行流程（总预算：900 秒、64 次模型请求，每 case 一次）

**铁律：前 3 分钟（≤180 秒）内必须落盘一版合法 result.json 骨架，之后只做增量改进；
任何中途异常都不能留下空产物。** `discover` 阶段立即写出覆盖全部候选、relevance 均为
`unknown` 的 partial 骨架，即使后续 Agent 判断未完成，产物也已是合法可读的。

| 步骤 | 动作 | 输入 → 输出 | 时间/请求预算 | 落盘检查点 |
|-|-|-|-|-|
| 0 | 定位 skill 根目录，读本 SKILL.md 与 `references/criteria.md` | — | ≤1 请求 | — |
| 1 | `python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output` | case.json/task.md → `result.json` 骨架 + `case_normalized.json` | ≤60 秒，≤1 请求 | **T+180s 前骨架已落盘** |
| 2 | 读 `result.json` 骨架与 `case_normalized.json`，确认科研问题/候选数/纳排标准；空候选或空目录：status=`insufficient_evidence`，写 omissions 后直接进步骤 6 | — | ≤1 请求 | — |
| 3 | `python3 -B scripts/main.py screen --work /app/work --output /app/output` | normalized → `scaffold.json`（确定性词面信号、去重聚类） | ≤30 秒，0 请求 | dedupe_groups 已回填 |
| 4 | **逐条判断**：读每条候选的标题/摘要（以及材料 PDF），按 `references/criteria.md` 判 relevance/tier/priority_score，写 reasons + evidence（locator + 逐字 quote）；批量处理省请求（3–5 条/请求） | 材料 → 填 `result.json.results` | ≤480 秒，≤40 请求 | 每批后增量回写 result.json |
| 5 | 裁定 dedupe（preprint vs published、同一研究多报道）、冲突与平局；写 `dedupe_groups`、`conflict`、平局规则 | — | ≤90 秒，≤6 请求 | — |
| 6 | `python3 -B scripts/main.py finalize --work /app/work --output /app/output` 校验覆盖、字段、证据与 status 诚实性；据 `errors`/`warnings` 修复后重跑 | result.json → `selfcheck.json` | ≤90 秒，≤6 请求 | `errors=0` |
| 7 | 写 `screening_report.md`（可选但建议），回填 `sources/uncertainty/change_log/runtime`，最终落盘 | — | ≤120 秒，≤8 请求 | 最终产物 |

步骤 4 是主体：`relevance`/`tier`/`priority_score`/`reasons`/`evidence` 只能来自材料，
不得由脚本猜。`scaffold.json` 的 `base_score` 仅是词面先验，**不得**直接当最终排序分数。
每完成一批判断就重写一次 `result.json`（增量改进），确保任一时刻产物可读。

## 5. 判定标准与自检

- 判定口径见 `references/criteria.md`：三级相关性、三档 tier、0–1 打分口径、去重/冲突
  规则、证据要求。若 case.json 自带标准，**以输入标准为最高优先级**。
- 自检清单见 `references/checklist.md`，逐条对应评分维度（实际效果/使用增益/创新增益/
  可靠体验/生态贡献）。交付前 `finalize` 必须 `errors=0`；warning 逐条修复或如实说明。
- 确定性：排序用固定平局键 `priority_score desc, evidence_completeness desc, year desc,
  norm_title asc, record_id asc`；相同输入两次运行结果一致。
- 用 `python3 -B scripts/selftest.py` 跑空输入/坏 JSON/确定性三场景自检。

## 6. 降级策略（严禁编造）

- **空输入/空候选**：退出码 0，写 `insufficient_evidence` + omissions，列出缺什么，
  不生成示例文献。
- **坏 JSON/坏编码**：捕获写入 `errors`/`degradations`，用 task.md 文本继续，
  其余材料照常处理。
- **缺摘要/年份/DOI**：对应字段 `null`，`relevance=unknown`、`tier=pending`、
  `confidence=low`，写入 `uncertainty`，不猜测。
- **缺软件/权限/接口异常**：`status` 降级 `partial` 或 `input_error`，`errors` 记录，
  `pending_human` 说明待人工；**不得声称缺软件为"已安装"或伪造状态**。
- **PDF 无法解析**：先用 `pdftotext` 重试，仍失败则 `degradations` 记 `not_reported`，
  不依据文件名/领域标签补造内容。
- 任何结论、数值、DOI、引用必须来自材料；无据写 `pending_human` 或 `not_reported`。

## 7. 脚本清单（相对本 SKILL.md 所在目录；仅标准库，Python 3）

| 脚本 | 用途 |
|-|-|
| `scripts/main.py` | 薄入口，转发到 core_impl.py（<30KB） |
| `scripts/core_impl.py` | discover/screen/finalize 三阶段实现 |
| `scripts/selftest.py` | 空输入、坏 JSON、确定性三场景自检 |

若执行环境 cwd 不是 skill 根目录，先 `cd` 到 SKILL.md 所在目录（或 `$SKILL_ROOT`），
再运行相对路径；`python3` 不可用则回退 `python`。`--input/--work/--output` 默认
`/input`、`/app/work`、`/app/output`。

最小调用示例（示意输出须标注"示例"）：

```bash
python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output
python3 -B scripts/main.py screen   --work /app/work --output /app/output
# （Agent 依据材料填写 result.json 的 results）
python3 -B scripts/main.py finalize --work /app/work --output /app/output
```

示意 result.json 片段（示例，非正式案例结果）：

```json
{"case_id": "example_case", "status": "partial",
 "results": [{"record_id": "rec_0000", "title": "...", "relevance": "direct",
   "tier": "retain", "priority_score": 0.93, "rank": 1,
   "reasons": ["研究对象、方法与结局均匹配纳入条件 X"],
   "evidence": [{"locator": "case.json#papers[0].abstract", "quote": "..."}],
   "confidence": "high", "criteria_matched": ["X"], "conflict": null, "dedupe": null}],
 "sources": [], "uncertainty": [], "change_log": [{"ts": "2026-01-01T00:00:00Z", "step": "discover", "action": "partial_skeleton_written"}]}
```

## 8. 限制与维护

本 Skill 面向有限快照候选集（如固定 9 篇）的逐条筛选与排序，不声称全局召回、不接入
实时数据库、不声称真实人工复核。维护时更新 `references/criteria.md` 的判定口径、
`scripts/core_impl.py` 的字段发现键名（ID_KEYS / TITLE_KEYS 等）与平局键即可。
