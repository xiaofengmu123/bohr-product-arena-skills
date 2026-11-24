---
name: academic-literature-search
description: >-
  面向科研问题的可复核学术文献检索 Skill：以结构化中间产物（case_summary / 查询计划 / 候选记录 /
  证据 / 打分 rubric / 内容指纹 manifest）为唯一事实源，完成多轮查询规划、术语扩展、跨库候选召回、
  去重与元数据核验、透明打分与证据溯源，最终交付可逐条复核的 result.json。当需要围绕研究问题检索、
  筛选、排序文献并保留筛选依据时使用。
version: 1.1.0
---

# 学术文献检索 Skill（中间表示与可复核版）

## 1. 角色与何时使用

本 Skill 把“研究问题 → 结构化查询规划 → 候选召回 → 去重核验 → 透明排序 → 可复核交付”固化为可重复、可复现的流程。适用：围绕主题/方法/研究对象检索开放学术文献，交付候选集、排序/筛选依据与遗漏说明。不适用：单篇全文解析、写作润色、资料库整理。

核心原则：**不编造文献、数值、DOI、元数据或来源**；无材料写“材料不足”，绝不示例填充。区别于一次性脚本，本 Skill 把每一步中间产物落成结构化 JSON（`case_summary.json`、`plan.json`、`records.json`、`evidence.json`、`rubric.json`、`manifest.json`），使检索过程可被第三方逐层复核、重复运行结果一致。

## 2. 输入契约

- 原始输入位于 `/input/`：`case.json`（案例任务、约束、可能含 `output_contract`）与 `task.md`（题目说明）；可能另有 `materials/`（PDF、Crossref/EuropePMC 检索响应 JSON 等科研材料）。
- 不假设固定键名：先扫描 `/input/` 全部文件再解析；文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 均降级不崩溃）；单文件超过 2MB 跳过并记录到 `degradations`。
- 工作目录 `/app/work/`，最终产物写入 `/app/output/`。
- 若 `case.json` 含 `output_contract`（或 `output`/`schema`/`required_fields` 等），其**全部字段必须原样回填**进 result.json：未填的给 `null` 或 `[]`，不改名、不删键。

## 3. 输出契约（`/app/output/result.json`）

必须产出 `/app/output/result.json`，字段如下（`*` 为强制）：

- `case_id`*：案例标识，取自输入；
- `status`*：`ok | partial | insufficient_evidence | input_error`；
- `results`*：候选文献列表，每条含 rank / record_id / title / authors / year / journal / doi / url / database / relevance / relevance_reason / score / score_breakdown / evidence（locator+quote）/ dedup / provenance；
- `evidence`*：查询、筛选、排序、追踪依据（kind/query/source/database/timestamp/rationale/locator/quote/query_id）；
- `sources`*、`uncertainty`*（missing_metadata/conflicts/coverage_gaps/notes）、`status_notes`*、`pending_human`*、`change_log`*（time/action/reason/status）；
- `research_question`、`search_scope`、`errors`、`degradations`、`limitations`、`runtime`；
- 题目 `output_contract` 的**全部字段**（未获得的回填 `null`/`[]`）。

脚本还会生成 `/app/output/candidates.md`（由 result.json 渲染的可编辑候选清单）与 `/app/output/manifest.json`（中间产物 SHA-256 指纹链）。本任务无 Office/图件硬性要求，不生成 PDF/PNG；若案例另行要求再按需产出。

## 4. 执行流程（预算：总 900 秒、64 次模型请求、单响应 ≤16384 tokens）

先落盘骨架，再增量改进；**任何时刻中途失败，都必须保证 `/app/output/result.json` 合法存在**。步骤 0 必须在前 3 分钟完成。

