---
name: sci-evidence-review
description: 把“系统性文献综述”沉淀为可复核的“证据优先”流程 Skill：先拆解研究问题并写出带意图的可执行检索式，再定条款化纳排标准并保留题录/全文两轮筛选记录，对纳入研究做可解释质量评价，最后从证据台账综合出带逐字证据回链（locator + quote）的结论、冲突与空白。当任务要求围绕固定研究问题完成综述、系统筛选、趋势分析或选题评估，且输入含多篇文献/题录时使用；不用于单篇精读、翻译、润色或学术事实核验。
version: 1.0.0
---

# 证据优先的系统性文献综述 Skill

## 1. 角色与何时使用

把“一次检索 + 一段总结”升级为别人能复跑一遍的流程。五环节缺一不可：**检索式 → 纳排标准 → 两轮筛选记录 → 质量评价 → 带证据回链的综述结论**。本版的设计取舍是**证据优先**：结论不是自由文本，而是证据台账的投影——每条证据都有 `evidence_id + locator + 逐字 quote + claim_type`，每条结论必须列出支撑它的 `evidence_ids` 并在 `basis` 里内联 locator+quote；脚本会**重开源文件核对 quote 是否为逐字子串**，对不上就标 `quote_mismatch` 并降级置信度，绝不静默信任。

- **使用**：任务含“系统性文献综述 / 系统筛选 / 证据综合 / 趋势分析 / 选题评估”，输入含多篇 PDF、题录（`.csv/.jsonl/.bib/.ris/.enw`）或记录文件。
- **不使用**：单篇逐句精读、全文翻译、语言润色、投稿选刊，或替代学术审查做事实与方法核验。

## 2. 输入契约（运行时固定）

- 原始输入：`/input/case.json`（研究问题、材料路径、可能的 `output_contract`）+ `/input/task.md`。
- **先发现，再假设**：不假设固定文件名/键名。运行 `python -B scripts/main.py discover` 列清单，识别 `case.json`、`task.md` 与材料（`.pdf/.md/.txt/.csv/.json/.jsonl/.xml/.html/.tex/.bib/.ris/.enw`、图片、Office）。
- **必须读真实文件**：PDF 用平台 pdf-text-tool/OCR 自行解析，不信任“预抽取答案层”或文件名标签；单文件 >2MB 截断并记 `degradations`。
- 文本按 `utf-8-sig` → `gb18030` → `latin-1` 顺序降级读取；坏 JSON/坏编码/缺文件不崩溃，记入 `errors`/`degradations`，继续处理其余材料。
- 工作目录 `/app/work`（中间产物）；最终产物与可编辑文件写 `/app/output/`。
- 预算：**900 秒、64 次模型请求、每响应 ≤16384 token（含推理）**。**前 3 分钟必须先落盘一版合法 result.json（status=partial）**，之后只做增量改进，任何异常保留已落盘的 partial，禁止清空重建。

## 3. 输出契约（必须满足）

`/app/output/result.json` 必须存在且为合法 JSON，包含：

1. `case_id`：取 `case.json` 的 `case_id/caseId/id/armiId/challenge_id`，缺失填 `unknown`。
2. **题目 `output_contract` 的全部字段**：原样回填键名与类型，缺值填 `null`/`[]`，不改名、不删未知键；脚本 `skeleton` 自动发现这些键。
3. 追加诚实字段：`status`、`sources`、`uncertainty`、`change_log`、`limitations`、`errors`、`degradations`、`pending_human`、`runtime_seconds`。

领域字段（详见 `references/output-contract.md`）：`review_question`、`subquestions`、`search_strategy{query_blocks}`、`inclusion_criteria`、`exclusion_criteria`、`screening_records`、`prisma_flow`、`quality_appraisal`、`synthesis{conclusions,conflicts,gaps}`、`evidence_map{evidence,by_source,by_conclusion,conflicts}`、`uncertainty`。

`status` 取值：`ok`（五环节完成且证据可回链）/ `partial`（部分完成，须列缺口）/ `insufficient_evidence`（材料不足，不编造）/ `input_error`（输入不可解析）。

配套可复核产物（均写 `/app/output/`）：`review.md`（人类可读摘要，结论内联 locator+quote）、`screening_log.json`、`evidence_map.json`；来自 `/app/work` 的协议/综合笔记保留备查。

## 4. 执行流程（每步输入→输出，共 900 秒）

**铁律：前 3 分钟先落盘骨架，之后只做增量改进；任何异常保留已落盘 partial，禁止清空重建。**

