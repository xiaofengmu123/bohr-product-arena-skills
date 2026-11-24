---
name: sci-sysreview-budgeted
description: 把“系统性文献综述”做成带时间预算护栏的可复核流程 Skill：先拆解研究问题、写带意图的可执行检索式，再定条款化纳排标准并保留题录与全文两轮筛选记录，对纳入研究做逐维度可解释质量评价，最后综合出带逐字证据回链的结论、冲突与空白；以 900 秒 / 64 次请求的分阶段预算驱动，前 3 分钟必先落盘 partial 骨架，之后只做增量改进。当任务要求围绕固定研究问题完成综述、系统筛选、趋势分析或选题评估且输入含多篇文献/题录时使用；不用于单篇精读、翻译、润色或学术事实核验。
version: 1.0.0
---

# 系统性文献综述 Skill（时间预算护栏版）

## 1. 角色与何时使用

把“一次检索 + 一段总结”升级为别人能沿着走一遍的流程。五环节缺一不可：**检索式 → 纳排标准 → 两轮筛选记录 → 质量评价 → 带证据回链的综述结论**。判断做对只看三件事：每篇纳入/排除都有可查理由；每条论断都能回链到材料中的逐字证据；换人复跑能得出一致的纳入集与结论。

- **使用**：任务说明含“系统性文献综述 / 系统筛选 / 证据综合 / 趋势分析 / 选题评估”，输入含多篇 PDF、题录（.csv/.jsonl/.bib/.ris/.enw）或记录文件。
- **不使用**：单篇逐句精读、全文翻译、语言润色、投稿选刊、替代学术审查做事实与方法核验。

## 2. 输入契约（运行时固定）

- 原始输入：`/input/case.json`（研究问题、材料路径、可能的 `output_contract`）+ `/input/task.md`。
- **先发现，再假设**：不假设文件名/键名。先跑 `python -B scripts/main.py discover` 列清单，再识别 `case.json`、`task.md` 与材料。
- **必须读真实文件**：PDF 用平台 pdf-text-tool/OCR 自行解析，不信任“预抽取答案层”或文件名标签；单文件 >2MB 截断并记入 `degradations`。
- 文本按 `utf-8-sig` → `gb18030` → `latin-1` 降级读取；坏 JSON/坏编码/缺文件不崩溃，记入 `errors`/`degradations`，继续处理其余材料。
- 工作目录 `/app/work`（中间产物）；最终产物与可编辑文件写 `/app/output/`。
- 预算：**900 秒、64 次模型请求、每响应 ≤16384 token（含推理）**。前 3 分钟必须已落盘一版合法 result.json。

## 3. 输出契约（必须满足）

`/app/output/result.json` 必须存在且为合法 JSON，包含：

1. `case_id`：取 `case.json` 的 `case_id/caseId/id/armiId/challenge_id`，缺失填 `unknown`。
2. **题目 `output_contract` 的全部字段**：原样回填键名与类型，缺值填 `null`/`[]`，不改名、不删未知键；脚本 `skeleton` 自动发现并回填。
3. 追加诚实字段：`status`、`sources`、`uncertainty`、`change_log`（另含 `limitations/errors/degradations/pending_human/runtime_seconds/artifacts`）。

领域字段：`review_question`、`subquestions`、`search_strategy{query_blocks}`、`inclusion_criteria`、`exclusion_criteria`、`screening_records`、`prisma_flow`、`quality_appraisal`、`synthesis{conclusions,conflicts,gaps}`、`evidence_map`（模式见 `references/protocol.md`）。

`status` 取值：`ok`（五环节完成且证据可回链）/ `partial`（部分完成，须列缺口）/ `insufficient_evidence`（材料不足，不编造）/ `input_error`（输入不可解析）。

配套可复核产物（写 `/app/output/`）：`review.md`、`screening_log.json`、`evidence_map.json`。

## 4. 时间预算与阶段调度（本版核心）

总预算拆成阶段（`scripts/core_impl.py` 的 `PHASES` 是唯一事实源）：

| 阶段 | 时间窗 | 请求 | 输入 → 输出 |
|---|---|---|---|
| P0 骨架 | 0–180s | 3 | 读输入+扫材料 → `skeleton` 落盘 result.json(partial) + run_state.json |
| P1 协议 | 180–720s | 7 | 读题面/材料 → `/app/work/protocol.json`（问题、检索式、纳排条款先于筛选） |
| P2 筛选 | 720–1800s | 20 | 逐篇判读 → 追加 `/app/work/screening.jsonl`（题录+全文两轮，decision/reason/criterion_id/judge） |
| P3 质量与证据 | 1800–2520s | 12 | 纳入研究 → `quality_appraisal.jsonl`；抽取 → `evidence_cards.jsonl`（locator+逐字 quote） |
| P4 综合与收尾 | 2520–3360s | 12 | 写 `synthesis.json` → `finalize` 组装+重算 PRISMA → `selfcheck` 修复 |
| P5 缓冲 | 3360–3540s | 6 | 补缺口/修校验；仍缺则如实 `partial` |

**护栏铁律**（必须遵守）：

1. 每个阶段结束立即 `commit` 落检查点；skeleton 之后**只增量、不清空重建**。
2. 每次实质模型请求前跑 `python -B scripts/main.py budget`；每次模型回复后跑 `python -B scripts/main.py budget tick` 记账。
3. `budget` 返回 `verdict`：`on_track` 继续；`behind` 本阶段最小收口后立即 `finalize`；`critical`（剩余 ≤120s 或请求 ≤4 次）停止新内容工作、立即 `finalize`；`stop`（≥840s 或请求耗尽）只写盘、不发新请求。
4. 840s 后不再发任何新模型请求，预留 60s 写盘；任何异常保留已落盘 partial。

