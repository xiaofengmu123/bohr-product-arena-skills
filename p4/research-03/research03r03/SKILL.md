---
name: sci-sysreview-deterministic
description: 把“系统性文献综述”沉淀为可复核、确定性的流程 Skill：先拆解研究问题并写带意图的可执行检索式，再定条款化纳排标准并保留题录与全文两轮筛选记录，对纳入研究做可解释质量评价，最后综合出带逐字证据回链的结论、冲突与空白。强调自检与确定性——scripts/selftest.py 校验同输入两次产物字节一致，scripts/main.py selfcheck 做纳排一致性与逐字引文审计。当任务要求围绕固定研究问题完成系统综述、系统筛选、趋势分析或选题评估，且输入含多篇文献或题录时使用；不用于单篇精读、翻译、润色或学术事实核验。
version: 2.0.0
---

# 可复核的系统性文献综述 Skill（确定性版）

## 1. 角色与何时使用

把“一次检索 + 一段总结”升级为别人能沿着走一遍的流程。五个环节缺一不可：**检索式 → 纳排标准 → 两轮筛选记录 → 质量评价 → 带证据回链的综合结论**。判断做对只看三件事：纳入/排除的每一篇都有可查理由；综述中每条论断都能回链到具体证据（locator + 逐字 quote）；换人复跑能得出基本一致的纳入集与结论。

本版与普通综述 Skill 的核心差别是**确定性**：同一输入、同一组工作记录，两次运行产物字节一致（见第 6 节与 `scripts/selftest.py`）。模型只负责产出 `/app/work/` 下的结构化工作文件，`result.json` 由脚本确定性组装，避免手工改 JSON 与墙钟时间引入的漂移。

- **使用**：任务说明含“系统性文献综述 / 系统筛选 / 证据综合 / 趋势分析 / 选题评估”，输入含多篇 PDF、题录（`.csv/.json/.jsonl/.bib`）或记录文件。
- **不使用**：单篇逐句精读、全文翻译、语言润色、投稿选刊、替代学术审查做事实与方法核验。

## 2. 输入契约（运行时固定）

- 原始输入：`/input/case.json`（研究问题、材料路径、可能的 `output_contract`）+ `/input/task.md`。
- **先发现，再假设**：不假设固定文件名/键名。运行 `python -B scripts/main.py discover` 列出清单；识别 `case.json`、`task.md` 与材料（`.pdf/.md/.txt/.csv/.json/.jsonl/.xml/.html/.tex/.bib`、图片、Office）。
- **必须读真实文件**：PDF 用平台 pdf-text-tool/OCR 自行解析，不信任“预抽取答案层”或文件名标签；单文件 >2MB 截断并记 `degradations`。
- 文本按 `utf-8-sig` → `gb18030` → `latin-1` 顺序降级读取；坏 JSON/坏编码/缺文件不崩溃，记入 `errors`/`degradations`，继续处理其余材料。
- 工作目录 `/app/work`（中间产物）；最终产物与可编辑文件写 `/app/output/`。
- 预算：**900 秒、64 次模型请求、每响应 ≤16384 token（含推理）**。**前 3 分钟必须已落盘一版合法 result.json**，之后只做增量改进。

## 3. 输出契约（必须满足）

`/app/output/result.json` 必须存在且为合法 JSON，包含：

1. `case_id`：取 `case.json` 的 `case_id/caseId/id/armiId/challenge_id`，缺失填 `unknown`。
2. **题目 `output_contract` 的全部字段**：原样回填键名与类型，缺值填 `null`/`[]`，不改名、不删未知键；脚本 `skeleton` 自动发现这些键。
3. 追加诚实字段：`status`、`sources`、`uncertainty`、`change_log`（另含 `limitations/errors/degradations/pending_human/runtime_seconds`）。

领域字段（详见 `references/quality-appraisal.md` 与 `references/checklist.md`）：

```text
review_question, subquestions, search_strategy{query_blocks},
inclusion_criteria, exclusion_criteria, screening_records, prisma_flow,
quality_appraisal, synthesis{conclusions,conflicts,gaps}, evidence_map, uncertainty
```

