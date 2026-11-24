---
name: academic-literature-search
description: >-
  面向科研问题的可追溯开放学术文献检索 Skill：输入发现、多轮查询规划、关键词扩展、
  跨库候选召回与本地增量导入、去重核验、透明打分排序、逐字证据溯源（locator+quote）、
  增量落盘与确定性复现，最终交付可复核的 result.json。
  当需要围绕一个研究问题检索、筛选、排序文献并保留筛选依据时使用。
version: 1.1.0
---

# 学术文献检索 Skill（鲁棒性强化版）

## 1. 角色与何时使用

本 Skill 把「研究问题 → 查询规划 → 候选召回 → 去重核验 → 排序 → 可复核交付」固化为可重复流程，供 Agent 在固定科研案例上运行。适用：围绕主题/方法/研究对象检索开放学术文献，交付候选集、排序/筛选依据与遗漏说明。不适用：单篇论文全文解析、写作润色、资料库整理。核心原则：**不编造文献、数值、DOI、元数据或来源**；无材料就写「材料不足」，绝不示例填充。

本版与常见检索 Skill 的设计取舍：**鲁棒性优先**。空输入、坏 JSON、坏编码、缺参数、超大文件、二进制污染都要优雅降级不崩溃，同时保证题目 `output_contract` 字段原样回填、结果可逐条复核、重复运行主内容确定性一致（见 `reproducibility.fingerprint`）。

## 2. 输入契约

- 运行时原始输入位于 `/input/`：`case.json`（案例任务、约束，可能含 `output_contract`）与 `task.md`（题目说明）；可能另有 `materials/`（PDF 等）与组织方本地增量导入态（JSON/JSONL/CSV）。
- 不假设固定文件名/键名：先扫描 `/input/` 全部文件再解析。文本按 `utf-8-sig→utf-8→gb18030→cp1252→latin-1` 容错解码；坏 JSON、坏编码、空文件降级不崩溃；单文件超过 2MB 跳过并记录 `degradations`，二进制/污染文件跳过。
- 工作目录 `/app/work/`，最终产物写入 `/app/output/`。
- 若 `case.json` 含 `output_contract`（或 `output`/`schema`/`required_fields`/`output_schema` 等），其**全部字段必须原样回填**进 result.json：未获得的给 `null` 或 `[]`，不改名、不删未知键。

## 3. 输出契约（/app/output/result.json）

必须产出 `/app/output/result.json`，字段见 `references/result_schema.md`：

- `case_id`*：案例标识，取自输入，恒非空；
- `status`*：`ok | partial | insufficient_evidence | input_error`；
- `results`*：候选文献列表，每条含 `rank/title/authors/year/journal/doi/url/database/relevance/relevance_reason/score/score_components/evidence/verified/fingerprint`；
- `evidence`*：查询、筛选、排序、追踪依据（`query/source/database/timestamp/rationale/kind/results_count`）；
- `sources`*、`uncertainty`*（`missing_metadata/conflicts/coverage_gaps/notes`）、`change_log`*（`time/action/reason/status`）、`reproducibility`*（确定性 `fingerprint`）；
- `research_question`、`search_scope`、`status_notes`、`limitations`、`pending_human`、`errors`、`degradations`、`runtime`；
- 题目 `output_contract` 的**全部字段**（未获得的回填 `null`/`[]`）。

同时生成 `/app/output/candidates.md`（可编辑候选清单，标记需复核条目）。本任务无 Office/图件硬性要求，不生成 PDF/PNG；若案例另行要求，再按需产出并附真实渲染预览。

## 4. 执行流程（预算：总 900 秒、64 次模型请求）

**先落盘骨架，再增量改进；任何时刻中途失败，都必须保证 `/app/output/result.json` 合法存在。** 步骤 0 必须在前 3 分钟内完成。

