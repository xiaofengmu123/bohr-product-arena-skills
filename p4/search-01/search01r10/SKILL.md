---
name: source-bound-literature-search
description: >-
  面向科研问题的可溯源学术文献检索 Skill。在 /input 提供的真实材料与
  Crossref/EuropePMC/发行商响应上工作，执行多轮查询规划、关键词扩展、
  候选召回、去重核验与透明排序，把每次查询、每条候选与筛选依据计入单一
  版本化账本，并交付可复核的 result.json。当需要围绕研究问题检索、筛选、
  排序文献并保留筛选依据时使用。
version: 1.0.0
---

# 科研文献检索 Skill（source-bound）

## 1. 角色与何时使用

本 Skill 把「研究问题 → 查询规划 → 候选召回 → 去重核验 → 排序 → 可复核交付」
固化为可重复流程。核心原则：**不编造文献、数值、DOI、元数据或来源**；无材料
就写「材料不足」，绝不示例填充。适用：围绕主题/方法/研究对象检索开放学术文献，
交付候选集、排序/筛选依据与遗漏说明。不适用：单篇全文解析、写作润色、资料库整理。

## 2. 输入契约

- 原始输入位于 `/input/`：`case.json`（任务、约束，可能含 `output_contract`）
  与 `task.md`；可能另有 `materials/`（PDF 等）。**先扫描再解析**，不假设固定键名。
- 文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 均降级不崩溃），
  单文件超 2MB 跳过并记录。
- 本任务为 source-bound：检索命中来自平台提供的真实 Crossref/EuropePMC/发行商
  响应与材料，**不要**凭空声称 live 远程覆盖或全球最新文献；只依据实际读到的
  响应与材料落账。
- 工作目录 `/app/work/`，最终产物写 `/app/output/`。
- 若 `case.json` 含 `output_contract`（或 `output`/`schema`/`required_fields`
  等），其全部字段必须原样回填进 result.json：未获得的给 `null` 或 `[]`，
  不改名、不删键。

## 3. 输出契约（/app/output/result.json）

必须产出 `/app/output/result.json`，字段（`*` 强制）如下：

- `case_id`*、`status`*（`ok|partial|insufficient_evidence|input_error`）；
- `results`*：候选文献，每条含 rank/title/authors/year/journal/doi/url/database/
  relevance/relevance_reason/score/evidence（每项 evidence 含 locator + 逐字 quote）；
- `evidence`*、`sources`*、`uncertainty`*（missing_metadata/conflicts/coverage_gaps/notes）、
  `pending_human`、`change_log`*（time/action/reason/status/seq）；
- `research_question`、`search_scope`、`status_notes`、`limitations`、
  `errors`、`degradations`、`runtime`；
- 题目 `output_contract` 的**全部字段**（未获得的回填 `null`/`[]`）。

脚本同时生成 `/app/output/candidates.md`（由 result.json 渲染的可编辑清单，
便于人工复核）。本任务不要求 Office/图件；若案例另行要求，再按需产出并附
真实 PDF/PNG 预览。

## 4. 执行流程（总 900 秒、64 次模型请求；首 3 分钟先落骨架）

所有确定性工序由脚本完成，模型请求只用于检索与相关性判断。账本是唯一事实
来源，`result.json` 由账本派生，因此中途失败也始终有合法产物。

| 步骤 | 内容 | 输入 | 输出 | 预算 |
|-|-|-|-|-|
| 0 骨架 | `python3 scripts/main.py run`（skeleton→parse→plan→assemble） | /input/ | partial result.json + 账本 | **首 ≤3 分钟** |
| 1 解析 | 确认研究问题、约束、output_contract 键 | /input/ | 账本 case | 并入步 0 |
| 2 规划 | 多轮查询规划 + 关键词扩展核验清单 | 账本 | 账本 plan | 2–5 分钟 |
| 3 检索 | 用 paper_search/web_search 执行召回（单概念→AND→引用图） | plan | 命中记录 | ≤20 分钟 |
| 4 导入 | `python3 scripts/main.py import --file/--inline …`（排除项带 `excluded:true` + `exclusion_reason`） | 命中记录 | 账本 candidates/excluded | 秒级 |
| 5 证据 | `python3 scripts/main.py evidence --inline '{…}'` | 查询日志 | 账本 evidence | 随检索 |
| 5b 状态 | `python3 scripts/main.py note --inline '{status_notes/limitations/coverage_gaps/pending_human/search_scope…}'` | 人工判断 | 账本说明字段 | ≤1 分钟 |
| 6 复核 | 元数据核验/去重/相关性依据（标题、作者、年份、期刊、DOI） | 账本 | 去重后账本 | 5 分钟 |
| 7 组装 | `python3 scripts/main.py assemble` 写 result.json+candidates.md | 账本 | /app/output/ | ≤1 分钟 |
| 8 自检 | `python3 scripts/main.py selfcheck` | result.json | PASS/FAIL | ≤1 分钟 |

