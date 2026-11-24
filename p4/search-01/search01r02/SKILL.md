---
name: academic-literature-search
description: >-
  面向科研问题的开放学术文献检索 Skill：把「研究问题 → 多轮查询规划 → 关键词扩展 →
  跨库候选召回 → 去重与元数据核验 → 透明打分排序 → 证据溯源交付」固化为可重复流程，
  最终产出可复核的 result.json 与可编辑的 candidates.md。当需要围绕一个研究问题检索、
  筛选、排序文献并保留每条结果的来源与相关性依据时使用。
version: 2.0.0
---

# 学术文献检索 Skill

## 1. 角色与何时使用

本 Skill 面向科研检索任务：围绕主题/方法/研究对象检索开放学术数据库，交付候选集、
排序与筛选依据、遗漏说明。核心原则：**不编造文献、数值、DOI、元数据或来源**；
无材料就写“材料不足”，绝不示例填充。

适用：多轮查询规划、关键词扩展、跨库召回、去重核验、透明排序与证据溯源。
不适用：单篇论文全文解析、写作润色、资料库整理。

## 2. 输入契约（先发现，再假设）

- 原始输入位于 `/input/`：`case.json`（案例任务、约束、可能含 `output_contract`）
  与 `task.md`（题目说明）；可能另有 `materials/`（PDF 等科研材料）。
- 不假设固定键名：先扫描 `/input/` 全部文件再解析；文本按 `utf-8-sig` 读取并容错
  （BOM、坏编码、坏 JSON 均降级不崩溃），单文件超过 2MB 跳过并记录。
- 工作目录 `/app/work/`，最终产物写入 `/app/output/`。
- 若 `case.json` 含 `output_contract`（或 `output/schema/required_fields` 等），
  其全部字段必须原样回填进 result.json：有真实值的用真实值，未获得的给
  `null` 或 `[]`，不改名、不删未知键。

## 3. 输出契约（/app/output/result.json）

必须产出 `/app/output/result.json`（`*` 为强制）：

- `case_id`*、`status`*（`ok|partial|insufficient_evidence|input_error`）；
- `results`*：候选文献列表，每条含 rank/title/authors/year/journal/doi/url/database/
  relevance/relevance_reason/score/score_breakdown/evidence（locator+quote）；
- `evidence`*：查询、筛选、排序、追踪依据（query/source/database/timestamp/rationale/kind）；
- `status_notes`*、`sources`*、`uncertainty`*（missing_metadata/conflicts/coverage_gaps/notes）、
  `change_log`*（time/action/reason/status）；
- `research_question`、`search_scope`、`limitations`、`errors`、`degradations`、
  `pending_human`、`runtime`、`budget`；
- 题目 `output_contract` 的**全部字段**（未获得的回填 `null`/`[]`）。

脚本另生成 `/app/output/candidates.md`（由 result.json 确定性渲染的可编辑候选清单）。
本任务无 Office/图件硬性要求，不生成 PDF/PNG；若案例另行要求，再按需产出并附渲染预览。
字段细则见 `references/result_schema.md`。

## 4. 执行流程（预算：总 900 秒、64 次模型请求）

**总原则：前 3 分钟先落盘一版 status=partial 的合法结果骨架，此后每一步通过
`assemble` 重写同一份 result.json，只做增量改进；任何时刻中断，产物都合法存在。**
脚本是确定性辅助，不消耗模型请求；只有检索与相关性判断消耗模型请求。

| 阶段 | 动作 | 输入 → 输出 | 时间 | 请求 |
|-|-|-|-|-|
| A 骨架 | `scripts/main.py run`（skeleton→parse→plan→assemble） | /input/ → 骨架 result.json + case_summary/plan | ≤180s | 2 |
| B 规划 | 多轮查询规划 + 关键词扩展核验清单 | case_summary → plan.json | ≤90s | 3 |
| C 召回 | paper_search / web_search 执行（单概念→AND→同义词→引文） | plan → 候选记录 | ≤420s | 40 |
| D 核验 | `scripts/main.py import` 逐批导入；校验元数据/去重/相关性依据 | 候选 → records.json | ≤120s | 8 |
| E 组装 | `scripts/main.py assemble` 写 result.json + candidates.md | 全部 → /app/output/ | ≤60s | 1 |
| F 缓冲 | 兜底自检与收尾 | — | ≤30s | 10 |

