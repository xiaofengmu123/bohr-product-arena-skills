---
name: research-question-retrieval
description: >-
  科研问题驱动检索 Skill：把复杂科研问题拆成可检索子问题，规划查询顺序与依赖，
  扩展同义/缩写/跨语言/上下位术语，多轮检索并全量筛选每条记录，按透明规则打分排序，
  记录停止判断与遗漏说明，交付可复核的 result.json 与 screening.md/plan.md。
  用于"围绕科研问题检索、筛选、排序文献并保留筛选依据"的任务；不用于单篇精读、翻译或写作。
version: 1.1.0
---

# 科研问题驱动检索

## 1. 角色与何时使用

本 Skill 把“科研问题 → 六要素拆解 → 依赖化查询规划 → 受控术语扩展 → 多轮检索 → 全量筛选 → 停止判断 → 可复核交付”固化为可复现流程。适用于围绕研究对象、机制、方法、时间范围、比较关系与证据要求检索文献，交付候选集、排序/筛选依据与遗漏说明。不适用于单篇精读、全文翻译、写作润色、开放式问答。核心原则：**不编造文献、标题、作者、年份、期刊、DOI、数值、来源或评测分数**；无材料就写“材料不足”，绝不示例填充。

## 2. 输入契约

- 原始输入位于 `/input/`：`case.json`（案例任务、约束、可能含 `output_contract`）与 `task.md`；可能有 `materials/`（固定快照的论文 PDF）。**先扫描全部文件再解析，不假设固定键名**。
- 文本按 `utf-8-sig` 读取并容错（BOM、坏编码、坏 JSON 均降级不崩溃）；单文件超 2MB 跳过并记录到 `degradations`。
- 工作目录 `/app/work/`，最终产物写入 `/app/output/`。
- 若 `case.json` 含 `output_contract`（或 `output`/`schema`/`required_fields`/`contract`），其**全部字段必须原样回填**进 result.json：未填给 `null`/`[]`，不改名、不删未知键。
- 题目固定快照含 9 篇真实署名论文与真实查询输入；**必须读取实际 PDF 原文，逐字引用 locator+quote**，不依据标签补造数据。有限快照即全部证据范围，不声称“全库召回/活库检索”；search09 的合成重复导出与冲突观点是输入夹具，需如实标注。

## 3. 输出契约

必须产出 `/app/output/result.json`，至少含（`*` 强制，详见 `references/result-schema.md`）：

- `case_id`*、`status`*（`ok | partial | insufficient_evidence | input_error`）；
- `research_question`、`sub_questions`*、`query_plan`*、`term_expansion`*、`stop_decision`*；
- `results`*（rank/title/authors/year/journal/doi/url/database/sub_question_ids/relevance/relevance_reason/screening/screening_reason/evidence(locator+quote)/score/score_breakdown）；
- `screening`*（included/excluded/uncertain/merged_duplicates，**全量记录**，排除项不静默丢弃）；
- `evidence`*（query/source/database/timestamp/rationale/kind）、`sources`*、`uncertainty`*、`change_log`*；
- `status_notes`、`limitations`、`errors`、`degradations`、`pending_human`、`runtime`；
- 题目 `output_contract` 的全部字段。

脚本同时生成可编辑的 `/app/output/screening.md`（全量筛选清单）与 `/app/output/plan.md`（拆解、查询规划、术语扩展、停止判断）。本任务无 Office/图件要求，不生成 PDF/PNG。

## 4. 执行流程（预算：总 900 秒、64 次模型请求、16384 token/响应）

**硬性约束：前 3 分钟内必须用 `run` 落盘一版 `status=partial` 骨架**，此后只用增量子命令更新同一份 state，禁止重新 `run` 覆盖计划；任何时刻中断，result.json 都必须合法存在。

| 步骤 | 内容 | 输入 → 输出 | 预算 |
|-|-|-|-|
| 0 骨架 | `python3 -B scripts/main.py run`：发现输入→解析→落盘 partial result | /input/ → result.json+state | ≤3 分钟（硬性） |
| 1 拆解 | 六要素识别，形成 SQ1..SQn | case_summary → `set-plan` | 2–4 分钟 |
| 2 规划 | 子问题查询顺序与依赖（前置/并行） | sub_questions → `set-plan` | 2–3 分钟 |
| 3 扩展 | 同义/缩写/跨语言/上下位，每概念 ≤6 变体 | key_concepts → `set-plan` | 2–3 分钟 |
| 4 检索 | paper_search/web_search + 读 materials/PDF，多轮 | plan → 候选 + evidence | ≤25 分钟 |
| 5 筛选 | `python3 -B scripts/main.py import --file`：逐条 include/exclude/uncertain + 理由 + locator/quote | 候选 → records | 随检索进行 |
| 6 收敛 | 每子问题证据检查、冲突验证、边际收益 → `set-plan` 填 stop_decision | evidence → stop_decision | 3–5 分钟 |
| 7 组装 | `python3 -B scripts/main.py assemble` 写三件产物 | 全部 → /app/output/ | ≤1 分钟 |
| 8 自检 | `python3 -B scripts/main.py selfcheck` 逐条 PASS/FAIL | result.json → 清单 | ≤1 分钟 |

