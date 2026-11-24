---
name: academic-literature-search
description: >-
  面向科研问题的开放学术文献检索 Skill：把“研究问题拆解 → 多轮查询规划 → 关键词扩展
  → 跨库召回 → 去重核验 → 透明打分排序 → 证据溯源”固化为可复核流程，交付
  /app/output/result.json 与可编辑候选清单，并按题目评分维度逐条对账。
  当需要围绕一个研究问题检索、筛选、排序文献并保留筛选依据时使用。
version: 2.0.0
---

# 学术文献检索 Skill

## 1. 角色与何时使用

本 Skill 把“研究问题 → 问题拆解 → 查询规划 → 候选召回 → 去重核验 → 打分排序 → 可复核交付”固化为可重复执行的流程，供 Agent 在固定科研案例上运行。适用：围绕主题、方法或研究对象在开放学术数据库上检索、筛选、排序文献，并保留每一条结果的相关性依据与来源。不适用：单篇论文全文精读、写作润色、资料库整理、Office/图件制作。核心红线：**不编造文献、标题、作者、年份、期刊、DOI、URL 或任何数值；无材料即写“材料不足”，绝不示例填充。**

## 2. 输入契约

- 运行时原始输入位于 `/input/`：`case.json`（案例任务、约束，可能含 `output_contract`）与 `task.md`（题目说明）；可能另有 `materials/`（PDF 等科研材料）。
- **先发现、再假设**：扫描 `/input/` 全部文件，不假定固定文件名或键名。文本按 `utf-8-sig` → `utf-8` → `gb18030` → `latin-1` 顺序容错解码；坏编码、坏 JSON 均降级记录，不崩溃。单文件超过 2MB 跳过并写 `degradations`。
- 工作目录 `/app/work/`，最终产物写入 `/app/output/`（含 `result.json`）。
- 若 `case.json` 含 `output_contract`（或其别名 `output`/`schema`/`required_fields`/`expected_fields` 等），其**全部键必须原样回填**进 `result.json`：已获得的填实值，未获得的填 `null` 或 `[]`，不改名、不删键。

## 3. 输出契约（/app/output/result.json）

必须产出 `/app/output/result.json`，字段如下（标 `*` 为强制）：

- `case_id`*、`status`*：`ok | partial | insufficient_evidence | input_error`（诚实标注，缺材料不算 ok）；
- `results`*：候选文献列表。每条含 `rank / title / authors / year / journal / doi / url / database / relevance / relevance_reason / score / score_components / evidence(locator+quote) / aspects / query_ids`；
- `evidence`*：查询与筛选追踪账本。每条含 `id / query / source / database / aspect_id / timestamp / rationale / kind / hits_found / hits_kept / result_ids`；
- `coverage_matrix`*：研究问题拆解后的“角度 aspect × 计划/已执行/命中/状态”覆盖矩阵；`coverage_gaps` 列未覆盖角度；
- `sources`*、`uncertainty`*（`missing_metadata / conflicts / coverage_gaps / notes`）、`change_log`*（`time / action / reason / status`）、`pending_human`*；
- `research_question`、`search_scope`、`limitations`、`errors`、`degradations`、`runtime`；
- 题目 `output_contract` 的**全部字段**原样回填。

脚本会依 `result.json` 渲染 `/app/output/candidates.md`（可编辑候选清单，含 rank、标题、年份、score、依据与来源，供人工复核）。本任务不要求 Office/图件，不生成 PDF/PNG；若案例另行要求再按需产出，并标注为示例。

## 4. 评分维度对齐（本版核心设计）

本版直接把题目 5 个评分维度映射为流程动作与自检项，使“能被评分”变成可勾选清单：

| 评分维度(分值) | 本 Skill 对应的流程/产出 | 自检项 |
|-|-|-|
| 实际效果(40) | problem→aspects 拆解保证 Recall；`results` 每条有真实标题+来源+locator/quote；transparent 打分保证 Precision/排序 | S1–S5 |
| 使用增益(25) | 多轮查询规划、关键词扩展、去重聚类降低人工筛选；evidence 账本使每次查询可复现 | S6–S7 |
| 创新增益(15) | 覆盖矩阵 + 双向溯源（result→query→aspect 与 aspect→query→question）+ 确定性排序 | S8–S9 |
| 可靠体验(10) | 前 3 分钟落盘骨架；空输入/坏 JSON/限流/冲突/无权限均诚实降级；重复运行结果一致 | S10–S13 |
| 生态贡献(10) | SKILL.md + scripts + references 齐全，他人可按说明独立复现一次完整检索 | S14–S16 |

## 5. 执行流程（预算：总 900 秒、64 次模型请求、单响应 ≤16384 token）

**硬规则：步骤 0 必须在开头 3 分钟内落盘一版 `status=partial` 的合法 `result.json`；此后每一步只做增量改进，任何中途失败都必须保证 `result.json` 合法存在。**

