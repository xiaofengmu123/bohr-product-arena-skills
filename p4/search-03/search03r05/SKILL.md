---
name: literature-screening-ranking
description: 对给定候选文献集做可复核的相关性判断、分层筛选与稳定可解释排序，处理重复/版本冲突与信息缺失，并为每条保留/排除决定保留逐字证据与完整契约回填。适用于从一批候选论文中筛选与科研问题相关的文献、去噪排序并辅助人工复核；不适用于底层数据库接入、全文精读、写作或系统综述纳排流程。
version: 1.1.0
---

# 文献筛选与智能排序

## 1. 角色与何时使用

把"候选文献 → 逐条相关性判断 → 分层筛选 → 稳定可解释排序 → 可复核结果"沉淀为可重复调用的流程。
**何时使用**：输入给出科研问题、一批候选文献（含标题/作者/年份/摘要/DOI 等）与纳排标准，需要判断相关、
排除噪声、去重、排序并交付可复核结果。**不适用**：底层数据库接入、全文精读、写作、系统综述完整纳排。

**做对的标准**：(1) 相关性标签与可复核的标注标准一致；(2) 关键文献排到用户真正会看的位置；
(3) 每条保留/待定/排除决定都可被他人复核。因此每条决定必须可解释、可溯源，不依赖黑箱打分。

## 2. 输入契约

- 运行时原始输入在 `/input/`：`case.json`（任务、候选文献、纳排标准、可能的 `output_contract`）与 `task.md`（题面说明）。
- 文献材料按题目提供：`/input/materials/` 或 `/input/` 下的 PDF / md / jsonl / txt（PDF 无预抽取答案层，须实际读取）。
- 工作目录 `/app/work/`（中间产物）；最终产物写 `/app/output/`。
- **先发现，再假设**：不假设固定键名。`scripts/main.py discover` 自动发现 case.json / task.md / 材料文件，
  提取科研问题、候选文献、纳排标准与 `output_contract`（亦识别 `output_schema` / `expected_fields` / `required_fields`）。
- 文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 降级）；单文件 >2MB 跳过并记录到 `degradations`。
- 若 case.json 带 `output_contract`，**原样回填全部键到 result.json 顶层**，缺值给 `null`/`[]`，
  不改名、不删未知键，并在 `contract.alignment` 记录对齐关系。

## 3. 输出契约

必须产出 `/app/output/result.json`。顶层字段：

| 字段 | 要求 |
|-|-|
| `case_id` | 输入标识（来自 case.json 或 task.md） |
| `status` | `ok` \| `partial` \| `insufficient_evidence` \| `input_error`，必须诚实 |
| 题目 `output_contract` 全部键 | 原样回填（echo），缺值 `null`/`[]` |
| `results` | 每条候选的筛选与排序结果，按最终顺序，字段见下 |
| `dedupe_groups` | 重复/版本聚类与裁定 |
| `evidence` | 证据索引（locator→quote 聚合） |
| `sources` | 输入文件来源清单（path/kind/role） |
| `uncertainty` / `pending_human` | 逐条不确定性、待人工项 |
| `errors` / `degradations` | 异常与降级（不编造） |
| `change_log` | 逐步改动记录（ts/step/action/detail），append-only |
| `runtime` / `contract` | 运行时间线；契约发现来源与字段对齐 |

每条 `results[i]` 必须含：`record_id`、`title`、`authors`、`year`、`venue`、`doi`、`url`、
`source_type`、`relevance`（direct/indirect/irrelevant/unknown）、`tier`（retain/pending/exclude）、
`priority_score`（0–1）、`rank`、`reasons`（逐条理由）、`evidence`（每项含 `locator` + 逐字 `quote`）、
`confidence`（high/medium/low）、`criteria_matched`、`conflict`（冲突裁定或 null）、`dedupe`（去重角色或 null）。

**契约对齐约定**：`output_contract` 中语义等同筛选输出的列表型字段（如 `retrieved_records` /
`screening_decisions` / `evidence_map`）在 discover 阶段先回填 `[]` 占位，步骤 4–7 中 Agent 应将其
镜像/映射为 `results`（或对应子集）；`finalize` 会对仍为空的契约字段给出 warning 提示补填，
确保"字段逐一对齐"而非只回填键名。

另建议产出 `/app/output/screening_report.md`（排序表 + 逐条理由）便于人工复核；如产图表须附真实 PDF/PNG 预览。

## 4. 执行流程（预算：900 秒、64 次模型请求、每响应 ≤16384 token）

**铁律：前 3 分钟内必须落盘一版合法 result.json（discover 即写全字段骨架），之后只做增量改进；任何中途异常不得留下空产物。**

