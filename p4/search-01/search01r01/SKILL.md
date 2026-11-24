---
name: academic-literature-search-evidence
description: >-
  面向科研问题的开放学术文献检索 Skill：多轮查询规划、关键词/术语扩展、跨库候选召回、
  按 DOI 与规范化标题去重、元数据核验、透明打分排序，并以 evidence_map 把每条结论、
  每条候选的 locator 与逐字 quote 固化，最终交付可复核的 result.json 与可编辑清单。
  当需要围绕一个研究问题检索、筛选、排序文献并保留逐条筛选依据时使用。
version: 1.1.0
---

# 学术文献检索 Skill（证据溯源强化版）

## 1. 角色与何时使用

把“研究问题 → 查询规划 → 候选召回 → 去重核验 → 透明排序 → 可复核交付”固化为可重复流程，供 Agent 在固定科研案例上运行。**本变体核心设计取舍**：每条结论/候选都强制带 `locator + 逐字 quote`，并额外产出 `evidence_map`（比普通 evidence 列表更细：kind/locator/quote/source/supports 反向链接），让评审能逐条回溯“这条结果为何入选、依据在哪一行”。

适用：围绕主题/方法/研究对象检索开放学术文献，交付候选集、排序/筛选依据与遗漏说明。不适用：单篇全文解析、写作润色、资料库整理。核心原则：**不编造文献、数值、DOI、元数据或来源**；无材料就写“材料不足”，绝不示例填充。

## 2. 输入契约

- 原始输入位于 `/input/`：`case.json`（案例任务、约束、`output_contract` 若存在其全部字段必须原样回填）与 `task.md`；可能另有 `materials/`（PDF/文本等科研材料）。不假设固定键名，先扫描 `/input/` 全部文件再解析。
- 文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 均降级不崩溃）；单文件超过 2MB 跳过并记录。
- 工作目录 `/app/work/`，最终产物写入 `/app/output/`。
- 检索本身由模型工具 `paper_search` / `web_search` 完成；脚本是确定性辅助，不消耗模型请求。

## 3. 输出契约（/app/output/result.json）

必须产出 `/app/output/result.json`，字段如下（`*` 强制），并回填题面 `output_contract` 的全部字段（未获得的给 `null`/`[]`，不改名、不删键）：

- `case_id`*、`status`*（`ok | partial | insufficient_evidence | input_error`）
- `results`*：候选文献，每条含 `rank / title / authors / year / journal / doi / url / database / relevance / relevance_reason / score / evidence（list，每条含 locator+quote）/ evidence_ids`
- `evidence`*：查询/来源记录；`evidence_map`*：更细的证据映射（`kind / claim / locator / quote / source / database / rationale / supports`）
- `sources`*、`uncertainty`*（`missing_metadata / conflicts / coverage_gaps / notes`）、`status_notes`*、`change_log`*（`time / action / reason / status`）
- `research_question`、`search_scope`、`limitations`、`errors`、`degradations`、`pending_human`、`runtime`
- 同步生成 `/app/output/candidates.md`（可编辑复核清单）与 `/app/output/evidence_map.json`。本任务无 Office/图件硬性要求；若案例另行要求，再按需产出 PDF/PNG 预览。

## 4. 执行流程（预算：总 900 秒、64 次模型请求；单响应 16384 token）

**铁律：前 3 分钟必须先落盘一版 `status=partial` 的 result.json 骨架，之后每一步只做增量改进，任何中途失败都保证 result.json 合法存在。**

| 步骤 | 内容 | 输入 → 输出 | 命令 | 时间预算 |
|-|-|-|-|-|
| 0 骨架 | skeleton→parse→plan→assemble（不检索） | /input → partial result.json | `python3 scripts/main.py run` | ≤3 分钟 |
| 1 解析 | 确认研究问题、约束、`output_contract` 字段 | /input → `case_summary.json` | `run` 内含 | 并入步骤 0 |
| 2 规划 | 多轮查询规划 + 关键词/术语扩展清单 | case_summary → `plan.json` | `run` 内含 | 2–5 分钟 |
| 3 检索 | `paper_search`/`web_search` 召回（单概念→AND 组合→引文回溯） | plan → 候选 | 模型工具 | ≤20 分钟 |
| 4 导入 | 候选 upsert，按 DOI/规范标题去重 | `--inline` 候选 → `records.json` | `python3 scripts/main.py import --inline '{...}'` | 秒级 |
| 5 证据 | 记录查询/来源，每条带 locator+quote | `--inline` → `evidence.json` | `python3 scripts/main.py evidence --inline '{...}'` | 随检索进行 |
| 6 复核 | 元数据核验、去重、相关性依据（标题/作者/年份/期刊/DOI） | records → 去重后 records | 同 `import` | 5 分钟 |
| 7 组装 | 打分排序，写 result.json + candidates.md + evidence_map.json | 全部 → /app/output/ | `python3 scripts/main.py assemble` | ≤1 分钟 |
| 8 自检 | 校验 result.json 契约 | result.json → PASS/FAIL 清单 | `python3 scripts/main.py selfcheck` | ≤1 分钟 |

