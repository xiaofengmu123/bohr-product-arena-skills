---
name: literature-screening-ledger
description: 对给定候选文献集做可复核的相关性判断、分层筛选与稳定可解释排序。采用"证据账本（decisions.jsonl）→ 校验后合并"的架构：每条决定只追加一行 JSON，脚本校验后再组装 result.json；处理去重/版本冲突与信息缺失，每条决定带逐字证据。当需要从一批候选论文中筛选与科研问题相关的文献并排序、去噪、辅助人工复核时使用。
version: 2.0.0
---

# 文献筛选与智能排序（证据账本版）

## 1. 角色与何时使用

本 Skill 把"候选文献 → 逐条相关性判断 → 分层筛选 → 稳定可解释排序 → 可复核结果"沉淀为可重复流程。
**何时使用**：输入给科研问题、一批候选文献（标题/作者/年份/摘要/DOI）与纳排标准，需判相关、去噪、去重、
排序并交付可复核结果。**不适用**：底层数据库接入、全文精读、写作、系统综述完整纳排（PICO 全流程检索）。

做对的标准对应评分三件事：(1) 相关性标签与可复核标注标准一致；(2) 关键文献排在用户真正会看的位置；
(3) 每条保留/待定/排除决定都能被复核。故每条决定可解释、可溯源，不依赖黑箱打分。

**架构要点（与"直接改 result.json"的写法不同）**：Agent 只在 `decisions.jsonl` 里**逐行追加**判断，
`result.json` 由脚本在**校验通过后**合并生成，保证契约完整、可复现、可回滚。

## 2. 输入契约

- 原始输入在 `/input/`：`case.json`（任务、候选文献、纳排标准、可选 `output_contract`）与 `task.md`。
- 材料在 `/input/materials/` 或 `/input/` 下（PDF/MD/TXT/JSONL/JSON）；PDF **无预抽取答案层**，须实际 `pdftotext` 读取。
- 工作目录 `/app/work/`（中间产物）；最终产物写 `/app/output/`（含 `result.json`）。
- **先发现，再假设**：不假设键名。`discover` 自动识别 case.json/task.md/材料，并提取科研问题、候选文献、
  纳排标准与 `output_contract`。`python3` 不可用回退 `python`。
- 文本按 `utf-8-sig` 读取并容错（BOM/坏编码/坏 JSON 降级）；单文件 >2MB 跳过并记入 `degradations`。
- 若 `case.json` 带 `output_contract`/`output_schema`/`expected_fields` 等键，**原样回填全部键**，
  缺值 `null`/`[]`，不改名、不删未知键。

## 3. 输出契约

最终必须产出 `/app/output/result.json`，顶层至少含：

| 字段 | 要求 |
|-|-|
| `case_id` | 输入标识（来自 case.json，缺失回退 `unknown_case`） |
| `status` | `ok` \| `partial` \| `insufficient_evidence` \| `input_error`，诚实标注 |
| `research_question` / `criteria` | 回填科研问题与 `{inclusion, exclusion}` |
| `results` | 每条候选的筛选与排序结果，按最终顺序排列 |
| `dedupe_groups` / `conflicts` | 重复/版本变体聚类与冲突裁定 |
| `sources` / `uncertainty` / `omissions` | 来源清单、逐条不确定性、遗漏说明 |
| `pending_human` / `errors` / `degradations` | 待人工项、错误、降级 |
| `change_log` / `runtime` / `status_notes` | 脚本逐步记录、运行元信息、状态说明 |
| 题目 `output_contract` 字段 | 原样回填，缺值 `null`/`[]` |

每条 `results[i]` 含：`record_id`、`title`、`authors`、`year`、`venue`、`doi`、`url`、`source_type`、
`relevance`（direct/indirect/irrelevant/unknown）、`tier`（retain/pending/exclude）、`priority_score`（0–1）、
`rank`、`reasons`、`criteria_matched`、`evidence`（每项 `locator`+逐字 `quote`）、`confidence`、
`score_components`（base/evidence_bonus/recency_bonus，解释分数来源）、`conflict`、`dedupe`。

另建议产出可读的 `/app/output/screening_report.md`（排序表+逐条理由）供人工复核；如出图表须附真实渲染 PDF/PNG。
所有产物可打开、可编辑、可复现。

## 4. 执行流程（总预算 900 秒、64 次模型请求，每 case 一次）

**铁律：前 3 分钟内 `discover` 必须落盘一版合法 `result.json` partial 骨架，之后只做增量改进；任何中途异常不得留空产物。**

