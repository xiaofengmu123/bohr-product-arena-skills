---
name: literature-screening-ranking
description: 对给定候选文献集做可复核的相关性判断、分层筛选与稳定可解释排序，处理重复/版本冲突与信息缺失，并为每条决定保留逐字证据。通过 plan.json（规则层）、scaffold.json（确定性先验）、evidence_ledger.json（语义证据）三层中间表示分离，使最终排序由"规则×证据"推导而非黑箱。当需要从一批候选论文中筛选与科研问题相关的文献并排序、去噪、辅助人工复核时使用。
version: 1.1.0
---

# 文献筛选与智能排序

## 1. 角色与何时使用

本 Skill 把"候选文献 → 逐条相关性判断 → 分层筛选 → 稳定可解释排序 → 可复核结果"沉淀为可重复流程。
**何时使用**：输入给出科研问题、一批候选文献（通常含标题/作者/年份/摘要/DOI）和纳排标准，需要判断相关、排除噪声、去重、排序，并交付能复核的结果。
**不适用**：底层数据库接入、全文精读、写作、系统综述完整纳排（PICO 全流程检索）。

**判断做对的标准**（对应评分维度）：(1) 相关性标签与可复核标注标准一致；(2) 关键文献排到用户真正会看的位置；(3) 每条保留/待定/排除决定都能被他人复核。故每条决定必须可解释、可溯源。

## 2. 输入契约

- 运行时原始输入在 `/input/`：`case.json`（任务、候选文献、纳排标准、可能的 output_contract）与 `task.md`（题目说明）。
- 材料按题目提供：`/input/materials/` 或 `/input/` 下的 PDF / md / jsonl / txt（PDF 无预抽取答案层，须实际读取）。
- 工作目录 `/app/work/`（中间产物）；最终产物写 `/app/output/`。
- **先发现，再假设**：不假设固定键名。`main.py discover` 自动发现 case.json / task.md / 材料文件，并从 case.json 提取科研问题、候选文献、纳排标准与 `output_contract`。
- 文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 降级）；单文件 >2MB 跳过并记入 `degradations`。

## 3. 中间表示（本 Skill 的核心设计）

三层中间产物分离，使每个最终决定都能从"规则×证据"追溯，而非黑箱打分：

| 文件（/app/work/） | 层 | 内容 | 谁写 |
|-|-|-|-|
| `case_normalized.json` | 输入层 | 规范化 case_id、科研问题、候选集、纳排标准、output_contract、源文件清单 | `discover` |
| `plan.json` | 规则层 | 机器可检查的筛选计划：命名准则（cXX）、tier 门槛、平局键、去重/冲突规则、分档 | `discover` |
| `scaffold.json` | 先验层 | 确定性词面信号、元数据完整度、去重聚类（**仅先验，非最终判断**） | `scaffold` |
| `evidence_ledger.json` | 证据层 | 逐条语义判断：relevance/tier/priority_score/reasons/evidence（locator+逐字 quote）/confidence/conflict | **Agent 填写** |

`finalize` 合并三层并校验一致性；`result.json` 是最终交付，`result_ledger.json` 是审计副本。语义判断只写在 `evidence_ledger.json`，**不手工改 result.json**。

## 4. 输出契约

最终必须产出 `/app/output/result.json`，顶层至少包含：

| 字段 | 要求 |
|-|-|
| `case_id` | 输入标识（来自 case.json 或 task.md） |
| `status` | `ok \| partial \| insufficient_evidence \| input_error`，必须诚实 |
| 题目 output_contract 字段 | 原样回填全部键，缺值给 `null`/`[]`，不改名、不删未知键 |
| `results` | 每条候选的筛选与排序结果，按最终顺序，字段见下 |
| `evidence` | 扁平化证据记录（record_id + criterion_id + locator + 逐字 quote） |
| `dedupe_groups` / `conflicts` | 去重聚类裁定 / 冲突记录 |
| `sources` / `uncertainty` / `pending_human` / `errors` / `degradations` / `omissions` | 来源、不确定性、待人工、异常、降级、遗漏 |
| `change_log` / `runtime` | 逐步改动记录 / 运行时间 |

每条 `results[i]` 必须含：`record_id`、`title`、`authors`、`year`、`venue`、`doi`、`url`、`source_type`、`relevance`（direct/indirect/irrelevant/unknown）、`tier`（retain/pending/exclude）、`priority_score`（0–1）、`rank`、`reasons`、`evidence`（每项 locator + 逐字 quote）、`confidence`、`criteria_matched`、`conflict`、`dedupe`。

另建议产出 `/app/output/screening_report.md`（排序表与逐条理由），便于人工复核；若出图/表须附真实渲染 PDF/PNG 预览。

## 5. 执行流程（预算：900 秒、64 次模型请求，每 case 一次）

**铁律：前 3 分钟内必须落盘一版合法 result.json 骨架（`discover` 自动完成），之后只做增量改进；任何中途异常都不能留下空产物。**

