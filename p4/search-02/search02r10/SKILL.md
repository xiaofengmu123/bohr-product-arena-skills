---
name: research-question-driven-search
description: >-
  科研问题驱动检索 Skill：把同时含对象、机制、方法、时间范围、比较关系与证据要求的复杂科研问题拆成可检索子问题，
  规划查询顺序与依赖，扩展同义/缩写/跨语言/上下位术语，对固定快照中的全部记录逐条筛选、按内容指纹去重，
  识别重复导出与冲突意见等输入夹具，按透明规则排序，记录停止判断、冲突与遗漏，交付可复核的 result.json。
  适用于围绕科研问题检索、筛选、排序文献并保留筛选依据；不适用于单篇精读、全文翻译、写作润色或开放式科研问答。
version: 2.0.0
---

# 科研问题驱动检索 Skill

## 1. 角色与何时使用

本 Skill 把"科研问题 → 子问题拆解 → 查询规划 → 术语扩展 → 多轮检索 → 全量筛选/去重 → 排序 → 停止判断 → 可复核交付"固化为可重复流程。适用：围绕研究对象、机制、方法、时间范围、比较关系与证据要求检索文献，交付候选集、排序/筛选依据、冲突与遗漏说明。不适用：单篇文献精读、全文翻译、写作润色、开放式科研问答。

核心原则：**不编造文献、标题、作者、年份、期刊、DOI、数值、来源或评测分数**；无材料就写"材料不足"，绝不示例填充。质量判据只有三条——问题被拆成哪些可检索子问题、每个子问题是否有足够证据、检索过程能否解释与复现。

**本题限定**（作者版）为**有限快照**：固定研究问题 + 九篇真实署名论文 + 实际查询输入 + 全部记录筛选。不做活库检索、不做全局召回、不伪造人工评审；"重复导出/冲突意见"是输入夹具，需识别并标注，不得当作真实冲突或预期答案。

## 2. 输入契约（运行时）

- 原始输入位于 `/input/`：`case.json`（案例任务、约束、可能含 `output_contract`）与 `task.md`（题目说明）；可能另有 `materials/`（PDF 原文 = 论文快照，或检索导出 JSON/JSONL/CSV）。
- 不假设固定键名：先扫描 `/input/` 全部文件再解析。文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 均降级不崩溃）；单文件 >2MB 跳过并记入 `degradations`。
- PDF 通过 `pdftotext -layout`（或平台 PDF 文本工具）抽取为 `work/pdf_text/*.txt`；抽取失败不编造正文，标记 `pdf_not_extracted` 并建议用平台工具补充。
- 工作目录 `/app/work/`，最终产物写入 `/app/output/`。

## 3. 输出契约（/app/output/result.json）

必须产出 `/app/output/result.json`，含以下标准字段（`*` 强制）：

- `case_id`*（取自输入）、`status`*：`ok | partial | insufficient_evidence | input_error`；
- `research_question`、`sub_questions`*（六要素拆解）、`query_plan`*、`term_expansion`*、`stop_decision`*；
- `results`*：纳入候选，每条含 `rank/title/authors/year/journal/doi/url/database/sub_question_ids/relevance/relevance_reason/screening/screening_reason/evidence(locator+quote)/score/score_breakdown`；
- `screening`*：**全量账本**（`included/excluded/uncertain/merged_duplicates` 及逐条记录），排除项不静默丢弃；
- `evidence`*：查询/轮次/冲突/缺口日志（`query/source/database/timestamp/rationale/kind`）；
- `sources`*、`uncertainty`*（`missing_metadata/conflicts/coverage_gaps/notes`）、`change_log`*（`time/action/reason/status`）；
- `status_notes`、`limitations`、`errors`、`degradations`、`pending_human`、`runtime`、`selfcheck`；
- **题目 `output_contract` 的全部字段**：从 `case.json` 读取，逐键原样回填，未获得的给 `null`/`[]`，不改名、不删未知键；合同本身以 `output_contract` 键回显备查。