| 步骤 | 动作 | 输入 → 输出 | 预算 |
|-|-|-|-|
| 0 | 定位 skill 根目录，读本 SKILL.md 与 `references/criteria.md` | — | ≤1 请求 |
| 1 | `python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output` | case.json/task.md → `result.json` 骨架 + `case_normalized.json` + 空账本 | ≤60 秒，≤1 请求 |
| 2 | 读 `case_normalized.json` 与 `result.json` 骨架，确认科研问题/候选数/标准；空候选或空目录 → `insufficient_evidence`，写 omissions 后直接进步骤 6 | — | ≤1 请求 |
| 3 | `python3 -B scripts/main.py signal --work /app/work --output /app/output` | normalized → `candidates.json`（词面信号）+ `scaffold.json`（去重/冲突） | ≤30 秒，0 请求 |
| 4 | **逐条判断**：读每条候选标题/摘要及材料 PDF，按 `references/criteria.md` 判 relevance/tier/confidence/reasons/evidence；把每条决定写为**一行 JSON 追加**到 `/app/work/decisions.jsonl` | 材料 → 账本 | ≤480 秒，≤40 请求 |
| 5 | 补齐冲突/去重裁定证据（preprint vs published、同一研究多报道），可改账本中该条 `conflict`/`dedupe` 说明 | — | ≤90 秒，≤6 请求 |
| 6 | `python3 -B scripts/main.py merge --work /app/work --output /app/output`：校验账本、推导分数、排序、组装 result.json | 账本 → result.json | ≤30 秒，0 请求 |
| 7 | `python3 -B scripts/main.py finalize --work /app/work --output /app/output`：出 `selfcheck.json`；`errors=0` 为止，warning 逐条修复或说明 | result.json → selfcheck.json | ≤90 秒，≤6 请求 |
| 8 | 写 `screening_report.md`（可选但建议），回填 `sources/uncertainty/change_log`，最终落盘 | — | ≤120 秒，≤8 请求 |

`relevance`/`tier`/`reasons`/`evidence` 只能来自材料，不得由脚本猜；`scaffold.json` 里的词面先验不是最终分。
`decisions.jsonl` 每条 JSON 字段：`record_id`、`relevance`、`tier`、`confidence`、`reasons[]`、
`criteria_matched[]`、`evidence[{locator,quote}]`；分数不写账本，由 merge 按固定公式推导（见 criteria.md §3）。

## 5. 判定标准与自检

- 判定口径见 `references/criteria.md`：三级相关性、三档 tier、0–1 打分公式、去重/冲突、证据要求。
  若 case.json 自带标准，**以输入标准为最高优先级**。
- 自检清单见 `references/checklist.md`（逐条对应评分维度）；`scripts/main.py selftest` 用**已标注的合成夹具**
  全流程自检（discover→signal→账本→merge→finalize），只验证契约与确定性，不产出正式答案。
- 确定性：排序固定 `tier → priority_score desc → evidence 条数 desc → year desc → norm_title asc → record_id asc`；
  同输入两次运行结果一致。

## 6. 降级策略（严禁编造）

- **空输入/空候选**：退出码 0，写 `insufficient_evidence` + omissions，列出缺什么，不生成示例文献。
- **坏 JSON/坏编码**：捕获入 `errors`/`degradations`，用 task.md 文本继续，其余材料照常处理。
- **缺摘要/年份/DOI**：对应字段 `null`，`relevance=unknown`、`tier=pending`、`confidence=low/unknown`，写 `uncertainty`。
- **缺软件/权限/接口**：status 降 `partial` 或 `input_error`，`errors`/`pending_human` 说明待人工。
- **PDF 无法解析**：先 `pdftotext` 重试，仍失败则 `degradations` 记 `not_reported`，不按文件名/领域标签补造内容。
- 任何结论、数值、DOI、引用必须来自材料；无据写 `pending_human` 或 `not_reported`；`limitations`（若有）如实写出。

## 7. 脚本与资源（相对本 SKILL.md；仅标准库，Python 3）

| 文件 | 用途 |
|-|-|
| `scripts/main.py` | 入口分派（discover/signal/merge/finalize/selftest），实现接 `scripts/core.py` |
| `scripts/core.py` | 发现、信号、校验-合并、终检的确定性实现 |
| `scripts/selftest.py` | 合成夹具全流程自检（明确标注"示例/合成"，非正式答案） |
| `references/criteria.md` | 相关性/分层/打分/去重/证据口径 |
| `references/checklist.md` | 逐条自检清单（对应评分维度） |

## 8. 最小调用示例（示意输出须标明"示例"）

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output
python3 -B scripts/main.py signal   --work /app/work --output /app/output
# Agent 逐条判断，追加一行 JSON 到 /app/work/decisions.jsonl
python3 -B scripts/main.py merge    --work /app/work --output /app/output
python3 -B scripts/main.py finalize --work /app/work --output /app/output
python3 -B scripts/main.py selftest
```

示意 result.json 片段（示例，非正式案例结果）：

```json
{"case_id": "example_case", "status": "partial",
 "results": [{"record_id": "rec_0000", "title": "...", "relevance": "direct",
   "tier": "retain", "priority_score": 0.85, "rank": 1,
   "reasons": ["研究对象、方法与结局均匹配纳入条件 X"],
   "evidence": [{"locator": "case.json#papers[0].abstract", "quote": "..."}],
   "confidence": "high", "criteria_matched": ["X"],
   "score_components": {"base": 0.70, "evidence_bonus": 0.15, "recency_bonus": 0.0},
   "conflict": null, "dedupe": null}],
 "sources": [], "uncertainty": [], "change_log": [{"ts": "2026-01-01T00:00:00Z", "step": "discover", "action": "skeleton"}]}
```

## 9. 边界与维护

面向有限快照候选集（如固定九篇）的逐条筛选与排序，不声称全局召回、不接实时数据库、不声称真实人工复核。
维护时更新 `references/criteria.md` 判定口径与 `scripts/core.py` 的字段发现键名即可。