建议分配：检索 40%、复核 30%、规划 15%、组装自检 10%、缓冲 5%。64 次请求用于检索与相关性判断；脚本确定性辅助不占请求。步骤 4/5 可在检索中多次增量调用，`change_log` 每次自动追加。

## 5. 自检清单（对应评分维度）

**实际效果**：
- [ ] `results` 每条有真实 title + 来源（DOI 或 URL）+ `relevance_reason` + 证据（locator + quote）；作者/年份/期刊/DOI 尽量齐全
- [ ] 覆盖主要研究方向并在 `uncertainty.coverage_gaps` 说明遗漏；关键文献命中与 Recall 可复核

**使用增益**：
- [ ] 多轮查询规划 + 关键词扩展提高 Recall；去重与透明排序减少人工筛选；`evidence`/`evidence_map` 可复现每次查询与去重决策

**创新增益**：
- [ ] `evidence_map`（kind/locator/quote/supports 反向链接）与打分（`0.6·relevance + 0.2·元数据完整 + 0.2·来源可靠`）由脚本固化、可复现，且明显区别于普通 evidence 列表

**可靠体验**：
- [ ] `python3 -B scripts/main.py --help` 退出码 0；空输入/坏 JSON/坏编码不崩溃；重复运行结果一致；异常写入 `errors`/`degradations`

**生态贡献**：
- [ ] SKILL.md + scripts（main.py/core_impl.py/selftest.py）+ references 齐全；`python3 scripts/selftest.py` 可独立复现一次完整检索闭环

## 6. 降级策略

- 缺数据：候选为空 → `status=insufficient_evidence`，在 `status_notes`/`limitations` 写明缺失与下一步检索建议，**不补造文献**。
- 缺软件/接口：检索工具不可用、限流、权限不足 → 记录到 `errors`/`degradations`，改用已有本地材料（PDF/materials）补充证据并标注局限；绝不伪造 Crossref/EuropePMC 返回或命中数、引用数与在线状态。
- 元数据缺失/来源冲突：缺失标 `null` 并列入 `uncertainty.missing_metadata`；同一指标多来源不一致时全部记录并标 `source_conflict`（写入 `evidence_map` 的 `conflict` 条目），不擅自取舍。
- 无法判断的字段写 `pending_human`，不猜测。

## 7. 脚本（纯标准库，相对路径调用）

在 Skill 根目录（运行时装于 `/skills/weekly-skill`，先 `cd /skills/weekly-skill`）执行：

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work
python3 -B scripts/main.py import --inline '{"title":"...","doi":"...","relevance":0.9,"evidence":[{"locator":"...","quote":"..."}]}' --work /app/work
python3 -B scripts/main.py evidence --inline '{"kind":"query","query":"...","source":"paper_search","database":"crossref","locator":"...","quote":"..."}' --work /app/work
python3 -B scripts/main.py assemble --input /input --output /app/output --work /app/work
python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
python3 -B scripts/selftest.py
```

候选字段与 evidence 规范见 `references/result_schema.md`；查询构造、术语扩展、去重与打分规则见 `references/query_and_evidence.md`。以上命令为示意，非正式案例结果。

## 8. 边界

- 只做一个“检索 + 溯源”的明确任务，不抽象成大而全；无材料时输出“材料不足”，不做示例填充。
- `evidence_map.json` 与 `result.json` 中的 `evidence_map` 内容一致，保证可复核。
- 交付他人前先 `python3 scripts/selftest.py` 全绿，再 `python3 scripts/main.py selfcheck` 复核。