`status` 取值：`ok`（五环节完成且证据可回链）/ `partial`（部分完成，须列缺口）/ `insufficient_evidence`（材料不足，不编造）/ `input_error`（输入不可解析）。

配套可复核产物（写 `/app/output/`）：`review.md`（人类可读摘要）、`screening_log.json`、`evidence_map.json`。

## 4. 执行流程（每步输入 → 输出，共 900 秒 / 64 请求）

**铁律：前 3 分钟先落盘骨架，之后只做增量改进；任何异常保留已落盘 partial，禁止清空重建。** 模型实质内容写进 `/app/work/`，脚本确定性组装。

| 阶段 | 时间窗 | 请求预算 | 输入 → 输出 |
|---|---|---|---|
| P0 骨架 | 0–3 min | ~3 | 读 case.json/task.md + 扫描材料 → `python -B scripts/main.py skeleton` 落盘 result.json（status=partial）+ run_state.json |
| P1 问题与协议 | 3–12 min | ~7 | 读题面与材料 → 写 `/app/work/protocol.json`：review_question、subquestions、search_strategy.query_blocks（可执行检索式）、inclusion/exclusion_criteria（条款化，先于筛选） |
| P2 两轮筛选 | 12–30 min | ~20 | 逐篇判读 → 追加 `/app/work/screening.jsonl`（题录 + 全文两轮，每篇含 decision/reason/criterion_id/judge），去重记 `duplicate_of` |
| P3 质量与证据 | 30–42 min | ~12 | 对纳入研究 → `/app/work/quality_appraisal.jsonl`（逐维度 + basis_locator + quote + 等级 + 对结论影响）；抽取证据 → `/app/work/evidence.jsonl`（locator+quote 逐字） |
| P4 综合与收尾 | 42–56 min | ~12 | 写 `/app/work/synthesis.json`（conclusions/conflicts/gaps）→ `python -B scripts/main.py finalize` 组装并重算 PRISMA → `python -B scripts/main.py selfcheck` 修复问题、如实定 status |
| 缓冲 | 56–60 min | ~6 | 补缺口/修 validation 错误；仍缺则 `partial` 或 `insufficient_evidence` |

每个阶段结束都增量写盘。工作文件 schema（脚本按此读取）：

- `protocol.json`：`review_question`、`subquestions[]`、`search_strategy.query_blocks[]`（含 database/fields/query/intent/date_limit/language_limit）、`inclusion_criteria[]`、`exclusion_criteria[]`（每条 id/criterion/kind/priority）、`sources[]`（source_id/file/title）。
- `screening.jsonl`：一行一条，含 `record_id/source_id/stage(title|fulltext)/decision/reason/criterion_id/judge/duplicate_of`。
- `quality.jsonl`：一行一条，含 `record_id/source_id/overall/dimensions[{dimension,rating,basis_locator,quote}]/impact_on_conclusions`。
- `evidence.jsonl`：一行一条，含 `source_id/locator/quote/claim/kind`（`evidence_id` 由脚本内容寻址生成）。
- `synthesis.json`：`conclusions[{claim,evidence_ids,confidence,scope}]`、`conflicts[{source_a,source_b,quote_a,quote_b,resolution}]`、`gaps[{gap,scope,evidence_status}]`、`uncertainty`。
- `status_override.txt`（可选）：首行写 `ok/partial/insufficient_evidence/input_error` 覆盖自动判定，否则脚本按完成度推导。

## 5. 脚本调用（相对路径，仅标准库）

```bash
python -B scripts/main.py discover       # 输入清单
python -B scripts/main.py skeleton       # 早期骨架 + run_state.json
python -B scripts/main.py merge --payload /app/work/payload.json
python -B scripts/main.py finalize       # 确定性组装 result.json + review.md + screening_log.json + evidence_map.json
python -B scripts/main.py selfcheck      # 契约 + 筛选审计 + PRISMA 重算 + 逐字引文 + 确定性回环
python -B scripts/main.py selfcheck --json   # 机器可读报告
python -B scripts/selftest.py            # 提交前自检：正向流程 + 确定性 + 三类异常
# 通用参数（缺省指向运行时路径）：--input /input --output /app/output --work /app/work
```

