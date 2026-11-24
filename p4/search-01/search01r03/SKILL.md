---
name: academic-literature-search
description: >-
  面向科研问题的开放学术文献检索 Skill：多轮查询规划、关键词扩展核验、跨库候选召回、
  DOI/标题去重、元数据核验、透明打分排序与逐字证据溯源，最终交付可复核的 result.json。
  当需要围绕一个研究问题检索、筛选、排序文献并保留筛选依据时使用。本版侧重确定性：
  同输入两次运行产物逐字节一致，并提供 scripts/selftest.py 与可勾选自检清单。
version: 1.1.0
---

# 学术文献检索 Skill（确定性版）

## 1. 角色与何时使用

本 Skill 把「研究问题 → 查询规划 → 候选召回 → 去重核验 → 排序 → 可复核交付」
固化为可重复流程，供 Agent 在固定科研案例上运行。适用：围绕主题/方法/研究对象
检索开放学术文献，交付候选集、排序/筛选依据与遗漏说明。不适用：单篇论文全文
解析、写作润色、资料库整理。核心原则：**不编造文献、数值、DOI、元数据或来源**；
无材料就写「材料不足」，绝不示例填充。

本变体的明确设计取舍：**确定性优先**。全部产物不含墙钟时间，时间顺序用
`change_log[].seq` 表达；`assemble` 是（输入目录 + 工作区状态）的纯函数，同一输入
两次运行 result.json 逐字节一致（与常见「每次运行带时间戳」的实现明显不同）。

## 2. 输入契约

- 运行时原始输入位于 `/input/`：`case.json`（案例任务、约束、可能含 `output_contract`）
  与 `task.md`（题目说明）；可能另有 `materials/`（PDF 等科研材料）。
- 不假设固定键名：先扫描 `/input/` 全部文件再解析；文本按 `utf-8-sig` 读取并容错
  （BOM、坏编码、坏 JSON 均降级不崩溃），单文件超过 2MB 跳过并记录到 `degradations`。
- 工作目录 `/app/work/`，最终产物写入 `/app/output/`。
- 若 `case.json` 含 `output_contract`（或 `output`/`schema`/`required_fields` 等），
  其全部字段必须原样回填进 result.json：未填的给 `null` 或 `[]`，不改名、不删键。

## 3. 输出契约（/app/output/result.json）

必须产出 `/app/output/result.json`，字段如下（`*` 为强制，详见
`references/result_schema.md`）：

- `case_id`*、`status`*（`ok | partial | insufficient_evidence | input_error`）；
- `results`*：候选文献列表，每条含 rank/title/authors/year/journal/doi/url/database/
  relevance/relevance_reason/score/evidence（locator + 逐字 quote）；
- `evidence`*（query/source/database/rationale/kind/seq）、`sources`*、
  `uncertainty`*（missing_metadata/conflicts/coverage_gaps/notes）、
  `change_log`*（seq/action/reason/status）、`status_notes`*；
- `research_question`、`search_scope`、`limitations`、`errors`、`degradations`、
  `pending_human`、`runtime`、`determinism`、`outputs`、`contract`；
- 题目 `output_contract` 的**全部字段**（未获得的回填 `null`/`[]`）。

脚本还会生成 `/app/output/candidates.md`（由 result.json 渲染的可编辑候选清单）。
本任务无 Office/图件硬性要求，不生成 PDF/PNG；若案例另行要求，再按需产出。

## 4. 执行流程（总预算：900 秒、64 次模型请求）

**任何时刻中途失败，都必须保证 `/app/output/result.json` 合法存在。** 步骤 0 在
**前 3 分钟**落盘一版 `status=partial` 骨架，此后每一步只做增量改进。

| 步骤 | 内容 | 输入 | 输出 | 时间预算 |
|-|-|-|-|-|
| 0 骨架 | `python3 scripts/main.py run`（skeleton→parse→plan→assemble） | /input/ | result.json(partial) + case_summary/plan | ≤3 分钟 |
| 1 解析 | 读 case.json/task.md，确认研究问题、约束、output_contract 键 | /input/ | case_summary.json | 并入步骤 0 |
| 2 规划 | 多轮查询规划 + 关键词扩展核验清单 | case_summary | plan.json | 2–5 分钟 |
| 3 检索 | 用 paper_search / web_search 执行召回（单概念→AND 组合→引文） | plan | 候选记录 | ≤20 分钟 |
| 4 导入 | `python3 scripts/main.py import --file /app/work/candidates_in.json` | 候选 | records.json | 每次秒级 |
| 5 证据 | `python3 scripts/main.py evidence --inline '{...}'` | 查询日志 | evidence.json | 随检索进行 |
| 6 复核 | 校验元数据/去重/相关性依据（标题、作者、年份、期刊、DOI） | records | 去重后 records | 5 分钟 |
| 7 组装 | `python3 scripts/main.py assemble` 写最终 result.json + candidates.md | 全部 | /app/output/result.json | ≤1 分钟 |
| 8 自检 | `python3 scripts/selftest.py` 与 `main.py selfcheck` | result.json | PASS/FAIL 清单 | ≤1 分钟 |

