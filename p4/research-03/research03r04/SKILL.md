---
name: sci-systematic-review
description: 把“系统性文献综述”沉淀为可复核、可降级、可复跑的流程 Skill：先拆解研究问题并写出带意图的可执行检索式，再定条款化纳排标准并保留题录/全文两轮筛选记录，对纳入研究做逐维度质量评价，最后综合出每条带逐字证据回链的结论、冲突与研究空白。本版以“降级与不确定性显式化”为核心变体：limitations/uncertainty 分级记录，证据卡带置信度与内容哈希，selfcheck 逐字核验引用，缺数据一律如实标注、绝不编造。当任务要求围绕固定研究问题完成综述、系统筛选、趋势分析或选题评估，且输入含多篇文献/题录时使用；不用于单篇精读、翻译、润色或学术事实核验。
version: 1.1.0
---

# 系统性文献综述 Skill（降级与不确定性变体）

## 1. 角色与何时使用

把“一次检索 + 一段总结”升级为别人能沿着走一遍的流程。五环节缺一不可：**检索式 → 纳排标准 → 两轮筛选记录 → 质量评价 → 带证据回链的综述结论**。判断做对只看三件事：纳入/排除的每一篇都有可查理由；每条论断都能回链到具体证据（locator + 逐字 quote）；换人复跑能得出基本一致的纳入集与结论。

本版的核心设计取舍是**把不确定性当作一等产出**：不是“结论对/错”的二元判断，而是每张证据卡带 `confidence`（high/moderate/low/insufficient），每条结论带 `strength`（established/suggestive/preliminary/not_reported），每个缺口都显式记入 `uncertainty`/`limitations`/`pending_human`。**材料里没有的数值、文献、DOI、评分一律写 `not_reported` 或列入 pending_human，绝不编造。**

- **使用**：任务含“系统性文献综述 / 系统筛选 / 证据综合 / 趋势分析 / 选题评估”，输入含多篇 PDF、题录（.csv/.jsonl/.bib）或记录文件。
- **不使用**：单篇逐句精读、全文翻译、语言润色、投稿选刊、替代学术审查做事实与方法核验。

## 2. 输入契约（运行时固定）

- 原始输入：`/input/case.json`（研究问题、材料路径、可能的 `output_contract`）+ `/input/task.md`；产物写 `/app/output/`，中间产物写 `/app/work/`。
- **先发现，再假设**：不假设固定文件名/键名。运行 `python -B scripts/main.py discover` 列清单；识别 `case.json`、`task.md` 与材料（`.pdf/.md/.txt/.csv/.json/.jsonl/.xml/.html/.tex/.bib`、图片、Office）。
- **必须读真实文件**：PDF 自行解析（平台 pdf-text-tool，或 pdftotext），提取文本存到 `/app/work/extracted/<原名>.txt` 供自检逐字核验；不信任“预抽取答案层”或文件名标签。
- 文本按 `utf-8-sig → utf-8 → gb18030 → latin-1` 降级读取；坏 JSON/坏编码/缺文件不崩溃，记入 `errors`/`degradations`，继续处理其余材料；单文件 >2MB 截断并记 `degradations`。
- 预算：**900 秒、64 次模型请求、每响应 ≤16384 token（含推理）**。

## 3. 输出契约（必须满足）

`/app/output/result.json` 必须存在且为合法 JSON，包含：

1. `case_id`：取 `case.json` 的 `case_id/caseId/id/armiId/challenge_id`，缺失填 `unknown`。
2. **题目 `output_contract` 的全部字段**：原样回填键名与类型，缺值填 `null`/`[]`，不改名、不删未知键（`skeleton` 自动发现）。
3. 追加诚实字段：`status`、`sources`、`uncertainty`、`change_log`、`limitations`、`errors`、`degradations`、`pending_human`、`runtime_seconds`。