时间分配建议：检索 40%、复核 30%、规划 15%、组装与自检 10%、缓冲 5%。
**首 3 分钟内必须先落盘一版 `status=partial` 骨架**，之后每步只做增量改进，
任何异常都不得留下不完整产物。

## 5. 证据溯源（科研场景硬要求）

- 每个关键结论、数值、候选，都要能指回**逐字片段**（locator + quote）；
- 证据 locator 可用 `materials/xxx.md#p3` 或数据库响应字段定位；
- 冲突不隐藏：同一指标两来源不一致时两个都记录并标 `source_conflict`；
- 缺失标 `null` 并入 `uncertainty.missing_metadata`；判断不了写 `pending_human`。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] **实际效果**：results 每条均有真实标题+来源（DOI/URL）+相关性依据（locator+quote）；
  作者/年份/期刊/DOI 尽量齐全；覆盖主要方向并写明遗漏（coverage_gaps）。
- [ ] **使用增益**：相比基础 Agent，多轮查询规划与关键词扩展提高 Recall；去重与透明
  排序减少人工筛选；evidence 可复现每次查询。
- [ ] **创新增益**：查询规划、关键词扩展核验清单、跨库去重、透明打分
  （0.6·relevance+0.2·元数据完整+0.2·来源可靠）由脚本固化、可复现。
- [ ] **可靠体验**：`python3 scripts/main.py --help` 退出码 0；空输入/坏 JSON/坏编码
  不崩溃；重复运行结果一致；异常写入 errors/degradations。
- [ ] **生态贡献**：SKILL.md+脚本+references 齐全；他人可按说明独立复现一次完整检索。

提交前跑一次 `python3 scripts/selftest.py`，应输出全 PASS。

## 7. 降级策略

- **缺数据**：候选为空 → `status=insufficient_evidence`，在 `status_notes`/
  `limitations` 写明缺失与下一步检索建议，**不补造文献**。
- **缺软件/接口**：检索工具不可用、限流、权限不足 → 记入 `errors`/`degradations`，
  改用本地材料（PDF/materials）补充证据并标注局限。
- **元数据缺失/来源冲突**：缺失标 `null` 并入 `uncertainty.missing_metadata`；
  多来源不一致全部记录并标 `source_conflict`，不擅自取舍。
- **无法判断**：写 `pending_human`，不猜测。

## 8. 脚本调用（相对路径，纯标准库，无网络）

在 Skill 根目录（运行时挂载于 `/skills/weekly-skill`）执行：

```bash
python3 scripts/main.py --help
python3 scripts/main.py run --input /input --output /app/output --work /app/work
python3 scripts/main.py import --inline '{"title":"...","doi":"...","database":"crossref","relevance":0.9,"relevance_reason":"...","evidence":[{"locator":"abstract","quote":"..."}]}' --work /app/work
python3 scripts/main.py import --inline '{"title":"...","doi":"...","excluded":true,"exclusion_reason":"研究对象不符"}' --work /app/work
python3 scripts/main.py evidence --inline '{"query":"...","source":"crossref","database":"crossref","rationale":"..."}' --work /app/work
python3 scripts/main.py note --inline '{"status_notes":["..."],"limitations":["..."],"coverage_gaps":["..."],"pending_human":["..."],"search_scope":"..."}' --work /app/work
python3 scripts/main.py assemble --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck --output /app/output --work /app/work
python3 scripts/selftest.py
```

候选记录字段见 `references/result_schema.md`；查询构造、去重与排序规则见
`references/query_and_dedup.md`。以上命令为示意，非正式案例结果。

## 9. 边界与复现

不做「通用大而全」：一个 Skill 只解决一类任务。架构上采用**单一版本化账本 +
纯函数派生**，与多散落 JSON 文件的实现不同：每次变更只增不改（单调 seq），
`assemble` 无副作用，故重复运行确定一致。交付前请在不属于作者的环境里
用 `scripts/selftest.py` 跑一遍，确认空输入/坏 JSON/坏编码/契约回填/
重复运行五类场景均通过。