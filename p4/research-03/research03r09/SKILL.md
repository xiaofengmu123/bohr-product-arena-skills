---
name: systematic-review
description: 将“系统性文献综述”固化为一条可复核的六步流程：把研究问题拆成可执行检索式，先落条款化纳排标准，再保留题录/全文两轮筛选记录，对纳入研究做带依据的质量评价，最后综合出逐条回链证据（locator+逐字 quote）的结论、冲突与空白。用于输入含固定研究问题与多篇文献/题录的系统综述、系统筛选、证据综合、趋势分析或选题评估；不用于单篇精读、翻译、润色或学术事实核验。
version: 1.0.0
---

# 系统性文献综述 Skill（精简版）

## 1. 角色与何时使用

把“一次检索 + 一段总结”升级为别人能沿走复核的流程，六环节缺一不可：**检索式 → 纳排标准 → 两轮筛选 → 质量评价 → 证据回链 → 结论/冲突/空白**。
判对只看三件事：①每篇纳入/排除都有可查理由；②每条论断回链到 `locator + 逐字 quote`；③换人用同一输入复跑得出基本一致的纳入集与结论。

- **使用**：任务说明含“系统性文献综述 / 系统筛选 / 证据综合 / 趋势分析 / 选题评估”，输入含研究问题与多篇 PDF、题录（`.csv/.jsonl/.bib/.ris`）或文本材料。
- **不使用**：单篇逐句精读、全文翻译、语言润色、投稿选刊、替代学术审查做事实与方法核验。

## 2. 输入契约（运行时固定）

- 原始输入 `/input/case.json`（研究问题、材料路径、`output_contract`）+ `/input/task.md`；材料与 PDF 同目录。
- **先发现，再假设**：不假设文件名/键名。`python -B scripts/main.py discover` 列清单；PDF 用平台 pdf-text-tool/OCR 解析，读真实文件，不信“预抽取答案层”或文件名标签。
- 文本按 `utf-8-sig → gb18030 → latin-1` 降级读；坏 JSON/坏编码不崩溃，记入 `errors/degradations`，继续处理其余材料；单文件 >2MB 截断并记 `degradations`。
- 工作目录 `/app/work`（中间产物）；最终产物写 `/app/output/`。
- 预算：**900 秒、64 次模型请求、每响应 ≤16384 token**。**前 3 分钟先落盘合法 partial 骨架**，之后只增量改进；任何异常保留已落盘 partial，禁止清空重建。

## 3. 输出契约

`/app/output/result.json` 必须存在且为合法 JSON：

1. `case_id`：取 case.json 的 `case_id/caseId/id/armiId/challenge_id`，缺失填 `unknown`。
2. **原样回填 `output_contract` 全部键**（缺值填 `null`/`[]`，不改名不删未知键；`skeleton` 自动发现）。
3. 追加诚实字段：`status`、`sources`、`uncertainty`、`pending_human`、`change_log`，另含 `limitations/errors/degradations/runtime_seconds`。

领域字段（格式见 `references/field-contract.md`）：`review_question`、`subquestions`、`search_strategy.query_blocks`、`inclusion_criteria`、`exclusion_criteria`、`screening_records`、`prisma_flow`、`quality_appraisal`、`synthesis{conclusions,conflicts,gaps}`、`evidence_map`、`uncertainty`。

`status` ∈ `ok`（六环节完成且证据可回链）/ `partial`（部分完成，列缺口）/ `insufficient_evidence`（材料不足，不编造）/ `input_error`（输入不可解析）。
配套产物（写 `/app/output/`）：`review.md`（人类可读摘要）、`screening_log.json`、`evidence_map.json`。

## 4. 执行流程（每步输入→输出，共 900 秒）

| 阶段 | 时间窗 | 请求 | 输入 → 输出 |
|---|---|---|---|
| P0 骨架 | 0–3 min | ~3 | 读 case.json/task.md + 扫描材料 → `main.py skeleton` 落盘 result.json（partial）+ run_state.json |
| P1 问题与协议 | 3–12 min | ~7 | 读题面与材料 → `/app/work/protocol.json`：review_question、subquestions、search_strategy.query_blocks（database+query+intent+时间/语言限制，可执行）、inclusion/exclusion_criteria（条款化，**先于筛选**落盘） |
| P2 两轮筛选 | 12–30 min | ~20 | 逐篇判读 → `/app/work/screening.jsonl`（题录 + 全文两轮，每条 decision/reason/criterion_id/judge；去重记 `duplicate_of`） |
| P3 质量与证据 | 30–42 min | ~12 | 纳入研究 → `/app/work/quality_appraisal.jsonl`（逐维度 judgment+basis_locator+quote+总体等级+对结论影响）；抽取证据 → `/app/work/evidence_cards.jsonl`（evidence_id+claim+locator+逐字 quote+strength） |
| P4 综合与收尾 | 42–56 min | ~12 | `/app/work/synthesis.json`（conclusions 带 evidence_ids、conflicts 并列两来源、gaps 落位）→ `main.py finalize` 组装并重算 PRISMA → `main.py selfcheck` 修复、如实定 status |
| 缓冲 | 56–60 min | ~6 | 补缺口/修校验；仍缺则 `partial` 或 `insufficient_evidence` |