| 步骤 | 内容 | 输入 | 输出 | 时间预算 |
|-|-|-|-|-|
| 0 骨架 | `python3 scripts/main.py run`（skeleton→parse→plan→assemble） | /input/ | result.json(partial) + case_summary/plan | ≤3 分钟 |
| 1 解析 | 读 case.json/task.md，确认研究问题、约束、output_contract 键 | /input/ | case_summary.json | 并入步骤 0 |
| 2 规划 | 多轮查询规划 + 关键词扩展核验清单（可改写 plan.json） | case_summary | plan.json | 2–5 分钟 |
| 3 检索 | paper_search / web_search 召回（单概念→AND→同义词→引文）；本地材料走 import | plan | 候选记录 | ≤20 分钟 |
| 4 导入 | `python3 scripts/main.py import --file <候选文件>` | 候选 | records.json | 每次秒级 |
| 5 证据 | `python3 scripts/main.py evidence --inline '{...}'` | 查询日志 | evidence.json | 随检索进行 |
| 6 复核 | 校验元数据/去重/相关性依据（标题、作者、年份、期刊、DOI） | records | 去重后 records | 5 分钟 |
| 7 组装 | `python3 scripts/main.py assemble` | 全部 | /app/output/result.json + candidates.md | ≤1 分钟 |
| 8 自检 | `python3 scripts/main.py selfcheck`（另可 `python3 scripts/selftest.py`） | result.json | PASS/FAIL 清单 | ≤1 分钟 |

时间分配：检索 40%、复核 30%、规划 15%、组装与自检 10%、缓冲 5%。64 次模型请求用于检索与相关性判断；脚本为确定性辅助，不消耗模型请求。

## 5. 自检清单（对应评分维度）

- [ ] **实际效果（40）**：results 每条均有真实标题 + 来源（DOI/URL）+ 逐字证据（locator+quote）；作者/年份/期刊/DOI 尽量齐全；`verified=false` 的不冒充已核验；覆盖主要研究方向并说明遗漏（coverage_gaps）。
- [ ] **使用增益（25）**：相比基础 Agent，多轮查询规划与关键词扩展提高 Recall；去重与透明排序减少人工筛选；evidence 可复现每次查询，`sources` 去重可追溯。
- [ ] **创新增益（15）**：查询规划、关键词扩展核验清单、跨库去重、透明打分（0.6·relevance+0.2·metadata+0.2·source）、确定性 fingerprint 均由脚本固化、可复现。
- [ ] **可靠体验（10）**：`python3 scripts/main.py --help` 退出码 0；空输入/坏 JSON/坏编码/缺参数/超大文件不崩溃；重复运行主内容一致；异常写入 errors/degradations。
- [ ] **生态贡献（10）**：SKILL.md + scripts + references 齐全；他人可按说明独立复现一次完整检索；`selftest.py` 覆盖 10 项鲁棒性场景。

## 6. 降级策略

- 缺数据：候选为空 → `status=insufficient_evidence`，在 `status_notes`/`limitations` 写明缺失与下一步检索建议，**不补造文献**。
- 缺软件/接口：检索工具不可用、限流、权限不足 → 记录到 `errors`/`degradations`，改用本地材料（PDF/`/input` 内 JSON/CSV）补充证据并标注局限。
- 元数据缺失/来源冲突：缺失标 `null` 并列入 `uncertainty.missing_metadata`；同一指标多来源不一致时全部记录并标 `source_conflict`，不擅自取舍。
- 无法判断的字段写 `pending_human`，不猜测。

## 7. 脚本调用（纯标准库，相对路径）

在 Skill 根目录（运行时挂载于 `/skills/weekly-skill`，先 `cd /skills/weekly-skill`）执行：

```bash
python3 scripts/main.py --help
python3 scripts/main.py run --input /input --output /app/output --work /app/work
python3 scripts/main.py import --file /app/work/candidates_in.json --work /app/work
python3 scripts/main.py evidence --inline '{"query":"...","source":"paper_search","database":"crossref","rationale":"..."}' --work /app/work
python3 scripts/main.py assemble --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck --output /app/output --work /app/work
python3 scripts/selftest.py          # 鲁棒性自检（10 项场景）
```

候选记录字段见 `references/result_schema.md`；查询构造、去重与排序规则见 `references/query_and_dedup.md`。以上命令为示意，非正式案例结果。

## 8. 关键设计取舍（区别于常规包）

- 输入发现：容忍任意文件名/键名，编码多级降级，单文件 2MB 上限 + 整树 2000 项截断，防超大/二进制输入拖垮进程；
- 输出契约：`output_contract` 全量原样回填 + 未知键兜底补齐，selfcheck 校验字段与 status 枚举；
- 复现性：主内容生成 SHA-256 `fingerprint`，排序确定性（score→year→title），重跑可验证；
- 证据门控：无逐字 quote 的结果自动 `verified=false`，进入 `pending_human`，不冒充已核验结论。