| 步骤 | 内容 | 输入 | 输出 | 时间预算 |
|-|-|-|-|-|
| 0 骨架 | `python3 scripts/main.py run --input /input --output /app/output --work /app/work`（parse→plan→assemble，不检索） | /input/ | result.json(partial) + case_summary + plan | ≤3 分钟 |
| 1 解析 | 读 case.json/task.md，确认研究问题、约束、output_contract 键、材料清单 | /input/ | case_summary.json | 并入步骤 0 |
| 2 规划 | 结构化查询计划 + 术语扩展核验清单（同义词/缩写/跨语言） | case_summary | plan.json | 2–5 分钟 |
| 3 检索 | 用 paper_search / web_search 执行召回（单概念→AND 组合→引文追踪），并读取 materials 中的 Crossref/EuropePMC 响应 | plan | 候选记录 | ≤20 分钟 |
| 4 导入 | `python3 scripts/main.py import --inline '{...}' --work /app/work --query-id Q1 --label ...` | 候选 | records.json + import_state.json | 每次秒级 |
| 5 证据 | `python3 scripts/main.py evidence --inline '{...}' --work /app/work` 记录查询与来源 | 查询日志 | evidence.json | 随检索进行 |
| 6 复核 | 校验元数据/去重/相关性依据（标题、作者、年份、期刊、DOI） | records | 去重后 records | 5 分钟 |
| 7 组装 | `python3 scripts/main.py assemble --input /input --output /app/output --work /app/work` | 全部 | /app/output/result.json + candidates.md | ≤1 分钟 |
| 8 自检 | `python3 scripts/main.py selfcheck --output /app/output --work /app/work` | result.json | PASS/FAIL 清单 | ≤1 分钟 |

原则：步骤 0 必须先落盘一版 `status=partial` 骨架，此后每一步通过 `assemble` 更新同一份 result.json，只做增量改进。时间分配建议：检索 40%、复核 30%、规划 15%、组装与自检 10%、缓冲 5%。64 次模型请求用于检索与相关性判断；脚本是确定性辅助，不消耗模型请求。

## 5. 自检清单（对应评分维度）

- [ ] **实际效果**：results 每条均有真实标题 + 来源（DOI 或 URL）+ 相关性依据（locator+quote）；元数据尽量齐全；覆盖主要研究方向并说明遗漏（coverage_gaps）。
- [ ] **使用增益**：结构化查询规划与术语扩展核验清单提高 Recall；去重与透明排序减少人工筛选；evidence 可复现每次查询。
- [ ] **创新增益**：中间产物指纹链（manifest.json）、透明打分（0.6·relevance + 0.2·元数据完整 + 0.2·来源可靠）、增量导入状态均由脚本固化、可复现。
- [ ] **可靠体验**：`python3 scripts/main.py --help` 退出码 0；空输入/坏 JSON 不崩溃；重复运行一致；异常写入 errors/degradations；`python3 scripts/selftest.py` 全 PASS。
- [ ] **生态贡献**：SKILL.md + scripts + references 齐全，纯标准库；他人可按说明独立复现一次完整检索。

## 6. 降级策略

- 缺数据：候选为空 → `status=insufficient_evidence`，在 `status_notes`/`limitations` 写明缺失与下一步检索建议，**不补造文献**。
- 缺软件/接口：检索工具不可用、限流、权限不足 → 记录到 `errors`/`degradations`，改用本地材料（PDF/materials 检索响应）补充证据并标注局限。
- 元数据缺失/来源冲突：缺失标 `null` 并列入 `uncertainty.missing_metadata`；同一指标多来源不一致时全部记录并标 `source_conflict`，不擅自取舍。
- 无法判断的字段写 `pending_human`，不猜测。

## 7. 脚本（纯标准库，相对路径调用）

在 Skill 根目录（运行时挂载于 `/skills/weekly-skill`，先 `cd /skills/weekly-skill`）执行：

```bash
python3 scripts/main.py --help
python3 scripts/main.py run --input /input --output /app/output --work /app/work
python3 scripts/main.py import --inline '[{"title":"...","doi":"..."}]' --work /app/work --query-id Q1 --database crossref
python3 scripts/main.py evidence --inline '{"kind":"query","query":"...","source":"crossref","database":"crossref","rationale":"...","query_id":"Q1"}'
python3 scripts/main.py assemble --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck --output /app/output --work /app/work
python3 scripts/selftest.py
```

候选记录字段见 `references/schema_and_rubric.md`；自检清单与降级策略见 `references/checklist.md`。以上命令为示意，非正式案例结果。