建议时间分配：检索 40%、复核 30%、规划 15%、组装与自检 10%、缓冲 5%。64 次模型
请求用于检索与相关性判断；脚本是确定性辅助，不消耗模型请求。每次 `import`/
`evidence`/`assemble` 都通过 `assemble` 增量更新同一份 result.json。
`run` 会先清空工作区状态再落骨架，保证重复运行（如评测的多次重复）逐字节一致；
运行期增量改进请用 `import`/`evidence`/`assemble`，不要重复调用 `run`。

## 5. 自检清单（逐条可勾选）

完整清单见 `references/checklist.md`；自动检查用 `python3 scripts/selftest.py`。

- [ ] **实际效果**：results 每条均有真实标题 + 来源（DOI 或 URL）+ 逐字 quote；
  元数据尽量齐全；遗漏写入 coverage_gaps。
- [ ] **使用增益**：多轮查询规划与关键词扩展提高 Recall；去重与透明排序减少人工筛选；
  evidence 可复现每次查询。
- [ ] **创新增益**：查询规划、关键词扩展核验清单、跨库去重、透明打分
  （0.6·relevance + 0.2·元数据完整 + 0.2·来源可靠）由脚本固化、可复现。
- [ ] **可靠体验**：`python3 scripts/main.py --help` 退出码 0；空输入/坏 JSON 不崩溃；
  同输入两次运行 result.json 逐字节一致；异常写入 errors/degradations。
- [ ] **生态贡献**：SKILL.md + 脚本 + references 齐全；他人可按说明独立复现一次完整检索。

## 6. 降级策略（不编造）

- 缺数据：候选为空 → `status=insufficient_evidence`，在 `status_notes`/`limitations`
  写明缺失与下一步检索建议，**不补造文献**。
- 缺软件/接口：检索工具不可用、限流、权限不足 → 记录到 `errors`/`degradations`，
  改用已有本地材料（PDF/materials）补充证据并标注局限。
- 元数据缺失/来源冲突：缺失标 `null` 并列入 `uncertainty.missing_metadata`；同一指标
  多来源不一致时全部记录并标 `source_conflict`，不擅自取舍。
- 无法判断的字段写 `pending_human`，不猜测。

## 7. 确定性设计

- 全链路无随机数、无墙钟时间；顺序由 `seq` 单调递增表达。
- 排序并列项用「规范化标题升序 + 原始导入顺序」二次排序，杜绝字典遍历顺序抖动。
- `determinism.fingerprint` = 对确定性字段做 `json.dumps(sort_keys=True)` 后取 sha256；
  复核：`python3 scripts/selftest.py`（全量）或 `--quick`（只跑确定性）。

## 8. 脚本调用（纯标准库，相对路径）

在 Skill 根目录（运行时挂载于 `/skills/weekly-skill`，先 `cd /skills/weekly-skill`）执行：

```bash
python3 scripts/main.py --help
python3 scripts/main.py run --input /input --output /app/output --work /app/work
python3 scripts/main.py import --file /app/work/candidates_in.json --work /app/work
python3 scripts/main.py evidence --inline '{"query":"...","source":"paper_search","database":"crossref","rationale":"..."}' --work /app/work
python3 scripts/main.py assemble --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck --output /app/output --work /app/work
python3 scripts/selftest.py
```

候选记录字段见 `references/result_schema.md`；查询构造、去重与排序规则见
`references/checklist.md`。以上命令为示意，非正式案例结果。

## 9. 最小调用示例（示例，非正式案例）

```text
输入 /input/case.json：{"case_id":"case-demo-001","research_question":"...",
  "output_contract":{"must_have_sources":true}}
执行：python3 scripts/main.py run --input /input --output /app/output --work /app/work
示意输出 /app/output/result.json（骨架）：status=partial，results=[]，contract 回填
must_have_sources=true。检索后 import/evidence/assemble 得到 status=ok 与排序结果。
```

## 10. 边界

- 只解决「开放学术数据库上的检索、组合、召回与排序」，不做全文解析与写作。
- 没有真实材料时输出「材料不足」，不用示例填充；不提交预制答案与正式测试产物。
- 交付前应在不属于作者的环境（不同机器/不同 Python 版本）跑一遍 `selftest.py`。