| 步骤 | 动作 | 输入 → 输出 | 预算 |
|-|-|-|-|
| 0 | 定位 skill 根目录，读本 SKILL.md 与 `references/criteria.md` | — | ≤1 请求 |
| 1 | `python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output` | case.json/task.md → result.json 骨架 + `case_normalized.json` + `plan.json` | ≤60s，≤1 请求 |
| 2 | 读 `case_normalized.json` 与 `plan.json`，确认科研问题/候选数/纳排标准；空候选则 status=insufficient_evidence 并写 omissions，直接进步骤 6 | — | ≤1 请求 |
| 3 | `python3 -B scripts/main.py scaffold --work /app/work --output /app/output` | normalized → `scaffold.json`（词面先验、去重聚类）+ `evidence_ledger.json` 模板 | ≤30s，0 请求 |
| 4 | **逐条判断**：读每条候选标题/摘要（及材料 PDF），按 `references/criteria.md` 填 `evidence_ledger.json` 的 relevance/tier/priority_score/reasons/evidence（locator+逐字 quote）；3–5 条/请求批量 | 材料 → ledger | ≤480s，≤40 请求 |
| 5 | 裁定 dedupe（preprint vs published、同一研究多报道）、冲突与平局：填 `conflict`、`dedupe_override`、写 k 与平局规则 | — | ≤90s，≤6 请求 |
| 6 | `python3 -B scripts/main.py finalize --output /app/output --work /app/work` 校验覆盖、字段、证据与 status；据 selfcheck 修复后重跑 | ledger → result.json + `selfcheck.json` | ≤90s，≤6 请求 |
| 7 | 写 `screening_report.md`（可选但建议），回填 sources/uncertainty/change_log/runtime | — | ≤120s，≤8 请求 |

步骤 4 是主体：`relevance`/`tier`/`priority_score`/`reasons`/`evidence` 只能来自材料，`scaffold.json` 的 `base_score` 仅是词面先验，**不得**直接当最终排序分数。

## 6. 判定标准与自检

- 判定口径见 `references/criteria.md`（三级相关性、三档 tier、0–1 打分、去重/冲突、证据要求）。case.json 自带标准**优先级最高**。
- 自检清单见 `references/checklist.md`，逐条对应评分维度。交付前运行 `python3 -B scripts/main.py selftest`（25 项全过），`finalize` 必须 `errors=0`；warning 逐条修复或如实说明。
- 确定性平局键（`plan.json#tie_break`）：`tier_rank asc, priority_score desc, metadata_completeness desc, year desc, norm_title asc, record_id asc`；相同输入两次运行结果一致。

## 7. 降级策略（严禁编造）

- **空输入/空候选**：退出码 0，写 `insufficient_evidence` + omissions，不生成示例文献。
- **坏 JSON/坏编码**：捕获写 `errors`/`degradations`，用 task.md 文本继续，其余材料照常处理。
- **缺摘要/年份/DOI**：对应字段 `null`，`relevance=unknown`、`tier=pending`、`confidence=low`，写 `uncertainty`，不猜测。
- **缺软件/权限/接口异常**：`status` 降级 `partial` 或 `input_error`，`errors` 记录，`pending_human` 说明待人工。
- **PDF 无法解析**：先 `pdftotext` 重试，仍失败 `degradations` 记 `not_reported`，不依据文件名/领域标签补造内容。
- 任何结论、数值、DOI、引用必须来自材料；无据写 `pending_human` 或 `not_reported`。

## 8. 脚本清单（相对本 SKILL.md 所在目录；仅标准库，Python 3）

| 脚本 | 用途 |
|-|-|
| `scripts/main.py` | 薄入口：discover / scaffold / finalize / selftest / 默认全流程（<30KB） |
| `scripts/core_impl.py` | 实现：输入发现、规范化、计划编译、词面先验、去重、校验合并 |
| `scripts/selftest.py` | 自检：合成夹具（明确标注为夹具）跑全链路，25 项断言 |

若执行环境 cwd 不是 skill 根目录，先 `cd` 到 SKILL.md 所在目录再运行相对路径；`python3` 不可用回退 `python`。`--input/--work/--output` 默认 `/input`、`/app/work`、`/app/output`。

最小调用示例（示意输出须标注"示例"）：

```bash
python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output
python3 -B scripts/main.py scaffold --work /app/work --output /app/output
# Agent 依据材料与 references/criteria.md 填写 /app/work/evidence_ledger.json
python3 -B scripts/main.py finalize  --work /app/work --output /app/output
```

示意 result.json 片段（示例，非正式案例结果）：

```json
{"case_id": "example_case", "status": "partial",
 "results": [{"record_id": "rec_0000", "title": "...", "relevance": "direct",
   "tier": "retain", "priority_score": 0.93, "rank": 1,
   "reasons": ["研究对象、方法与结局均匹配纳入条件 c01"],
   "evidence": [{"locator": "case.json#papers[0].abstract", "quote": "..."}],
   "confidence": "high", "criteria_matched": ["c01"], "conflict": null, "dedupe": null}],
 "sources": [], "uncertainty": [], "change_log": [{"ts": "2026-01-01T00:00:00Z", "step": "discover", "action": "skeleton"}]}
```

**限制与维护**：本 Skill 面向有限快照候选集（如固定 9 篇）的逐条筛选与排序，不声称全局召回、不接入实时数据库、不声称真实人工复核。维护时更新 `references/criteria.md` 的判定口径与 `core_impl.py` 的字段发现键名即可。
