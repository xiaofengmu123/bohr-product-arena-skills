---
name: academic-literature-search
description: >-
  面向科研问题的开放学术文献检索 Skill：契约优先的多轮查询规划、关键词扩展、跨库
  召回、结果去重核验与证据溯源。核心特征是把任意形式的 output_contract 归一化为
  有序字段表，result.json 逐一对齐（原字段名回填、含 contract_report 与 change_log），
  且任何中断都先落盘合法 partial 骨架。当需要围绕一个研究问题检索、筛选、排序文献
  并保留可复核依据时使用。
version: 1.1.0
---

# 学术文献检索 Skill（契约优先 + 证据溯源）

## 1. 角色与何时使用

本 Skill 把「研究问题 → 契约解析 → 查询规划 → 候选召回 → 去重核验 → 排序 → 可复核交付」固化为可重复流程，供 Agent 在固定科研案例上运行。适用：围绕主题/方法/研究对象检索开放学术文献，交付候选集、排序依据与遗漏说明。不适用：单篇论文全文解析、写作润色、资料库整理。

**核心原则：不编造文献、数值、DOI、元数据与来源**。材料不足写「材料不足」，绝不示例填充；无法判断的字段写 `pending_human`，不猜测。三条不改：不改契约键名、不删契约未知键、不改 Meta 输出结论强度。

## 2. 输入契约

- 原始输入位于 `/input/`：`case.json`（案例任务、约束，可能含 `output_contract`）与 `task.md`（题目说明）；可能另有 `materials/`（PDF 等科研材料，由平台按 PDF 原文提供，无预提取答案层）。
- **先发现、后假设**：扫描 `/input/` 全部文件再解析，不假设固定键名；`output_contract` 可能出现在 `case.json` 的 `output_contract / outputContract / output / schema / result_schema / required_fields / expected_fields` 等键，也可能以 Markdown 表（| 字段 | 要求 |）写在 `task.md`。
- 文本按 `utf-8-sig → utf-8 → gb18030 → latin-1` 顺序读取并容错；坏 JSON / 坏编码 / 单文件超 2MB 均降级到 `degradations`，不崩溃。
- 工作目录 `/app/work/`，最终产物写入 `/app/output/`。

## 3. 输出契约（/app/output/result.json）

必须产出 `/app/output/result.json`，采用三层发布（本版核心设计）：

1. **规范字段**（始终存在）：`case_id`、`status`（`ok | partial | insufficient_evidence | input_error`）、`results`、`evidence`、`sources`、`uncertainty`、`pending_human`、`errors`、`degradations`、`limitations`、`change_log`、`query_plan`、`runtime`、`result_schema_version`。
2. **契约原名字段**：题目 `output_contract` 声明的**全部字段**按原名、原序回填进 result.json 顶层；能从规范字段对齐的填真实值，不能对齐的回填 `null`/`[]`。
3. **契约回填报告**：`contract_report` 逐字段记录 `name / required / type / source / status(filled|null_backfilled|empty_backfilled) / value_preview`，使「逐一对齐」可被机器与人工复核。

`results` 每条含 `rank / title / authors / year / journal / doi / url / database / relevance / relevance_reason / score / evidence[]`，其中 `evidence` 必带 `locator + quote + source`。`change_log` 每条含 `time / action / reason / status`，记录从骨架到终稿的全部写操作。脚本另生成 `/app/output/candidates.md`（可编辑复核清单）。本任务无 Office/图件硬性要求，不生成 PDF/PNG；若案例另行要求再按需产出。

## 4. 执行流程（预算：总 900 秒、64 次模型请求）

**时序铁律：前 3 分钟内必须先落盘一版 `status=partial` 的合法 result.json 骨架，此后只做增量改进；任何时刻中途失败，`/app/output/result.json` 都必须合法存在。**