领域字段：`review_question`、`subquestions`、`search_strategy{query_blocks}`、`inclusion_criteria`、`exclusion_criteria`、`screening_records`、`prisma_flow`、`quality_appraisal`、`synthesis{conclusions,conflicts,gaps}`、`evidence_map`、`uncertainty`。

`status` 取值：`ok`（五环节完成且证据可回链）/ `partial`（部分完成，须列缺口）/ `insufficient_evidence`（材料不足，不编造）/ `input_error`（输入不可解析）。

配套可复核产物（写 `/app/output/`）：`review.md`（人类可读摘要）、`screening_log.json`、`evidence_map.json`。

## 4. 执行流程（每步输入→输出，共 900 秒）

**铁律：前 3 分钟先落盘骨架，之后只做增量改进；任何异常保留已落盘 partial，禁止清空重建。**

| 阶段 | 时间窗 | 请求预算 | 输入 → 输出 |
|---|---|---|---|
| P0 骨架 | 0–3 min | ~3 | 读 case.json/task.md + 扫描材料 → `skeleton` 落盘 result.json（status=partial）+ run_state.json |
| P1 问题与协议 | 3–12 min | ~7 | 读题面与材料 → 写 `/app/work/protocol.json`：review_question、subquestions、search_strategy.query_blocks（可执行检索式）、inclusion/exclusion_criteria（条款化，先于筛选落盘） |
| P2 两轮筛选 | 12–30 min | ~20 | 逐篇判读 → 追加 `/app/work/screening.jsonl`（题录 + 全文两轮，每篇含 decision/reason/criterion_id/judge，去重记 `duplicate_of`） |
| P3 质量与证据 | 30–42 min | ~12 | 对纳入研究 → `/app/work/quality_appraisal.jsonl`（逐维度 + basis_locator + quote + 等级 + 对结论影响）；抽取证据 → `/app/work/evidence_cards.jsonl`（locator + 逐字 quote + confidence + type） |
| P4 综合与收尾 | 42–56 min | ~12 | 写 `/app/work/synthesis.json`（conclusions/conflicts/gaps/uncertainty/limitations/pending_human）→ `finalize` 组装并重算 PRISMA → `selfcheck` 逐字核验证据、修复问题、如实定 status |
| 缓冲 | 56–60 min | ~6 | 补缺口/修 validation 错误；仍缺则 `partial` 或 `insufficient_evidence` |

每阶段结束都增量写盘；模型实质内容写进 `/app/work/`，由脚本确定性组装，避免手工改 JSON 出错。

## 5. 脚本调用（相对路径，仅标准库，根目录执行）

```bash
python -B scripts/main.py discover       # 输入清单
python -B scripts/main.py skeleton       # 早期骨架 + run_state.json
python -B scripts/main.py merge --payload /app/work/payload.json
python -B scripts/main.py finalize       # 组装 result.json + review.md + screening_log.json + evidence_map.json
python -B scripts/main.py selfcheck      # 覆盖多维度 + 筛选重算 + PRISMA + 证据逐字核验
python -B scripts/selftest.py            # 离线自检（空输入/坏JSON/坏编码/完整案例）
# 通用参数：--input /input --output /app/output --work /app/work
```

脚本用 `os.path.dirname(__file__)` 定位 `resultlib.py`，不依赖当前工作目录；若环境无 `python3`，用 `python -B`。

## 6. 证据溯源与不确定性模型（本版重点）