| 阶段 | 时间窗 | 请求预算 | 输入 → 输出 |
|---|---|---|---|
| P0 骨架 | 0–3 min | ~3 | 读 case.json/task.md + 扫描材料 → `python -B scripts/main.py skeleton` 落盘 result.json（status=partial）+ run_state.json |
| P1 问题与协议 | 3–12 min | ~7 | 读题面与材料 → 写 `/app/work/protocol.json`（review_question、subquestions、search_strategy.query_blocks、inclusion/exclusion_criteria，条款先于筛选）→ `validate --kind protocol` |
| P2 两轮筛选 | 12–30 min | ~20 | 逐篇判读 → 追加 `/app/work/screening.jsonl`（题录+全文两轮，每条含 decision/reason/criterion_id/judge，去重记 `duplicate_of`）→ `validate --kind screening` |
| P3 质量与证据 | 30–42 min | ~12 | 对纳入研究 → `/app/work/quality_appraisal.jsonl`（逐维度 + basis_locator + quote + 等级 + 对结论影响）；抽证据 → `/app/work/evidence.jsonl`（evidence_id + locator + 逐字 quote + claim_type + confidence）→ `validate --kind appraisal/evidence` |
| P4 综合与收尾 | 42–56 min | ~12 | 写 `/app/work/synthesis.json`（conclusions/conflicts/gaps，每条结论带 evidence_ids 与内联 basis）→ `python -B scripts/main.py finalize` 组装并核对 quote → `selfcheck` 修复、如实定 status |
| 缓冲 | 56–60 min | ~6 | 补缺口/修 validation 错误；仍缺则 `partial` 或 `insufficient_evidence` |

每个阶段结束都增量写盘；模型实质内容写进 `/app/work/`，由脚本确定性组装。也可用 `python -B scripts/main.py merge --payload /app/work/payload.json` 增量合并。

## 5. 脚本调用（相对路径，仅标准库）

```bash
python -B scripts/main.py discover       # 输入清单
python -B scripts/main.py skeleton       # 早期骨架 + run_state.json
python -B scripts/main.py validate --kind protocol|screening|appraisal|evidence|synthesis
python -B scripts/main.py merge --payload /app/work/payload.json
python -B scripts/main.py finalize       # 组装 result.json + review.md + screening_log.json + evidence_map.json
python -B scripts/main.py selfcheck      # 全量检查 + 逐字 quote 核对 + 筛选审计 + PRISMA 重算
# 通用参数（缺省指向运行时路径）：--input /input --output /app/output --work /app/work
```

在 Skill 根目录执行；脚本用 `os.path.dirname(__file__)` 定位 `resultlib.py`，不依赖当前工作目录。若环境无 `python3`，用 `python -B`。

## 6. 证据溯源规范（本版核心，务必遵守）

- **证据是一等公民**：所有实质判断先落成 `evidence.jsonl` 一行，字段见 `references/evidence-protocol.md`；`claim_type ∈ {fact, estimate, inference, not_reported}` 强制区分“材料事实 / 合理推断 / 无法确定”。
- **locator** 为 `{"file": "相对 /input 的路径", "page": 可选, "anchor": 可选段落}` 或字符串 `"file::page::anchor"`；`quote` 必须**逐字**取自材料（仅允许合并换行/空白，不得改写、补字或换词）。
- **结论回链**：`synthesis.conclusions[i]` 必须含 `conclusion_id / statement / evidence_ids / support_level / boundary / basis`；`basis` 逐条内联 `evidence_id + role + locator + quote`，使 `review.md` 可直接呈现证据而不必再查表。
- **自动核对**：`finalize` 与 `selfcheck` 会重开源文件核对 `quote` 是否为逐字子串；对不上标 `quote_mismatch`，`extraction_status` 由 `verbatim` 降为 `not_verified`、`confidence` 降为 `low`。**宁可标注，不可假装逐字**。
- **冲突矩阵**：`synthesis.conflicts` 每条并列 ≥2 个 `evidence_ids` 并说明分歧类型（contradiction/divergence/terminology）；`evidence_map.conflicts` 与其一致。

## 7. 自检清单（对应评分维度，逐条勾选）

