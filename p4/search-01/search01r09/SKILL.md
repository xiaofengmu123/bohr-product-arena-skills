---
name: traceable-literature-search
description: >-
  面向科研问题的可追溯开放学术文献检索 Skill：多轮查询规划、关键词扩展、跨库召回、
  元数据核验、去重与透明排序，并逐条保留来源与相关性依据，最终交付可复核的 result.json。
  遇到“围绕研究问题检索/筛选/排序文献并保留筛选依据”的任务时使用。
version: 2.0.0
---

# 可追溯学术文献检索 Skill

## 1. 角色与使用时机

把“研究问题 → 查询规划 → 关键词扩展 → 跨库召回 → 去重核验 → 排序 → 可复核交付”固化为可重复流程，供 Agent 在固定科研案例上运行。适用：围绕主题/方法/研究对象检索开放学术文献，交付候选集、排序/筛选依据与遗漏说明。不适用：单篇论文全文解析、写作润色、资料库整理。

核心约束：**不编造文献、数值、DOI、元数据或来源**；没有材料就写“材料不足”，绝不示例填充。

## 2. 输入契约

- 运行时原始输入位于 `/input/`：`case.json`（案例任务、约束，可能含 `output_contract`）与 `task.md`（题目说明）；可能另有 `materials/`（PDF 等科研材料）。
- 先扫描 `/input/` 全部文件再解析，不假设固定文件名或键名；文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 均降级不崩溃）；单文件 >2MB 跳过并记录到 `degradations`。
- 工作目录 `/app/work/`，最终产物写入 `/app/output/`。
- `case.json` 含 `output_contract`（或 `output`/`schema`/`required_fields` 等）时，其**全部字段**必须原样回填进 result.json：未得到的填 `null`/`[]`，不改名、不删键。

## 3. 输出契约

必须产出 `/app/output/result.json`（`*` 为强制）：

- `case_id`*（取自输入）；`status`*：`ok|partial|insufficient_evidence|input_error`。
- `results`*：候选文献列表；每条含 rank/title/authors/year/journal/doi/url/database/relevance/relevance_reason/score/evidence（每项 `locator`+`quote` 逐字溯源）。
- `evidence`*：查询与追踪依据（query/source/database/kind/rationale/timestamp/scope）。
- `sources`*、`status_notes`*、`uncertainty`*（missing_metadata/conflicts/coverage_gaps/notes）。
- `pending_human`*、`change_log`*（time/action/reason/status）。
- 其它：`research_question`、`search_scope`、`limitations`、`errors`、`degradations`、`runtime`。
- 题目 `output_contract` 的**全部字段**回填。
- 同时生成 `/app/output/candidates.md`（可编辑候选清单）。本题无 Office/图件硬性要求；若案例要求，再按需产出（Office 需配真实 PDF/PNG 预览）。

## 4. 执行流程（预算：900 秒、64 次模型请求、单次响应 ≤16384 token）

**先落盘骨架，再增量改进**：前 3 分钟必须产出 `status=partial` 的合法 result.json；此后每一步只更新同一份文件。任何时刻中途失败也必须保证 result.json 合法存在。

| 步骤 | 行为 | 输入 | 输出 | 时间 |
|-|-|-|-|-|
| 0 骨架 | `python3 scripts/main.py run` | /input/ | result.json(partial)+case_summary+plan | ≤3 分钟 |
| 1 解析 | 读 case.json/task.md，确认研究问题、约束、契约键 | /input/ | case_summary.json | 并入 0 |
| 2 规划 | 多轮查询规划 + 关键词扩展核验清单 | case_summary | plan.json | 2–5 分钟 |
| 3 检索 | paper_search/web_search 召回（概念种子→同义/缩写→AND 组合→引文追踪） | plan | 候选记录 | ≤20 分钟 |
| 4 导入 | `python3 scripts/main.py import --file /app/work/candidates_in.json` | 候选 | records.json | 每次秒级 |
| 5 证据 | `python3 scripts/main.py evidence --inline '{...}'` 记录查询与来源 | 检索日志 | evidence.json | 随检索 |
| 6 复核 | 核验元数据/去重/相关性依据（标题、作者、年份、期刊、DOI；冲突不取一） | records | 去重 records | 5 分钟 |
| 7 组装 | `python3 scripts/main.py assemble` 写 result.json+candidates.md | 全部 | /app/output/result.json | ≤1 分钟 |
| 8 自检 | `python3 scripts/main.py selfcheck` | result.json | PASS/FAIL | ≤1 分钟 |