- **证据卡**（`evidence_cards.jsonl`）每行至少含 `evidence_id`、`record_id`、`claim`、`locator`、`quote`（逐字取自材料）、`confidence`、`type`（measured/reported/inferred/not_reported）。
- **逐字核验**：`selfcheck` 会重新读取 `/app/work/extracted/` 与材料文本，检查每张证据卡的 quote 是否逐字出现（空白归一后子串匹配）；未命中的 quote 记为 `no_verbatim_match`，必须修正为逐字引用，或标 `verbatim:false` 并降级 confidence，不得当作确证呈现。
- **不确定性分类**：每条 `uncertainty` 用 `type` + `item` + `effect` + `handling` 四字段，type 至少覆盖：`source_conflict / missing_fulltext / abstract_only / extraction_failed / single_study / date_gap / not_reported / language_limit`。
- **结论强度**：每条 conclusion 写 `strength` 与 `scope`，强度由支持它的证据数量、全文可得性与一致性决定；单一研究、仅摘要、证据冲突分别降为 `suggestive / preliminary / conflicting`，不越界写 `established`。
- **冲突并列**：`conflicts` 每条并列 `source_a` 与 `source_b`，`resolution` 可为 `undetermined`（无法裁定时不强取一方）。

## 7. 自检清单（对应评分维度，逐条勾选）

- [ ] 问题拆解：`review_question` 非空，`subquestions` ≥1（推荐 ≥2）且带依赖。
- [ ] 检索式：每个 `query_blocks` 含 database+query+intent+时间/语言限制，含布尔逻辑，不退化成一个关键词串。
- [ ] 纳排标准：条款化、可判定、含优先级，且在筛选决定**之前**落盘。
- [ ] 筛选记录：两轮逐条 decision，每条 exclude 有 `reason+criterion_id`，计数可重算。
- [ ] 质量评价：逐维度 `basis_locator+quote` + 总体等级 + 对结论的影响。
- [ ] 证据回链：每条结论 `evidence_ids` 命中 `evidence_map`，每行有 `locator+quote` 且逐字取自材料（selfcheck 已核验）。
- [ ] 冲突与空白：`conflicts` 逐条并列两个来源；`gaps` 落位到具体缺口（kind+description+impact）。
- [ ] 综合结论：`conclusions` 非空且与研究问题、证据范围一致，不越界。
- [ ] 完整合法：`selfcheck` 报 `error_count == 0`；result.json 可被 `json.load` 解析。
- [ ] 异常处理：空输入/坏 JSON/坏编码/检索为空/全文不可得/条款冲突/证据矛盾均记录且退出码 0。
- [ ] 可复用：纳排标准、检索式模板、质量量表、筛选表均可被他人复用。

## 8. 降级策略（诚实标注，禁止编造）

- 材料不足/空输入 → `status=insufficient_evidence`，`pending_human` 列出缺口，不生成示例填充。
- 仅有摘要/无全文 → 该篇标 `abstract_only`，结论降为 `suggestive`/`not_reported`，不补齐正文数据。
- 数据库不可用/超时 → 检索块标 `not_run`，记 `degradations`，不伪造 `hits`。
- PDF 无文本层/低质量图 → 标 `extraction_failed`，建议 pdf-text-tool/OCR 或人工，不硬读像素值。
- 缺软件/缺权限 → 记入 `limitations`/`errors`，降级为最小合法产物，进程不崩溃。
- 冲突无法裁定 → 并列记录 + `resolution:undetermined`，不强行取一方。
- 缺字段/未知字段 → 保留键填 `null`/`[]`，记 `errors`/`degradations`；**绝不编造文献/DOI/数值、评测分数或运行结果**。
- 证据无法逐字核验 → `selfcheck` 报 `no_verbatim_match`，修正引用或降级 confidence；不把未核验引用当确证。
- 重复运行 → 固定证据 id、`sort_keys` 确定性写入，同一输入两次运行结果一致。

## 9. 边界

- 一个 Skill 只解决“系统性文献综述”这一件事；不代写不存在的实验/数据；不生成虚假参考文献。
- 不预置正式测试案例答案、不伪造 benchmark 分数；真实分数与运行痕迹由平台记录。
- 文献结论只做“材料内是否有支持”的判定，不越界判定其真伪；综述结论不越过证据范围扩展。
- 本包 3 个脚本只用标准库、路径相对、无外部依赖；详见 `references/protocol.md`（协议与量表）与 `references/checklist.md`（运行清单）。
