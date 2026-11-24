---
name: literature-screening-ranking
description: 对给定候选文献集做可复核的相关性判断、分层筛选与稳定可解释排序，处理重复/版本冲突与信息缺失，并为每条决定保留逐字证据。当需要从一批候选论文中筛选与科研问题相关的文献并排序、去噪、辅助人工复核时使用。
version: 1.1.0
---

# 文献筛选与智能排序

## 1. 角色与何时使用

本 Skill 把"候选文献 → 逐条相关性判断 → 分层筛选 → 稳定可解释排序 → 可复核结果"沉淀为可重复调用的流程。

**何时使用**：输入给出科研问题、一批候选文献（标题/作者/年份/摘要/DOI）和纳排标准，需要判断相关、排除噪声、去重、排序并交付可复核结果。**不适用**：底层数据库接入、全文精读、写作、系统综述完整纳排。

**做对的标准**：(1) 相关性标签与可复核标注标准一致；(2) 关键文献排到用户真正会看的位置；(3) 每条保留/待定/排除决定都能被他人复核。每条决定必须可解释、可溯源，不依赖黑箱打分。

## 2. 输入契约

- 运行时原始输入在 `/input/`：`case.json`（任务、候选文献、纳排标准、可能的 `output_contract`）与 `task.md`（题目说明）。
- 文献材料按题目提供：`/input/materials/` 或 `/input/` 下的 PDF / md / jsonl / txt（PDF 无预抽取答案层，须实际读取）。
- 工作目录 `/app/work/`（中间产物）；最终产物写 `/app/output/`。
- **先发现，再假设**：不假设固定键名。`discover` 自动发现 case.json / task.md / 材料，并从 case.json 提取科研问题、候选文献、纳排标准与 `output_contract`。
- 文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 降级）；单文件 >2MB 跳过并记入 `degradations`。

## 3. 输出契约

必须产出 `/app/output/result.json`，顶层至少包含：

| 字段 | 要求 |
|-|-|
| `case_id` | 输入标识（来自 case.json 或 task.md） |
| `status` | `ok` / `partial` / `insufficient_evidence` / `input_error`，必须诚实 |
| 题目 `output_contract` 字段 | 原样回填全部键，缺值给 `null`/`[]`，不改名、不删未知键 |
| `results` | 每条候选的筛选与排序结果（按最终顺序） |
| `dedupe_groups` | 重复/版本变体聚类与 canonical 裁定 |
| `sources` | 输入文件来源清单（path/kind/role） |
| `uncertainty` / `pending_human` | 逐条不确定性、待人工项 |
| `change_log` / `errors` / `degradations` / `warnings` / `omissions` / `runtime` | 逐步改动、异常、降级、遗漏、运行记录 |

每条 `results[i]` 必须含：`record_id`、`title`、`authors`、`year`、`venue`、`doi`、`url`、`source_type`、`relevance`（direct/indirect/irrelevant/unknown）、`tier`（retain/pending/exclude）、`priority_score`（0–1）、`rank`、`reasons`、`evidence`（每项含 `locator` + 逐字 `quote`）、`confidence`、`criteria_matched`、`conflict`、`dedupe`。另建议产出一份可读的 `/app/output/screening_report.md`。

## 4. 执行流程（预算：900 秒、64 次模型请求，每 case 一次）

**铁律：前 3 分钟内先落盘一版合法 partial 骨架，之后只做增量改进；任何中途异常都不能留下空产物。**

| 步骤 | 动作 | 输入 → 输出 | 预算 |
|-|-|-|-|
| 0 | 读本 SKILL.md 与 `references/criteria.md` | — | ≤1 请求 |
| 1 | `python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output` | 输入 → `result.json` 骨架 + `case_normalized.json` | ≤60 秒，≤1 请求 |
| 2 | 读骨架与 normalized，确认问题/候选数/纳排标准；空候选/空目录 → `insufficient_evidence` + `omissions` 后直接跳步骤 6 | — | ≤1 请求 |
| 3 | `python3 -B scripts/main.py screen --work /app/work --output /app/output` | normalized → `scaffold.json`（确定性词面信号、去重聚类、基线排序） | ≤30 秒，0 请求 |
| 4 | **逐条判断**：读标题/摘要及材料，按 `references/criteria.md` 判 relevance/tier/priority_score，写 reasons + evidence（locator + 逐字 quote）；3–5 条/请求批量处理 | 材料 → 填 `result.json.results` | ≤480 秒，≤40 请求 |
| 5 | 裁定 dedupe（预印本 vs 正式版、同研究多报道）、冲突与平局；写 `dedupe_groups`、`conflict`、平局规则 | — | ≤90 秒，≤6 请求 |
| 6 | `python3 -B scripts/main.py check --input /input --work /app/work --output /app/output` 校验覆盖、字段、证据溯源与 status 诚实性 | result.json → `selfcheck.json` | ≤90 秒，≤6 请求 |
| 7 | 写 `screening_report.md`，回填 sources/uncertainty/change_log/runtime，最终落盘 | — | ≤120 秒，≤8 请求 |