同时生成可编辑 `/app/output/screening.md`（全量筛选清单）与 `/app/output/plan.md`（拆解、查询计划、术语扩展、停止判断）。本任务无 Office/图件硬性要求，默认不生成 PDF/PNG；若案例 `output_contract` 另行要求，再按需产出并补渲染预览。

## 4. 执行流程（总预算：900 秒 / 64 次模型请求 / 每响应含推理 ≤16384 token）

脚本是**确定性辅助**，不消耗模型请求；64 次请求用于拆解、相关性判断与查询调整。**前 3 分钟内必须先落盘一版合法 `status=partial` 骨架**，之后只用增量命令更新同一份 result.json，禁止重新覆盖计划。

| 步骤 | 内容 | 命令（相对路径，先 `cd /skills/weekly-skill`） | 输出 | 预算 |
|-|-|-|-|-|
| 0 扫描 | 发现输入、抽取 PDF、规范化全部记录、按指纹去重 | `python3 -B scripts/main.py scan --input /input --work /app/work` | work/inventory.json + work/records.json | ≤90 秒 |
| 1 骨架 | 生成最小合法 result.json（partial） | `python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work` | result.json(partial) | ≤3 分钟（硬性） |
| 2 拆解 | 识别对象/机制/方法/时间/比较/证据要求，形成 SQ1..SQn | `main.py plan --work /app/work --file work/plan_in.json` | work/plan.json | 3–5 分钟 |
| 3 规划+扩展 | 查询顺序/依赖 + 术语变体（每概念≤6，控发散） | 同上 plan 命令（增量合并） | work/plan.json | 2–4 分钟 |
| 4 筛选 | 逐条 include/exclude/uncertain + 理由 + locator+quote | `main.py screen --work /app/work --file work/screen_in.json` | work/screening.json | 随检索进行 |
| 5 证据+检索 | paper_search/web_search/本地材料按计划多轮，写证据日志 | `main.py evidence --work /app/work --file work/evidence_in.json` | work/evidence.json | ≤25 分钟 |
| 6 排序+收敛 | 计算透明得分、检查覆盖/冲突/边际收益 | `main.py rank --work /app/work` | work/rank.json | 2–3 分钟 |
| 7 组装 | `main.py assemble --input /input --output /app/output --work /app/work --runtime-seconds <s> --runtime-requests <n>` | result.json + screening.md + plan.md | ≤1 分钟 |
| 8 自检 | `main.py selfcheck --output /app/output --work /app/work`（逐条 PASS/FAIL） | 自检清单 | ≤1 分钟 |

原则：每步完成后用 `assemble` 刷新 result.json，只做增量改进。建议分配：检索/筛选 45%、拆解/规划 20%、组装/自检 10%、缓冲与异常处置 25%（快照题型检索为核对与筛选，不追求无限轮次）。`scan` 输出的 `records.json` 是**单一中间表示**，后续所有步骤只读写它，避免各步骤各自解析原文导致不一致。

## 5. 自检清单（逐条勾选，对应评分维度）

- [ ] **实际效果/完成度**：研究问题被拆成 ≥1 条可检索子问题，每条标注对象/机制/方法/时间/比较/证据要求；每步输入输出清楚。
- [ ] **实际效果/正确性**：每条结果标题/作者/年份/期刊/DOI 真实且逐字保留来源写法；结果与子问题相关；相关性依据为 locator+quote，非空泛评语。
- [ ] **实际效果/完整性**：每个子问题均有 ≥1 条 included 证据；未覆盖子问题列入 `uncertainty.coverage_gaps`；全部记录（含重复导出）都出现在 screening 账本。
- [ ] **实际效果/可核验性**：`evidence` 记录每轮查询、筛选、排序与停止理由；`screening` 覆盖全部记录，排除项有理由。
- [ ] **使用增益**：六要素拆解、依赖化查询规划、受控术语扩展、指纹去重与全量筛选减少人工返工；`scripts/main.py selfcheck` 全 PASS。
- [ ] **创新增益**：单一中间表示（records.json）+ 边界校验（输入→中间→输出三段）+ 内容指纹去重 + 透明打分 `0.6·relevance + 0.2·元数据完整 + 0.2·来源可靠` 由脚本固化、可复现。
- [ ] **可靠体验**：`python3 -B scripts/main.py --help` 退出码 0；空输入/坏 JSON/坏编码不崩溃且退出码 0；重复运行结果一致（幂等，change_log 时间戳取快照 mtime）；异常写入 errors/degradations。
- [ ] **生态贡献**：SKILL.md + scripts（main.py/core.py/selftest.py）+ references 齐全；他人可按 `references/` 独立复现一次完整检索；`scripts/selftest.py` 免案例自测。