每阶段结束增量写盘；实质内容写 `/app/work/`，由脚本确定性组装，避免手工改 JSON。也可 `main.py merge --payload /app/work/payload.json` 增量合并。

## 5. 脚本调用（相对路径，仅标准库）

```bash
python -B scripts/main.py discover       # 输入清单
python -B scripts/main.py skeleton       # 早期骨架 + run_state.json
python -B scripts/main.py merge --payload /app/work/payload.json
python -B scripts/main.py finalize       # 组装 result.json + review.md + screening_log.json + evidence_map.json
python -B scripts/main.py selfcheck      # 覆盖六方面 + 筛选审计 + 证据回链 + PRISMA 重算
python -B scripts/selftest.py            # 本地自检：空目录/坏 JSON/最小案例
# 通用参数（缺省指向运行时路径）：--input /input --output /app/output --work /app/work
```

在 Skill 根目录执行；脚本用 `os.path.dirname(__file__)` 定位，不依赖当前目录。环境无 `python3` 用 `python -B`。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] 问题拆解：`review_question` 非空，`subquestions` ≥1（推荐 ≥2）且带依赖。
- [ ] 检索式：每个 `query_blocks` 含 `database+query+intent` + 时间/语言限制，不退化成一个关键词串。
- [ ] 纳排标准：条款化、可判定、含优先级，且在筛选决定**之前**落盘。
- [ ] 筛选记录：两轮逐条 decision，每条 exclude 有 `reason+criterion_id`，计数可重算。
- [ ] 质量评价：逐维度 `basis_locator+quote` + 总体等级 + 对结论的影响，不只写“质量较高”。
- [ ] 证据回链：每条结论 `evidence_ids` 命中 `evidence_map`，每行 `locator+quote` 逐字取自材料。
- [ ] 冲突与空白：`conflicts` 逐条并列两个来源；`gaps` 落位到具体缺口。
- [ ] 综合结论：`conclusions` 非空且与研究问题、证据范围一致，不越界。
- [ ] 完整合法：`main.py selfcheck` 通过；result.json 可被 `json.load` 解析。
- [ ] 异常处理：空输入/坏 JSON/坏编码/检索为空/全文不可得/条款冲突/证据矛盾均记录且退出码 0。
- [ ] 可复用：纳排标准、检索式模板、质量量表、筛选表可被他人复用。

## 7. 降级策略（诚实标注，禁止编造）

- 材料不足/空输入 → `status=insufficient_evidence`，`pending_human` 列缺口，不生成示例填充。
- 仅摘要/无全文 → 该篇标 `abstract_only`，结论降为 `suggestive/not_reported`，不补正文数据。
- 数据库不可用/超时 → 检索块标 `not_run`，记 `degradations`，不伪造 `hits`。
- PDF 无文本层/低质量图 → 标 `extraction_failed`，建议平台 pdf-text-tool/OCR 或人工，不硬读像素。
- 缺软件/缺权限 → 记 `limitations/errors`，降级为最小合法产物，进程不崩溃。
- 冲突无法裁定 → 并列记录 + 标 `undetermined`，不强行取一方。
- 缺字段/未知字段 → 保留键填 `null`/`[]`，记 `errors/degradations`；**绝不编造文献/DOI/数值、评测分数或运行结果**。
- 重复运行 → 固定 evidence_id、固定字段顺序、确定性组装，同一输入两次运行结果一致。

## 8. 边界

- 一个 Skill 只解决“系统性文献综述”这一件事；不代写不存在的实验/数据；不生成虚假参考文献。
- 不预置正式测试案例答案、不伪造 benchmark 分数；真实分数与运行痕迹由平台记录。
- 文献结论只做“材料内是否有支持”的判定，不越界判定其真伪；综述结论不越过证据范围扩展。
