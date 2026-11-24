---
name: academic-literature-search
description: >-
  面向科研问题的开放学术文献检索与可复核交付 Skill。把「研究问题 → 多轮查询规划 →
  关键词扩展 → 跨库召回 → 去重核验 → 透明排序 → 证据溯源」固化为可重复流程，
  并对“降级与不确定性”做结构化显式化：缺数据/缺软件按降级阶梯处理并如实写入
  limitations/uncertainty，绝不编造文献、数值、DOI 或来源。当需要围绕研究问题
  检索、筛选、排序文献并保留逐字筛选依据时使用。
version: 1.1.0
---

# 学术文献检索 Skill

## 1. 角色与何时使用

本 Skill 把科研检索固化为可重复、可复核、可增量改进的最小流程。适用：围绕主题/方法/研究对象检索开放学术文献，交付候选集、排序依据、证据溯源与遗漏说明。不适用：单篇论文全文解析、写作润色、资料库整理。

核心原则（不可违反）：**不编造文献、数值、DOI、元数据或来源**；无材料就写 `insufficient_evidence`，绝不示例填充；每条正式结果必须有可独立复核的逐字 `quote` 与定位 `locator`。

## 2. 输入契约

- 原始输入位于 `/input/`：`case.json`（案例任务、约束、可能含 `output_contract`）与 `task.md`（题目说明）；可能另有 `materials/`（PDF 等科研材料）。PDF 解析收到的是真实 PDF，须读取实际文件而非按文件名臆测内容。
- **先扫描 `/input/` 全部文件再解析**，不假设固定键名；文本按 `utf-8-sig`→`utf-8`→`gb18030`→`latin-1` 顺序容错，坏编码/坏 JSON 均降级并记录，不崩溃；单文件超 2MB 跳过并记录。
- 运行时工作目录 `/app/work/`，最终产物写 `/app/output/`。
- 若 `case.json` 含 `output_contract`（或 `output`/`schema`/`required_fields`/`contract` 等键），其**全部字段必须原样回填**进 result.json：未获得的给 `null`/`[]`，不改名、不删键。

## 3. 输出契约（/app/output/result.json）

必须产出 `/app/output/result.json`，顶层含（`*` 为强制）：

- `case_id`*、`status`*（`ok|partial|insufficient_evidence|input_error`）、`research_question`、`search_scope`；
- `results`*：候选文献列表，每条含 rank/record_id/title/authors/year/journal/doi/url/database/relevance/relevance_reason/score/verification_status/meta_verified/meta_confidence/evidence（locator+quote）；
- `evidence`*：三层溯源（query/source/relevance，relevance 必带 locator+quote）；
- `sources`*、`uncertainty`*（missing_metadata/conflicts/coverage_gaps/verification/notes）、`limitations`*、`pending_human`*、`errors`*、`degradations`*、`status_notes`*、`change_log`*、`runtime`*；
- 题目 `output_contract` 的**全部字段**（未获得的回填 `null`/`[]`）。

脚本还生成 `/app/output/candidates.md`（可编辑候选清单）。本任务不要求 PDF/PNG；若案例另行要求再按需产出。

完整字段语义见 `references/result_schema.md`；降级与不确定性规则见 `references/uncertainty_ledger.md`。

## 4. 执行流程（预算：总 900 秒、64 次模型请求）

**先落盘骨架，再增量改进；任何时刻中途失败，都必须保证 `/app/output/result.json` 合法存在。**

| 步骤 | 内容 | 输入 → 输出 | 时间预算 |
|-|-|-|-|
| 0 骨架 | `python3 scripts/main.py run`（skeleton→parse→plan→assemble-skeleton） | /input/ → result.json(partial)+case_summary/plan | **前 3 分钟内** |
| 1 解析 | 读 case.json/task.md，确认研究问题、约束、output_contract 键 | /input/ → case_summary.json | 并入步 0 |
| 2 规划 | 多轮查询阶梯 + 关键词扩展核验清单 | case_summary → plan.json | 2–5 分钟 |
| 3 检索 | paper_search / web_search 召回：单概念 → 关键词扩展 → AND 组合 → 引文追踪 | plan → 候选 | ≤20 分钟 |
| 4 导入 | `python3 scripts/main.py import --file /app/work/candidates_in.json` | 候选 → records.json | 秒级/次 |
| 5 证据 | `python3 scripts/main.py evidence --inline '{"kind":"query",…}'` | 查询日志 → evidence.json | 随检索 |
| 6 不确定性 | `python3 scripts/main.py uncertainty --inline '{"coverage_gaps":[…]}'` | 缺口 → uncertainty.json | 随检索 |
| 7 复核 | 元数据核验/去重/相关性依据（标题/作者/年份/期刊/DOI） | records → 去重后 records | 5 分钟 |
| 8 组装 | `python3 scripts/main.py assemble` 写 result.json + candidates.md | 全部 → /app/output/ | ≤1 分钟 |
| 9 自检 | `python3 scripts/main.py selfcheck`；亦可运行 `python3 scripts/selftest.py` | result.json → PASS/FAIL | ≤1 分钟 |

