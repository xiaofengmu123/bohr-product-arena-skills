---
name: sci-systematic-review
description: 把“系统性文献综述”沉淀为可复核、可重放的流程 Skill：先拆解研究问题并写出带意图的可执行检索式，再定条款化纳排标准并保留题录/全文两轮筛选记录，对纳入研究做可解释质量评价，最后综合出带逐字证据回链的结论、冲突与空白。当任务要求围绕固定研究问题完成综述、系统筛选、证据综合、趋势分析或选题评估，且输入含多篇文献或题录时使用；不用于单篇精读、翻译、润色或替代学术审查做事实核验。
version: 2.0.0
---

# 系统性文献综述 Skill

## 1. 角色与何时使用

把“一次检索 + 一段总结”升级为别人能沿原路重走一遍的流程。五环节缺一不可：**检索式 → 纳排标准 → 两轮筛选记录 → 质量评价 → 带证据回链的综述结论**。判断做对只看三件事：纳入/排除的每一篇都有可查理由；综述中每条论断都能回链到具体证据（locator + 逐字 quote）；换人复跑能得出基本一致的纳入集与结论。

- **使用**：任务说明含“系统性文献综述 / 系统筛选 / 证据综合 / 趋势分析 / 选题评估”，输入含多篇 PDF、题录（.csv/.jsonl/.bib/.ris）或记录文件。
- **不使用**：单篇逐句精读、全文翻译、语言润色、投稿选刊、替代学术审查做事实与方法核验。

## 2. 输入契约（运行时固定）

- 原始输入：`/input/case.json`（研究问题、材料路径、可能的 `output_contract`）+ `/input/task.md`。
- **先发现，再假设**：不假设固定文件名/键名。先运行 `python -B scripts/main.py discover` 列出清单；识别 `case.json`、`task.md` 与材料。
- **必须读真实文件**：PDF 自行解析（用平台 pdf-text-tool/OCR），不信任“预抽取答案层”或文件名标签；单文件 >2MB 截断并记 `degradations`。
- 文本按 `utf-8-sig` → `gb18030` → `latin-1` 顺序降级读取；坏 JSON/坏编码/缺文件不崩溃，记入 `errors`/`degradations`，继续处理其余材料。
- 工作目录 `/app/work`（中间产物）；最终产物与可编辑文件写 `/app/output/`。
- 预算：**900 秒、64 次模型请求、每响应 ≤16384 token（含推理）**。**前 3 分钟必须已落盘一版合法 result.json**。

## 3. 输出契约（必须满足）

`/app/output/result.json` 必须存在且为合法 JSON，包含：

1. `case_id`：取 `case.json` 的 `case_id/caseId/id/armiId/challenge_id`，缺失填 `unknown`。
2. **题目 `output_contract` 的全部字段**：原样回填键名与类型，缺值填 `null`/`[]`，不改名、不删未知键；`skeleton` 自动发现这些键。
3. 追加诚实字段：`status`、`sources`、`uncertainty`、`change_log`（另含 `limitations/errors/degradations/pending_human/runtime_seconds`）。

领域字段（schema 详见 `references/protocol.md`）：

```text
review_question, subquestions, search_strategy{query_blocks},
inclusion_criteria, exclusion_criteria, screening_records, prisma_flow,
quality_appraisal, synthesis{conclusions,conflicts,gaps}, evidence_map, uncertainty
```

`status` 取值：`ok`（五环节完成且证据可回链）/ `partial`（部分完成，须列缺口）/ `insufficient_evidence`（材料不足，不编造）/ `input_error`（输入不可解析）。

**关键架构约定：`result.json` 是派生产物，绝不手工编辑。** 中间过程只写 `/app/work/` 下的规范工作文件（`protocol.json`、`sources.json`、`screening.jsonl`、`quality.jsonl`、`evidence.jsonl`、`synthesis.json`），由 `finalize` 确定性重放生成 `result.json` + `review.md` + `screening_log.json` + `evidence_map.json`，人读与机读产物同源、不会漂移。

## 4. 执行流程（每步输入→输出，共 900 秒）

**铁律：前 3 分钟先落盘骨架，之后只做增量改进；任何异常保留已落盘 partial，禁止清空重建。**

| 阶段 | 时间窗 | 请求预算 | 输入 → 输出 |
|---|---|---|---|
| P0 骨架 | 0–3 min | ~3 | 读 case.json/task.md + 扫描材料 → `python -B scripts/main.py skeleton` 落盘 result.json（partial）+ run_state.json |
| P1 问题与协议 | 3–12 min | ~7 | 读题面与材料 → 写 `/app/work/protocol.json`：review_question、subquestions、search_strategy.query_blocks（可执行检索式）、inclusion/exclusion_criteria（条款化，先于筛选） |
| P2 两轮筛选 | 12–30 min | ~20 | 逐篇判读 → 用 `record --kind screening` 追加题录/全文两轮记录（每篇 decision/reason/criterion_id/judge，去重记 duplicate_of） |
| P3 质量与证据 | 30–42 min | ~12 | 对纳入研究 `record --kind quality`（逐维度 basis_locator+quote+等级+对结论影响）；抽取证据 `record --kind evidence`（locator+逐字 quote） |
| P4 综合与收尾 | 42–56 min | ~12 | 写 `/app/work/synthesis.json`（conclusions/conflicts/gaps）→ `python -B scripts/main.py finalize` 重放并重算 PRISMA → `selfcheck` 修复问题、如实定 status |
| 缓冲 | 56–60 min | ~6 | 补缺口/修校验错误；仍缺则 `partial` 或 `insufficient_evidence` |