- [ ] 问题拆解：`review_question` 非空，`subquestions` ≥1（推荐 ≥2）且带依赖。
- [ ] 检索式：每个 `query_blocks` 含 database+query+intent+字段+时间/语言限制，不退化成一个关键词串。
- [ ] 纳排标准：条款化、可判定、含优先级，且在筛选决定**之前**落盘。
- [ ] 筛选记录：题录+全文两轮逐条 decision，每条 exclude 有 `reason+criterion_id`，计数可重算。
- [ ] 质量评价：逐维度 `basis_locator+quote` + 总体等级 + 对结论的影响，不只写“质量较高”。
- [ ] 证据回链：每条结论 `evidence_ids` 命中 `evidence_map`，`basis` 内联 locator+逐字 quote。
- [ ] 逐字核对：`selfcheck` 的 `quote_verification` 无 `quote_mismatch`（否则已如实降级标注）。
- [ ] 冲突与空白：`synthesis.conflicts` 逐条并列两个来源；`synthesis.gaps` 落位到具体缺口。
- [ ] 综合结论：`synthesis.conclusions` 非空且与研究问题、证据范围一致，不越界。
- [ ] 完整合法：`python -B scripts/main.py selfcheck` 各检查项通过；result.json 可被 `json.load` 解析。
- [ ] 异常处理：空输入/坏 JSON/坏编码/检索为空/全文不可得/条款冲突/证据矛盾均记录且退出码 0。
- [ ] 可复用：纳排标准、检索式模板、质量量表、筛选表、证据表均可被他人复用。

## 8. 降级策略（诚实标注，禁止编造）

- 材料不足/空输入 → `status=insufficient_evidence`，`pending_human` 列出缺口，不生成示例填充。
- 仅有摘要/无全文 → 该篇标 `abstract_only`，结论降为 `suggestive`/`not_reported`，不补齐正文数据。
- 数据库不可用/超时 → 检索块标 `not_run`，记 `degradations`，不伪造 `hits`。
- PDF 无文本层/低质量图 → 标 `extraction_failed`，建议平台 pdf-text-tool/OCR 或人工，不硬读像素值。
- 缺软件/缺权限 → 记入 `limitations`/`errors`，降级为最小合法产物，进程不崩溃。
- 冲突无法裁定 → 并列记录 + 标 `undetermined`，不强行取一方。
- 缺字段/未知字段 → 保留键填 `null`/`[]`，记 `errors`/`degradations`；**绝不编造文献/DOI/数值、评测分数或运行结果**。
- quote 无法逐字核对 → 标 `quote_mismatch/no_text_layer/file_unavailable` 并降级，不改为“看似逐字”。
- 重复运行 → 固定证据 id、固定字段顺序、确定性组装，同一输入两次运行结果一致。

## 9. 边界

- 一个 Skill 只解决“系统性文献综述”这一件事；不代写不存在的实验/数据；不生成虚假参考文献。
- 不预置正式测试案例答案、不伪造 benchmark 分数；真实分数与运行痕迹由平台记录。
- 文献结论只做“材料内是否有支持”的判定，不越界判定其真伪；综述结论不越过证据范围扩展。

## 10. 最小调用示例（示意，非正式案例）

```bash
cd /skills/weekly-skill
python -B scripts/main.py discover                      # 列出 /input 清单
python -B scripts/main.py skeleton                      # 落盘 partial 骨架
# Agent 读材料后写 /app/work/protocol.json、screening.jsonl、quality_appraisal.jsonl、
# evidence.jsonl、synthesis.json（内容由真实材料决定，此处仅为接口示意）
python -B scripts/main.py validate --kind evidence      # 阶段校验
python -B scripts/main.py finalize                      # 组装 result.json + review.md + screening_log.json + evidence_map.json
python -B scripts/main.py selfcheck                     # 自检，输出 {"pass": N, "fail": M}
```

示意 `result.json`（片段，非正式案例输出）：

```json
{"case_id": "case-xxx", "status": "partial", "review_question": null,
 "evidence_map": {"evidence": [], "by_source": {}, "by_conclusion": {}, "conflicts": []},
 "sources": [], "uncertainty": {}, "change_log": [{"action": "skeleton"}],
 "limitations": [], "pending_human": []}
```

## 11. 维护信息

- 依赖：仅 Python 3 标准库（`argparse/json/os/sys/time/re/hashlib/copy`），无需 pip 安装。
- 脚本入口：`scripts/main.py`（编排）→ `scripts/resultlib.py`（IO/契约/骨架）与 `scripts/selftest.py`（证据校验/自检，可独立运行）。
- 版本：`SKILL.md` frontmatter 的 `version`；运行痕迹由 `change_log` 与平台记录，不伪造。
- 更新规范：改字段必同步 `references/output-contract.md` 与 `references/evidence-protocol.md`；改动后运行 `python -B scripts/selftest.py --input ./demo --output ./demo/out --work ./demo/work` 回归。