时间建议：检索 40%、复核 30%、规划 15%、组装与自检 10%、缓冲 5%。64 次模型请求全部用于检索与相关性判断；脚本为确定性辅助，不占请求。

## 5. 自检清单（对应评分维度）

- [ ] 实际效果：每条 results 有真实 title+来源（DOI/URL）+relevance_reason+evidence(locator+quote)；元数据尽量齐全；coverage_gaps 说明遗漏方向。
- [ ] 使用增益：多轮查询规划 + 关键词扩展提高 Recall；去重减少人工筛选；evidence 可复现每次查询。
- [ ] 创新增益：查询规划、关键词扩展清单、跨库去重、透明打分（0.6·relevance+0.2·元数据完整+0.2·来源可靠）均由脚本固化。
- [ ] 可靠体验：`python3 scripts/main.py --help` 退出 0；空输入/坏 JSON 不崩溃；重复运行一致；异常写入 errors/degradations。
- [ ] 生态贡献：SKILL.md+scripts+references 齐全；他人可按 `scripts/selftest.py` 独立复现一次完整检索。

## 6. 降级策略

- 缺候选：结果 `insufficient_evidence`，写清缺失与下一步检索建议，不补造文献。
- 缺软件/接口限流/权限不足：写入 errors/degradations，改用已有 PDF/materials 证据并标注局限。
- 元数据缺失：该字段标 `null` 并列入 uncertainty.missing_metadata；来源冲突全部记录并标 source_conflict，不擅自取舍。
- 无法判断写入 pending_human。

## 7. 脚本调用（相对路径，先 `cd /skills/weekly-skill`）

```bash
python3 scripts/main.py --help
python3 scripts/main.py run --input /input --output /app/output --work /app/work
python3 scripts/main.py import --file /app/work/candidates_in.json --work /app/work
python3 scripts/main.py evidence --inline '{"query":"...","source":"paper_search","database":"crossref","rationale":"..."}' --work /app/work
python3 scripts/main.py assemble --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck --output /app/output --work /app/work
python3 scripts/selftest.py
```

字段定义见 `references/result-schema.md`；查询构造与去重规则见 `references/query-and-dedup.md`。以上为接口示意，非正式案例结果。

## 8. 最小调用示例（示例，非正式案例）

示意输入 `/app/work/candidates_in.json`：

```json
[
  {
    "title": "Graph neural networks for protein interaction prediction",
    "authors": ["A. Smith", "B. Jones"],
    "year": 2021, "journal": "Bioinformatics",
    "doi": "10.1000/xyz123", "url": "https://doi.org/10.1000/xyz123",
    "database": "crossref", "relevance": 0.9,
    "relevance_reason": "直接讨论研究问题",
    "evidence": [{"locator": "paper_search#1", "quote": "GNN-based PPI prediction"}]
  }
]
```

示意调用（在 `/skills/weekly-skill` 下）：

```bash
python3 scripts/main.py import --file /app/work/candidates_in.json --work /app/work
python3 scripts/main.py assemble --input /input --output /app/output --work /app/work
```

示意产物 `/app/output/result.json`：`{"case_id":"...","status":"ok","results":[...],...}`；同时生成 `candidates.md`。以上为接口示意，非正式案例结果。