## 5. 执行流程（每步输入→输出）

- **P0**：`discover` → `skeleton`。确认 result.json 已落盘且 `case_id`/status 正确。
- **P1**：读 case.json/task.md/材料标题摘要 → 写 `protocol.json`（`review_question`、`subquestions` 带依赖、`query_blocks` 含 database+query+intent+时间/语言限制、条款化纳排标准）→ `commit --payload protocol.json --phase P1`。
- **P2**：逐篇按 `screening.jsonl` 追加两轮记录；去重记 `duplicate_of`；每轮结束时 `commit --field screening_records --file screening.jsonl --phase P2`。
- **P3**：纳入研究逐维度质量评价（`dim+basis_locator+quote+rating+overall+impact`）；证据卡 `id+source+locator+quote`；分别 `commit --field`。
- **P4**：写 `synthesis.json`（conclusions 带 `evidence_ids`、conflicts 并列两个来源、gaps 落到具体缺口）→ `finalize` → `selfcheck` 修复问题，如实定 status。
- 若证据只能支撑 `suggestive`/`not_reported`，按此降级，不补齐正文数据。

## 6. 脚本调用（相对路径，仅标准库）

```bash
python -B scripts/main.py discover     # 输入清单
python -B scripts/main.py skeleton     # 早期骨架 + run_state.json
python -B scripts/main.py budget       # 预算护栏：阶段/剩余时间/剩余请求/verdict
python -B scripts/main.py budget tick  # 记账一次模型响应
python -B scripts/main.py commit --payload /app/work/protocol.json --phase P1
python -B scripts/main.py commit --field synthesis --file /app/work/synthesis.json
python -B scripts/main.py finalize     # 组装 result.json + review.md + screening_log.json + evidence_map.json
python -B scripts/main.py selfcheck    # 六点覆盖 + 筛选审计 + PRISMA 重算
python -B scripts/selftest.py          # 交付前自检（合成示例，非正式案例）
# 通用参数：--input /input --output /app/output --work /app/work
```

在 Skill 根目录执行；脚本用 `os.path.dirname(__file__)` 定位 `core_impl.py`，不依赖当前工作目录。无 `python3` 时用 `python -B`。

## 7. 自检清单（对应评分维度，逐条勾选）

- [ ] 问题拆解：`review_question` 非空，`subquestions` ≥1（推荐 ≥2）且带依赖。
- [ ] 检索式：每个 `query_blocks` 含 database+query+intent+时间/语言限制，不退化成一个关键词串。
- [ ] 纳排标准：条款化、可判定、含优先级，且在筛选决定**之前**落盘。
- [ ] 筛选记录：两轮逐条 decision，每条 exclude 有 `reason+criterion_id`，计数可重算。
- [ ] 质量评价：逐维度 `basis_locator+quote` + 总体等级 + 对结论的影响，不只写“质量较高”。
- [ ] 证据回链：每条结论 `evidence_ids` 命中 `evidence_map`，每行有 `locator+quote` 且逐字取自材料。
- [ ] 冲突与空白：`synthesis.conflicts` 逐条并列两个来源；`synthesis.gaps` 落到具体缺口。
- [ ] 综合结论：`synthesis.conclusions` 非空且与研究问题、证据范围一致，不越界。
- [ ] 完整合法：`selfcheck` 通过；result.json 可被 `json.load` 解析。
- [ ] 异常处理：空输入/坏 JSON/坏编码/检索为空/全文不可得/条款冲突/证据矛盾均记录且退出码 0。
- [ ] 可复用：检索式模板、纳排标准、筛选表、质量量表、证据表可被他人复用。

## 8. 降级策略（诚实标注，禁止编造）

- 材料不足/空输入 → `status=insufficient_evidence`，`pending_human` 列缺口，不生成示例填充。
- 仅有摘要/无全文 → 该篇标 `abstract_only`，结论降为 `suggestive`/`not_reported`。
- 数据库不可用/超时 → 检索块标 `not_run`，记 `degradations`，不伪造 `hits`。
- PDF 无文本层/低质量图 → 标 `extraction_failed`，建议平台 pdf-text-tool/OCR 或人工，不硬读像素。
- 缺软件/缺权限 → 记 `limitations`/`errors`，降级为最小合法产物，进程不崩溃。
- 冲突无法裁定 → 并列记录 + 标 `undetermined`，不强行取一方。
- 缺字段/未知字段 → 保留键填 `null`/`[]`，记 `errors`/`degradations`；**绝不编造文献/DOI/数值、评测分数或运行结果**。
- 重复运行 → 固定证据 id、固定字段顺序、确定性组装，同一输入两次运行结果一致。

## 9. 边界与最小调用示例

- 一个 Skill 只解决“系统性文献综述”这一件事；不代写不存在的实验/数据；不生成虚假参考文献。
- 不预置正式测试案例答案、不伪造 benchmark 分数；真实分数与运行痕迹由平台记录。
- 文献结论只做“材料内是否有支持”的判定，不越界判定其真伪；综述结论不越过证据范围扩展。

**最小调用示例（示意输出，非正式案例）**：

```bash
python -B scripts/main.py discover
python -B scripts/main.py skeleton
python -B scripts/main.py budget          # => {"verdict":"on_track","phase":"P0",...}
python -B scripts/main.py finalize        # 组装最小合法产物
python -B scripts/main.py selfcheck       # => {"ok":true|false,...}
```

运行 `scripts/selftest.py` 可验证上述契约与降级路径；其 fixture 均标注“合成示例”，不冒充正式案例结果。