原则：步 0 必须在前 3 分钟落盘 status=partial 骨架；此后仅通过 import/evidence/uncertainty/assemble 增量更新同一份 result.json。建议时间分配：检索 40%、复核 30%、规划 15%、组装自检 10%、缓冲 5%。脚本是确定性辅助，不消耗模型请求；64 次模型请求全部用于检索与相关性判断。

## 5. 自检清单（对应评分维度）

- [ ] **实际效果**：results 每条有真实标题+来源（DOI/URL）+逐字 quote；作者/年份/期刊/DOI 尽量齐全；覆盖主要研究方向并说明遗漏（coverage_gaps 带 next_query）。
- [ ] **使用增益**：多轮查询规划+关键词扩展提高 Recall；去重+透明排序减少人工筛选；evidence 可复现每次查询；效率收益在 change_log 中可测量。
- [ ] **创新增益**：查询阶梯、关键词核验清单、跨库去重、透明打分（0.6·relevance+0.2·元数据完整+0.2·来源可靠）、证据闸门与不确定性账本均由脚本固化、可复现。
- [ ] **可靠体验**：`python3 scripts/main.py --help` 退出码 0；空输入/坏 JSON 不崩溃且写合法结果；重复运行结果一致；异常写入 errors/degradations。
- [ ] **生态贡献**：SKILL.md+脚本+references 齐全；他人可按本说明在独立环境复现一次完整检索（运行 `scripts/selftest.py` 验证）。

## 6. 降级策略（本版核心：局限性/不确定性显式化）

- 缺数据：候选为空 → `status=insufficient_evidence`，在 status_notes/limitations 写明缺失与下一步检索建议，**不补造**。
- 缺软件/接口：检索工具不可用、限流、权限不足 → 记 errors/degradations，改用本地材料（PDF/materials）补证据并标注局限。
- 元数据缺失/来源冲突：缺失标 `null` 并进 uncertainty.missing_metadata；同一指标多来源不一致时全部记录并标 `source_conflict`，不擅自取舍。
- **证据闸门**：缺 title/doi+url/locator/quote 任一者，记录不进 results，隔离到 pending_human 并说明原因。
- 无法判断的字段写 pending_human，不猜测。降级阶梯（全量→元数据缺→接口限流→无候选→输入坏）见 `references/uncertainty_ledger.md`。

## 7. 脚本（纯标准库，相对路径调用）

在 Skill 根目录（运行时挂载于 `/skills/weekly-skill`，先 `cd /skills/weekly-skill`）执行：

```bash
python3 scripts/main.py --help
python3 scripts/main.py run --input /input --output /app/output --work /app/work
python3 scripts/main.py import --file /app/work/candidates_in.json --work /app/work
python3 scripts/main.py evidence --inline '{"kind":"query","query":"...","source":"paper_search","database":"crossref","rationale":"..."}' --work /app/work
python3 scripts/main.py uncertainty --inline '{"coverage_gaps":[{"aspect":"引文追踪","gap":"...","next_query":"..."}]}' --work /app/work
python3 scripts/main.py assemble --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck --output /app/output --work /app/work
```

候选记录字段见 `references/result_schema.md`；查询构造、去重、排序与不确定性规则见 `references/result_schema.md` 与 `references/uncertainty_ledger.md`。

## 8. 最小调用示例（示意，非正式案例结果）

```bash
# 示意：一次完整检索（输入为示例，输出须自备真实证据）
printf '%s' '{"records":[{"title":"…","doi":"10.1001/jama.2016.17216","database":"crossref","relevance":0.95,"evidence":{"locator":"crossref:…","quote":"逐字标题片段"}}]}' > /app/work/candidates_in.json
python3 scripts/main.py import --file /app/work/candidates_in.json --work /app/work
python3 scripts/main.py assemble --input /input --output /app/output --work /app/work
```

以上数据为**接口示意**，不是正式案例结果。