| 步骤 | 动作 | 输入 | 输出 | 时间预算 |
|-|-|-|-|-|
| 0 骨架 | `python3 scripts/main.py run`（skeleton→parse→plan→assemble） | /input/ | result.json(partial)+case_summary.json+plan.json | ≤3 分钟 |
| 1 解析 | 读 case.json/task.md，确认研究问题、约束、output_contract 键 | /input/ | case_summary.json | 并入步骤 0 |
| 2 规划 | 按问题拆解为 aspects，规划多轮查询 + 关键词扩展核验清单 | case_summary | plan.json | 2–5 分钟 |
| 3 检索 | 用检索工具执行召回（单概念 → AND/OR 组合 → 同义/缩写/跨语言 → 引文） | plan | 候选+证据 | ≤20 分钟 |
| 4 导入 | `main.py import` 导入候选；`main.py evidence` / `import-evidence` 记录来源 | 候选 | records.json/evidence.json | 每次秒级 |
| 5 复核 | 核验元数据、去重、相关性依据（标题/作者/年份/期刊/DOI） | records | 去重后记录 | 5 分钟 |
| 6 组装 | `main.py assemble` 写最终 result.json + candidates.md | 全部 | /app/output/result.json | ≤1 分钟 |
| 7 自检 | `main.py selfcheck` + `scripts/selftest.py` | result.json | PASS/FAIL 清单 | ≤1 分钟 |

时间建议：检索 40%、复核 30%、规划 15%、组装与自检 10%、缓冲 5%。64 次模型请求只用于检索与相关性判断；脚本是确定性辅助，不消耗模型请求。

## 6. 自检清单（提交前逐条勾选）

- [ ] **S1 真实标题与来源**：results 每条有非空 title + doi/url；作者/年份/期刊/DOI 尽量齐全。
- [ ] **S2 相关性依据**：每条有 relevance_reason 与 evidence（locator + 逐字 quote），无 quote 用 `not_reported`。
- [ ] **S3 覆盖与遗漏**：coverage_matrix 覆盖主要研究方向；gaps 写明未覆盖角度与建议查询。
- [ ] **S4 排序可解释**：每条含 score + score_components（0.5·relevance + 0.25·元数据 + 0.25·来源），排序确定可复现。
- [ ] **S5 溯源可复核**：每条 result 可经 query_ids 回到 evidence 的 query/source/database，且反向 aspect→query→result 贯通。
- [ ] **S6 使用增益**：多轮查询规划 + 关键词扩展相对单查询提高 Recall，去重减少人工复核。
- [ ] **S7 效率增益**：`import-evidence` 批量导入减少请求；确定性脚本替代人工排序核验。
- [ ] **S8 新增能力**：coverage matrix + 双向溯源是脚本固化的能力，基础 Agent 难以稳定复现。
- [ ] **S9 方法边界**：references/query_and_dedup.md 说明查询构造、扩展、去重、打分规则与适用边界。
- [ ] **S10 调用可用**：`python3 scripts/main.py --help` 退出码 0。
- [ ] **S11 运行稳定**：空输入/坏 JSON/缺输入各跑一次，不崩溃、退出码 0、写合法结果。
- [ ] **S12 异常处理**：接口限流/权限/缺失/冲突写入 errors/degradations/uncertainty，不猜测。
- [ ] **S13 重复一致**：相同输入两次运行 result.json 一致（确定性排序、固定字段顺序）。
- [ ] **S14 文档齐全**：SKILL.md 写明用途、调用、契约、异常、限制；scripts/references 真实存在。
- [ ] **S15 可复用**：他人可按 SKILL.md + references 独立复现一次完整检索。
- [ ] **S16 分享价值**：query_and_dedup.md 沉淀检索经验与术语扩展/去重模板，可作为 Benchmark 复用。

## 7. 降级策略（不编造）

- **缺数据/空结果**：`status=insufficient_evidence`，在 `status_notes`/`limitations` 写明缺失与下一步检索建议；不补造文献。
- **缺软件/接口异常/限流/权限**：写入 `errors`/`degradations`，改用已有本地材料（materials/PDF）补充证据并标注局限。
- **元数据缺失**：缺失字段标 `null` 并列入 `uncertainty.missing_metadata`。
- **来源冲突**：同一指标多来源不一致全部记录并标 `source_conflict`，不擅自取舍。
- **无法判断**：写入 `pending_human`，不猜测。

## 8. 脚本调用（纯标准库，相对路径）

在 Skill 根目录（运行时挂载于 `/skills/weekly-skill`，先 `cd /skills/weekly-skill`）执行：

```bash
python3 scripts/main.py --help
python3 scripts/main.py run --input /input --output /app/output --work /app/work
python3 scripts/main.py plan-update --inline '{"aspects":[...],"queries":[...]}' --work /app/work
python3 scripts/main.py import --file /app/work/candidates_in.json --work /app/work
python3 scripts/main.py evidence --inline '{"query":"...","source":"paper_search","database":"crossref","aspect_id":"a1","rationale":"..."}' --work /app/work
python3 scripts/main.py import-evidence --file /app/work/evidence_in.json --work /app/work
python3 scripts/main.py assemble --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck --input /input --output /app/output --work /app/work
python3 scripts/selftest.py
```

候选字段见 `references/result_schema.md`；查询构造、去重与打分规则见 `references/query_and_dedup.md`。

## 9. 目录结构与边界

```text
<skill-root>/
├── SKILL.md
├── scripts/main.py        # CLI 入口（子命令：run/import/evidence/import-evidence/plan-update/assemble/selfcheck）
├── scripts/core_impl.py   # 纯标准库实现：IO/解析/规划/去重/打分/组装/自检
├── scripts/selftest.py    # 空输入/坏 JSON/样本案例/契约回填的自检
└── references/
    ├── result_schema.md
    └── query_and_dedup.md
```

一个 Skill 只解决一类任务（开放学术文献检索）；无材料时输出“材料不足”，不做通用大而全流程，也不预写正式测试答案。