脚本是确定性辅助，不消耗模型请求；64 次请求用于拆解、相关性判断与查询调整。建议时间分配：检索 40%、筛选 25%、拆解规划 20%、组装自检 10%、缓冲 5%。每步完成后用 `assemble` 刷新 result.json，只做增量改进。

## 5. 自检清单（逐条可勾选，对应评分维度；详见 `references/method-and-checklist.md`）

- [ ] **实际效果/完成度**：≥1 条可检索子问题，每条含对象/机制/方法/时间/比较/证据要求；每步输入输出清楚。
- [ ] **实际效果/正确性**：标题/作者/年份/期刊/DOI 真实且逐字保留来源写法；相关性依据为 locator+quote，非空泛评语。
- [ ] **实际效果/完整性**：每个子问题 ≥1 条 included；未覆盖列入 `uncertainty.coverage_gaps`。
- [ ] **实际效果/可核验性**：`evidence` 记录每轮查询/筛选/排序/停止理由；`screening` 覆盖全部记录，排除有理由。
- [ ] **使用增益**：多轮规划与术语扩展提高召回；全量筛选与透明打分减少返工；`selfcheck` 全 PASS。
- [ ] **创新增益**：六要素拆解、依赖化规划、受控扩展、停止阈值与透明打分（0.6·relevance+0.2·元数据+0.2·来源）脚本固化、可复现。
- [ ] **可靠体验**：`--help` 退出 0；空输入/坏 JSON/坏编码不崩溃且退出 0；重复运行幂等；异常写入 errors/degradations。
- [ ] **生态贡献**：SKILL.md + scripts + references 齐全；他人可按 `references/` 独立复现一次完整检索。

## 6. 降级策略（不允许编造）

- 缺数据/空结果：候选为空 → `status=insufficient_evidence`，`status_notes`/`limitations` 写明缺失与下一步建议，不补造文献。
- 缺软件/接口异常/限流/权限：记录到 `errors`/`degradations`，改用本地 materials/PDF 补充并标注局限；无法继续降级为 partial。
- 元数据缺失：标 `null` 并列入 `uncertainty.missing_metadata`；多来源不一致全部记录并标 `source_conflict`，不擅自取舍。
- 术语严重歧义：`term_expansion` 注明歧义并加限定词，或拆两条查询分别记录；无法判断写 `pending_human`。
- 无法完成全部子问题：`stop_decision.status=handoff`，`pending_human` 列出具体缺口。

## 7. 脚本调用（相对路径，纯标准库）

运行时 Skill 挂载于 `/skills/weekly-skill`，先 `cd /skills/weekly-skill` 再调用：

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py run --input /input --output /app/output --work /app/work --runtime-seconds 900 --runtime-requests 64
python3 -B scripts/main.py set-plan --file /app/work/plan_in.json --work /app/work
python3 -B scripts/main.py import --file /app/work/records_in.json --work /app/work
python3 -B scripts/main.py evidence --file /app/work/evidence_in.json --work /app/work
python3 -B scripts/main.py set-contract --file /app/work/contract_in.json --work /app/work
python3 -B scripts/main.py assemble --input /input --output /app/output --work /app/work --runtime-seconds 900 --runtime-requests 64
python3 -B scripts/main.py selfcheck --output /app/output --work /app/work
python3 -B scripts/selftest.py   # 提交前自检：空目录/坏JSON/坏编码/幂等/契约回填
```

复杂 JSON 用 `--file` 传文件避免 shell 转义；`--inline` 仅用于简单单行。字段规范见 `references/result-schema.md`，方法细则与检查清单见 `references/method-and-checklist.md`。

## 8. 最小调用示例（示意，非正式案例结果）

```json
{"case_id": "demo-0001", "status": "partial", "research_question": null,
 "sub_questions": [], "query_plan": [], "term_expansion": [],
 "stop_decision": {}, "results": [], "screening":
 {"included": [], "excluded": [], "uncertain": [], "merged_duplicates": []},
 "evidence": [], "sources": [], "uncertainty":
 {"missing_metadata": [], "conflicts": [], "coverage_gaps": [], "notes": []},
 "change_log": [{"time": "…", "action": "skeleton", "reason": "…", "status": "partial"}],
 "status_notes": "", "limitations": [], "errors": [], "degradations": [],
 "pending_human": [], "runtime": {"seconds": 900, "requests": 64, "started": "…"}}
```

上例仅演示字段结构与接口，`…` 为运行时的真实值；不冒充正式案例结果。