## 6. 降级策略（不允许编造）

- **缺数据/空结果**：候选为空 → `status=insufficient_evidence`，`status_notes`/`limitations` 写明缺失与下一步建议，不补造文献。
- **缺软件/接口异常/限流/权限不足**：记入 `errors`/`degradations`，改用本地材料（materials/PDF）并标注局限；无法继续时如实降级为 `partial`。
- **元数据缺失**：标 `null` 并列入 `uncertainty.missing_metadata`；同一指标多来源不一致：全部记录并标 `source_conflict`，不擅自取舍。
- **重复导出/冲突意见**：按指纹归入同一去重组，主记录 `role=primary`、其余 `role=duplicate`，字段不一致写入 `dedup.conflicts`；题目声明为输入夹具的标注 `input_fixture`，不得当作真实冲突或答案。
- **术语严重歧义**：在 `term_expansion` 注明歧义并加限定词，或拆两条查询分别记录；无法判断的字段写 `pending_human`，不猜测。
- **无法完成全部子问题**：`stop_decision.status=handoff`，`pending_human` 列出具体缺口。

## 7. 脚本调用（纯标准库，相对路径）

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py --help
python3 -B scripts/main.py scan --input /input --work /app/work
python3 -B scripts/main.py skeleton --input /input --output /app/output --work /app/work
python3 -B scripts/main.py plan --work /app/work --file /app/work/plan_in.json      # 或用 --inline '简单JSON'
python3 -B scripts/main.py screen --work /app/work --file /app/work/screen_in.json
python3 -B scripts/main.py evidence --work /app/work --file /app/work/evidence_in.json
python3 -B scripts/main.py rank --work /app/work
python3 -B scripts/main.py assemble --input /input --output /app/output --work /app/work --runtime-seconds 120 --runtime-requests 7
python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
python3 -B scripts/selftest.py   # 免案例自测：空输入/坏JSON/坏编码/去重/幂等
```

复杂 JSON（拆解/候选/证据）优先 `--file`，避免 shell 引号转义；`--inline` 仅用于简单单行。`--input/--output/--work` 缺省分别为 `/input`、`/app/output`、`/app/work`。字段规范见 `references/result-schema.md`；方法细则与检查清单见 `references/method-and-checklist.md`。以上命令与字段均为示意，非正式案例结果。

## 8. 与参考实现的结构差异（本版差异化设计）

1. **单一中间表示**：`scan` 一次把 PDF/导出文件归一化为统一 `records.json`（含内容指纹、去重组、来源定位），后续步骤不再各自解析原文。
2. **三段边界校验**：输入（scan 时校验可读性与大小）、中间（screen/plan/evidence 导入时逐条校验 schema，坏条目记 errors 不中断）、输出（assemble/selfcheck 校验字段完整性与全量覆盖）。
3. **内容指纹去重**：以归一化 DOI 优先、否则归一化标题+第一作者+年份为指纹，主动识别重复导出与字段冲突，而非仅按标题字符串相等。
4. **确定性时间戳**：change_log/evidence 的 `timestamp` 取输入快照 mtime（同快照幂等），实际耗时只记 `runtime.wall_seconds`。