请求登记：每次模型请求前执行 `scripts/main.py ledger --inline '{...}'`；脚本累计并
在超过 64 次时标记 `over_budget`，超过 76 次返回退出码 3 并提示密封当前结果。

常用命令（相对路径，先 `cd /skills/weekly-skill`）：

```bash
python3 scripts/main.py --help
python3 scripts/main.py run --input /input --output /app/output --work /app/work
python3 scripts/main.py import --file /app/work/candidates_in.json --work /app/work
python3 scripts/main.py evidence --inline '{"query":"...","source":"paper_search","database":"crossref","rationale":"..."}' --work /app/work
python3 scripts/main.py ledger --inline '{"phase":"recall","action":"paper_search","reason":"..."}' --work /app/work
python3 scripts/main.py assemble --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck --output /app/output --work /app/work
```

## 5. 自检清单（逐条对应评分维度）

- [ ] **实际效果（40 分）**：results 每条均有真实标题 + 来源（DOI/URL）+ 逐字 quote 证据；
  作者/年份/期刊/DOI 尽量齐全；覆盖主要研究方向并说明遗漏（coverage_gaps）。
- [ ] **使用增益（25 分）**：多轮查询规划与关键词扩展提高 Recall；去重与透明排序
  减少人工筛选；evidence 记录每次查询可复现，效率可通过请求台账核验。
- [ ] **创新增益（15 分）**：查询规划、关键词扩展核验清单、跨库去重、
  透明打分（0.6·relevance + 0.2·completeness + 0.2·provenance）均由脚本固化、可复现。
- [ ] **可靠体验（10 分）**：`scripts/main.py --help` 退出码 0；空输入/坏 JSON/缺输入
  不崩溃；重复运行 results/score/排序一致；异常写入 errors/degradations；
  超预算有停止信号。
- [ ] **生态贡献（10 分）**：SKILL.md + 脚本 + references 齐全；他人可按说明独立复现
  一次完整检索（`scripts/selftest.py` 可一键自检）。

提交前执行 `python3 scripts/selftest.py`，应全部 PASS；空目录、坏 JSON、缺输入三种
情况各跑一次。

## 6. 降级策略

- **缺数据**：候选为空 → `status=insufficient_evidence`，在 `status_notes`/`limitations`
  写明缺失与下一步检索建议，**不补造文献**。
- **缺软件/接口**：检索工具不可用、限流、权限不足 → 记录到 `errors`/`degradations`，
  改用已有本地材料（PDF/materials）补充证据并标注局限。
- **元数据缺失/来源冲突**：缺失标 `null` 并列入 `uncertainty.missing_metadata`；
  同一指标多来源不一致时全部记录并标 `source_conflict`，不擅自取舍。
- **无法判断**：写 `pending_human`，不猜测。
- **超预算**：`ledger` 返回停止信号后不再发起新检索，直接 `assemble` 密封当前结果，
  并在 `limitations` 说明“检索未完成即止于预算”。

## 7. 文件结构

```text
academic-literature-search/
├── SKILL.md                       # 本文件
├── scripts/
│   ├── main.py                    # CLI 入口（子命令分发）
│   ├── core_impl.py               # 核心确定性逻辑（IO/去重/打分/组装/自检）
│   └── selftest.py                # 一键自检（空输入/坏 JSON/确定性/契约）
└── references/
    ├── result_schema.md           # result.json 字段规范
    └── query_and_dedup.md         # 查询构造、去重、打分、排序规则
```

脚本仅依赖 Python 标准库，相对路径调用，可在 Linux/`python3` 环境直接运行。