每个阶段结束都增量写盘；模型实质内容写进 `/app/work/`，由脚本确定性组装。推荐用 `record` 写入（写时校验），也可直接写工作文件后 `finalize` 重放。

## 5. 脚本调用（相对路径，仅标准库）

```bash
python -B scripts/main.py discover       # 输入清单
python -B scripts/main.py skeleton       # 早期骨架 + run_state.json
python -B scripts/main.py record --kind protocol --file /app/work/protocol.json
python -B scripts/main.py record --kind screening --json '{"round":"title_abstract",...}'
python -B scripts/main.py slug "标题或短语"   # 确定性 id（用于 source_id/evidence_id）
python -B scripts/main.py finalize       # 重放工作文件 → result.json + review.md + 两个日志
python -B scripts/main.py selfcheck      # 校验 result.json（--strict 失败退出码 1）
# 通用参数（缺省指向运行时路径）：--input /input --output /app/output --work /app/work
```

在 Skill 根目录执行；脚本用 `os.path.dirname(__file__)` 定位 `core_impl.py`，不依赖当前工作目录。若环境无 `python3`，用 `python -B`。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] 问题拆解：`review_question` 非空，`subquestions` ≥1 且带依赖（实际效果：拆解/检索覆盖）。
- [ ] 检索式：每个 `query_blocks` 含 database+query+intent+时间/语言限制，不退化成一个关键词串（实际效果：检索可执行命中核心文献）。
- [ ] 纳排标准：条款化、可判定、含优先级，且在筛选决定**之前**落盘（实际效果：判定符合事先条款）。
- [ ] 筛选记录：两轮逐条 decision，每条 exclude 有 `reason+criterion_id`，计数可重算（实际效果：可核验；使用增益：纳排一致性）。
- [ ] 质量评价：逐维度 `basis_locator+quote` + 总体等级 + 对结论的影响，不只写“质量较高”（实际效果：正确性）。
- [ ] 证据回链：每条结论 `evidence_ids` 命中 `evidence_map`，每行有 `locator+quote` 且逐字取自材料（实际效果：可核验/证据回链）。
- [ ] 冲突与空白：`synthesis.conflicts` 逐条并列两个来源；`synthesis.gaps` 落位到具体缺口（实际效果：冲突证据/研究空白）。
- [ ] 综合结论：`synthesis.conclusions` 非空且与研究问题、证据范围一致，不越界（实际效果：综合结论）。
- [ ] 完整合法：`python -B scripts/main.py selfcheck` 通过；result.json 可被 `json.load` 解析（可靠体验：运行稳定）。
- [ ] 异常处理：空输入/坏 JSON/坏编码/检索为空/全文不可得/条款冲突/证据矛盾均记录且退出码 0（可靠体验：异常处理）。
- [ ] 可复用：纳排标准、检索式模板、质量量表、筛选表均可被他人复用（生态贡献：资源可复用性）。

## 7. 降级策略（诚实标注，禁止编造）

- 材料不足/空输入 → `status=insufficient_evidence`，`pending_human` 列出缺口，不生成示例填充。
- 仅有摘要/无全文 → 该篇标 `abstract_only`，结论降为 `suggestive`/`not_reported`，不补齐正文数据。
- 数据库不可用/超时 → 检索块标 `not_run`，记 `degradations`，不伪造 `hits`。
- PDF 无文本层/低质量图 → 标 `extraction_failed`，建议平台 pdf-text-tool/OCR 或人工，不硬读像素值。
- 缺软件/缺权限 → 记入 `limitations`/`errors`，降级为最小合法产物，进程不崩溃。
- 冲突无法裁定 → 并列记录 + 标 `undetermined`，不强行取一方。
- 缺字段/未知字段 → 保留键填 `null`/`[]`，记 `errors`/`degradations`；**绝不编造文献/DOI/数值、评测分数或运行结果**。
- 重复运行 → 固定证据 id、固定字段顺序、确定性重放，同一输入两次运行结果一致。

## 8. 边界

- 一个 Skill 只解决“系统性文献综述”这一件事；不代写不存在的实验/数据；不生成虚假参考文献。
- 不预置正式测试案例答案、不伪造 benchmark 分数；真实分数与运行痕迹由平台记录。
- 文献结论只做“材料内是否有支持”的判定，不越界判定其真伪；综述结论不越过证据范围扩展。