| 步骤 | 内容 | 输入 | 输出 | 时间预算 |
|-|-|-|-|-|
| 0 骨架 | `python3 scripts/main.py run`（skeleton → parse → plan → assemble） | /input/ | result.json(partial)+case_summary/plan | ≤3 分钟 |
| 1 解析 | 读 case.json/task.md，确认研究问题、约束、output_contract 键并归一化 | /input/ | case_summary.json | 并入步骤 0 |
| 2 规划 | 多轮查询规划 + 关键词扩展核验清单 | case_summary | plan.json | 2–5 分钟 |
| 3 检索 | 用 paper_search / web_search 执行召回（单概念→AND 组合→引文→跨库核验） | plan | 候选记录 | ≤20 分钟 |
| 4 导入 | `python3 scripts/main.py import --inline '[...]'` | 候选 | records.json（DOI/标题 upsert） | 每次秒级 |
| 5 证据 | `python3 scripts/main.py evidence --inline '{...}'` | 查询日志 | evidence.json | 随检索进行 |
| 6 复核 | 校验元数据/去重/相关性依据（标题、作者、年份、期刊、DOI） | records | 去重后 records | 5 分钟 |
| 7 组装 | `python3 scripts/main.py assemble` | 全部工作文件 | /app/output/result.json + candidates.md | ≤1 分钟 |
| 8 自检 | `python3 scripts/main.py selfcheck` | result.json | PASS/FAIL 清单 | ≤1 分钟 |

建议分配：检索 40%、复核 30%、规划 15%、组装与自检 10%、缓冲 5%。64 次模型请求用在检索与相关性判断；脚本为确定性辅助，不消耗模型请求，在运行时路径 `cd /skills/weekly-skill` 后相对调用。

## 5. 自检清单（对应评分维度，逐条可勾选）

- [ ] **实际效果**：results 每条均有真实标题 + 来源（DOI 或 URL）+ 相关性依据（locator+quote）；作者/年份/期刊/DOI 尽量齐全；覆盖主要研究方向并说明遗漏（coverage_gaps）。
- [ ] **使用增益**：多轮查询规划与关键词扩展提高 Recall；去重与透明排序减少人工筛选；evidence 可复现每次查询。
- [ ] **创新增益**：查询规划、关键词扩展核验清单、跨库去重、透明打分（0.6·relevance + 0.2·元数据完整 + 0.2·来源可靠）均由脚本固化、可复现。
- [ ] **可靠体验**：`python3 scripts/main.py --help` 退出码 0；空输入/坏 JSON/坏编码不崩溃；重复运行结果一致；异常写入 errors/degradations。
- [ ] **生态贡献**：SKILL.md + scripts + references 齐全；他人可按说明独立复现一次完整检索（用 `scripts/selftest.py` 验证）。
- [ ] **契约完整（本版重点）**：`contract_report.total_fields` 与契约字段数一致；所有契约字段键均存在于 result.json；未获得的标记 `null_backfilled/empty_backfilled`；`change_log` 非空且含骨架→终稿全程。

提交前运行 `python3 scripts/selftest.py`，须输出 `PASS=... FAIL=0` 且退出码 0。

## 6. 降级策略（诚实标注，不允许编造）

- **缺数据**：候选为空 → `status=insufficient_evidence`，在 `status_notes/limitations/uncertainty.coverage_gaps` 写明缺失与下一步检索建议，**不补造文献**。
- **缺软件/接口**：检索工具不可用、限流、权限不足 → 记入 `errors/degradations`，改用已有本地材料（PDF/materials）补证据并标注局限。
- **元数据缺失/来源冲突**：缺失标 `null` 并列入 `uncertainty.missing_metadata`；同一指标多来源不一致时全部记录并标 `source_conflict`，不擅自取舍。
- **契约字段无法判断**：写 `pending_human` 或回填 `null`，保留原键名，不猜测、不删键。
- **输入不可解析**：`status=input_error`，`case_id` 兜底为 `case:<文件名>`，errors 记录原因，脚本退出码 0。

## 7. 脚本调用（相对路径，纯标准库）

```bash
cd /skills/weekly-skill
python3 scripts/main.py --help
python3 scripts/main.py run --input /input --output /app/output --work /app/work
python3 scripts/main.py import --file /app/work/candidates_in.json --work /app/work
python3 scripts/main.py evidence --inline '{"query":"...","source":"paper_search","database":"crossref","rationale":"..."}' --work /app/work
python3 scripts/main.py assemble --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck --output /app/output --work /app/work
python3 scripts/selftest.py
```

候选记录字段与回填规则见 `references/result_schema.md`；查询构造、去重与排序规则见 `references/query_and_dedup.md`。以上命令为示意，非正式案例结果。