步骤 4 是主体：`relevance`/`tier`/`priority_score`/`reasons`/`evidence` 只能来自材料，不得由脚本猜。`scaffold.json` 的 `base_score` 仅是词面先验，**不得**直接当最终排序分数。`python3` 不可用则回退 `python`；脚本路径用相对路径 `scripts/main.py`（先 `cd` 到 SKILL.md 所在目录）。

## 5. 判定与自检清单

判定口径见 `references/criteria.md`（三级相关性、三档 tier、0–1 打分、去重/冲突/平局规则、证据要求）；`case.json` 自带标准为最高优先级。交付前逐条核对（完整版见 `references/checklist.md`）：

- [ ] A 实际效果：覆盖全部候选；每条有 relevance/tier/score/rank；标签与标准一致；元数据不猜测；direct 靠前；exclude 只放无关/已裁重复；去重有 canonical；每条有 reasons+逐字 evidence。
- [ ] B 使用增益：retain→pending→exclude 稳定；pending 尽量小、理由免重读全文；change_log 可复现同排序。
- [ ] C 创新增益：三档分层落地；冲突/版本/分歧有规则并执行；词面先验与语义判断分离、排序由规则+证据支撑。
- [ ] D 可靠体验：三步命令可独立跑通；同输入两次逐字节一致；空候选/缺字段/标准冲突/大量同分/接口异常被识别。
- [ ] E 生态贡献：文档齐全、资源可复用、示意输出标注「示例」。

## 6. 降级策略（严禁编造）

- 空输入/空候选：退出码 0，`status=insufficient_evidence` + `omissions`，不生成示例文献。
- 坏 JSON/坏编码：捕获记入 `errors`/`degradations`，用 task.md 文本继续，其余材料照常处理。
- 缺摘要/年份/DOI：对应字段 `null`，`relevance=unknown`、`tier=pending`、`confidence=low`，写入 `uncertainty`。
- 缺软件/权限/接口异常：`status` 降级 `partial` 或 `input_error`，`errors` 记录，`pending_human` 说明待人工。
- PDF 无法解析：先用 `pdftotext` 重试，仍失败则 `degradations` 记 `not_reported`，不依据文件名/领域标签补造内容。
- 任何结论、数值、DOI、引用必须来自材料；无据写 `pending_human` 或 `not_reported`。

## 7. 脚本清单（相对本 SKILL.md 目录；仅标准库，Python 3）

| 脚本 | 用途 |
|-|-|
| `scripts/main.py` | 单入口：`discover` 输入发现+骨架、`screen` 词面信号+去重、`check` 契约校验+证据溯源 |
| `scripts/selftest.py` | 自检：管线/确定性/空输入/坏 JSON 四种场景（示例数据显式标注） |

## 8. 最小调用示例（示意输出须标注「示例」）

```bash
python3 -B scripts/main.py discover --input /input --work /app/work --output /app/output
python3 -B scripts/main.py screen   --work /app/work --output /app/output
# Agent 依据材料填写 result.json 的 results（示例，非正式案例结果）
python3 -B scripts/main.py check    --input /input --work /app/work --output /app/output
python3 -B scripts/selftest.py
```

示意 result.json 片段（示例，非正式案例结果）：

```json
{"case_id": "example_case", "status": "partial",
 "results": [{"record_id": "rec_0000", "title": "...", "relevance": "direct",
   "tier": "retain", "priority_score": 0.93, "rank": 1,
   "reasons": ["研究对象、方法与结局均匹配纳入条件 X（示例）"],
   "evidence": [{"locator": "case.json#papers[0].abstract", "quote": "..."}],
   "confidence": "high", "criteria_matched": ["X"], "conflict": null, "dedupe": null}],
 "sources": [], "uncertainty": [],
 "change_log": [{"ts": "2026-01-01T00:00:00Z", "step": "discover", "action": "skeleton"}]}
```

## 9. 边界与维护

本 Skill 面向有限快照候选集（如固定 9 篇）的逐条筛选与排序，不声称全局召回、不接入实时数据库、不声称真实人工复核。维护时更新 `references/criteria.md` 判定口径与 `scripts/main.py` 的字段发现键名即可。