在 Skill 根目录执行；脚本用 `os.path.dirname(__file__)` 定位 `core_impl.py`，不依赖当前工作目录。若环境无 `python3`，用 `python -B`。

## 6. 自检清单与确定性保证

自检分两层：`selfcheck` 在运行时对单次产物做机械审计，`selftest.py` 在提交前验证整条流程（4 项，须全 PASS）。逐条可勾选清单见 `references/checklist.md`，质量量表定义见 `references/quality-appraisal.md`。

- [ ] 问题拆解：`review_question` 非空，`subquestions` ≥1（推荐 ≥2）且带依赖。
- [ ] 检索式：每个 `query_blocks` 含 database+query+intent+时间/语言限制，不退化成一个关键词串。
- [ ] 纳排标准：条款化、可判定、含优先级，且在筛选决定**之前**落盘。
- [ ] 筛选记录：两轮逐条 decision，每条 exclude 有 `reason+criterion_id`，计数可重算（`selfcheck` 会复核 `criteria_consistency` 与 `prisma_recount`）。
- [ ] 质量评价：逐维度 `basis_locator+quote` + 总体等级 + 对结论的影响，不只写“质量较高”。
- [ ] 证据回链：每条结论 `evidence_ids` 命中 `evidence_map`，每行有 `locator+quote` 且逐字取自材料。
- [ ] 冲突与空白：`synthesis.conflicts` 逐条并列两个来源；`synthesis.gaps` 落位到具体缺口。
- [ ] 综合结论：`synthesis.conclusions` 非空且与研究问题、证据范围一致，不越界。
- [ ] 完整合法：`selfcheck` 通过；result.json 可被 `json.load` 解析。
- [ ] 异常处理：空输入/坏 JSON/坏编码/检索为空/全文不可得/条款冲突/证据矛盾均记录且退出码 0。
- [ ] 确定性：`selftest.py` 4/4 PASS，同输入两次产物字节一致。
- [ ] 可复用：纳排标准、检索式模板、质量量表、筛选表均可被他人复用。

**确定性实现规则**：证据 ID 由 `sha256(source_id, locator, quote)` 内容寻址；JSON 一律 `sort_keys` 序列化；结果不含墙钟时间、随机数、绝对输出路径；实际耗时与请求计数属遥测，只写 `/app/work/run_state.json`，`result.json` 的 `runtime_seconds` 恒为 `null`。

## 7. 降级策略（诚实标注，禁止编造）

- 材料不足/空输入 → `status=insufficient_evidence`，`pending_human` 列出缺口，不生成示例填充。
- 仅有摘要/无全文 → 该篇标 `abstract_only`，结论降为 `suggestive`/`not_reported`，不补齐正文数据。
- 数据库不可用/超时 → 检索块标 `not_run`，记 `degradations`，不伪造 `hits`。
- PDF 无文本层/低质量图 → 标 `extraction_failed`，建议平台 pdf-text-tool/OCR 或人工，不硬读像素值。
- 缺软件/缺权限 → 记入 `limitations`/`errors`，降级为最小合法产物，进程不崩溃。
- 冲突无法裁定 → 并列记录 + 标 `undetermined`，不强行取一方。
- 缺字段/未知字段 → 保留键填 `null`/`[]`，记 `errors`/`degradations`；**绝不编造文献/DOI/数值、评测分数或运行结果**。
- 引文校验失败 → `selfcheck` 记 `quote_verbatim` 告警并降级该条证据，不静默通过。

## 8. 边界

- 一个 Skill 只解决“系统性文献综述”这一件事；不代写不存在的实验/数据；不生成虚假参考文献。
- 不预置正式测试案例答案、不伪造 benchmark 分数；真实分数与运行痕迹由平台记录。
- 文献结论只做“材料内是否有支持”的判定，不越界判定其真伪；综述结论不越过证据范围扩展。