| 步骤 | 动作（输入 → 输出） | 时间/请求 |
|-|-|-|
| 0 | 定位 skill 根目录（`$SKILL_ROOT` 或 cd 到 SKILL.md 所在目录），读本文件与 `references/criteria.md` | ≤1 请求 |
| 1 | `python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output`（case.json/task.md → result.json 骨架 + case_normalized.json；每个候选预填完整字段 stub） | ≤60s，≤1 请求 |
| 2 | 读 result.json 与 case_normalized.json，确认科研问题/候选数/纳排标准；空候选或空目录 → `insufficient_evidence` + omissions，直接进步骤 6 | ≤1 请求 |
| 3 | `python3 -B scripts/main.py screen --work /app/work --output /app/output`（→ scaffold.json：确定性词面信号、去重聚类、基线排序） | ≤30s，0 请求 |
| 4 | **逐条判断**：读每条候选标题/摘要（及材料 PDF），按 `references/criteria.md` 判 relevance/tier/priority_score，填写 reasons + evidence（locator + 逐字 quote）；批量 3–5 条/请求 | ≤480s，≤40 请求 |
| 5 | 裁定 dedupe（预印本 vs 正式版、同研究多报道）、冲突与平局；写 `dedupe_groups`、`conflict`、平局规则 | ≤90s，≤6 请求 |
| 6 | `python3 -B scripts/main.py finalize --output /app/output --work /app/work` 校验覆盖、字段、证据与 status；据 errors/warnings 修复后重跑 | ≤90s，≤6 请求 |
| 7 | 写 `screening_report.md`（建议），回填 sources/uncertainty/change_log/runtime，最终落盘 | ≤120s，≤8 请求 |

步骤 4 是主体：`relevance`/`tier`/`priority_score`/`reasons`/`evidence` 只能来自材料，不得由脚本猜。
`scaffold.json` 的 `base_score` 仅为词面先验，**不得**直接当最终排序分数。

## 5. 判定标准与自检

- 判定口径见 `references/criteria.md`：三级相关性、三档 tier、0–1 打分、去重/冲突规则、证据要求。
  若 case.json 自带标准，以输入标准为最高优先级。
- 自检清单见 `references/checklist.md`，逐条对应评分维度（实际效果/使用增益/创新增益/可靠体验/生态贡献）。
  机读校验用 `scripts/main.py finalize`（errors=0）与 `scripts/main.py selftest`（全 PASS）。
- 确定性排序平局键：`priority_score desc → evidence_completeness desc → year desc → norm_title asc → record_id asc`；
  相同输入两次运行结果一致。

## 6. 降级策略（严禁编造）

- **空输入/空候选**：退出码 0，写 `insufficient_evidence` + omissions，列出缺什么，不生成示例文献。
- **坏 JSON/坏编码**：捕获写入 `errors`/`degradations`，用 task.md 文本继续，其余材料照常处理。
- **缺摘要/年份/DOI**：字段 `null`，`relevance=unknown`、`tier=pending`、`confidence=low`，写入 `uncertainty`，不猜测。
- **缺软件/权限/接口异常**：`status` 降级 `partial` 或 `input_error`，`errors` 记录，`pending_human` 说明待人工。
- **PDF 无法解析**：先 `pdftotext` 重试，仍失败则 `degradations` 记 `not_reported`，不按文件名/领域标签补造内容。
- 任何结论、数值、DOI、引用必须来自材料；无据写 `pending_human` 或 `not_reported`。

## 7. 脚本清单（相对 SKILL.md 所在目录；仅标准库，Python 3）

| 脚本 | 用途 |
|-|-|
| `scripts/main.py` | 统一入口，子命令 discover/screen/finalize/selftest；主流程实现见 `scripts/core_impl.py` |
| `scripts/core_impl.py` | 输入发现、契约回填、确定性脚手架、去重、校验、自测实现 |
| `scripts/selftest.py` | 独立自检入口（对合成 fixture 全流程断言） |

若执行环境 cwd 不是 skill 根目录，先 `cd` 到 SKILL.md 所在目录（或 `$SKILL_ROOT`）再运行相对路径；
`python3` 不可用则回退 `python`。

最小调用示例（示意输出须标注"示例"）：

```bash
python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output
python3 -B scripts/main.py screen   --work /app/work --output /app/output
# （Agent 依据材料填写 result.json 的 results）
python3 -B scripts/main.py finalize --output /app/output --work /app/work
python3 -B scripts/main.py selftest
```

示意 result.json 片段（示例，非正式案例结果）：

```json
{"case_id": "example_case", "status": "partial",
 "results": [{"record_id": "rec_0000", "title": "...", "relevance": "direct",
   "tier": "retain", "priority_score": 0.93, "rank": 1,
   "reasons": ["研究对象、方法与结局均匹配纳入条件 X"],
   "evidence": [{"locator": "case.json#papers[0].abstract", "quote": "..."}],
   "confidence": "high", "criteria_matched": ["X"], "conflict": null, "dedupe": null}],
 "sources": [], "uncertainty": [], "change_log": [{"ts": "2026-01-01T00:00:00Z", "step": "discover", "action": "skeleton"}]}
```

**限制与维护**：本 Skill 面向有限快照候选集（如固定 9 篇）的逐条筛选与排序，不声称全局召回、不接入实时数据库、
不声称真实人工复核。维护时更新 `references/criteria.md` 判定口径与 `scripts/core_impl.py` 的字段发现键名即可。