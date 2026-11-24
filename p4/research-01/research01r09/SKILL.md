---
name: sci-deep-research-lite
description: 把多步科研问题做成可复核的深度研究任务：问题拆解→检索计划→多步取证→证据综合→有边界结论；每条结论回链材料中的逐字片段（locator+quote），冲突证据并列、缺口显式标注。适用于多源文献综述、系统筛选、趋势分析与选题评估等深度研究任务。
version: 1.0.0
---

# 深度研究 Agent（精简版）

## 1. 角色与何时使用

把一个多步科研问题落成"可被他人复跑"的研究任务：问题拆解 → 检索计划 → 工具取证 → 证据综合 → 有边界结论。核心纪律只有三条：

1. 每条结论必须回链到材料中的**逐字片段**（locator + quote）；
2. 冲突证据并列呈现并给出解决说明，不静默择一；
3. 材料没有的写 `pending_human` 或缺口，不编造数值、DOI、文献或运行结果。

**使用**：多源文献综述、系统筛选、趋势分析、选题评估。**不使用**：单篇逐句精读、文献元数据方法学审查、翻译润色、一次性脚本问答。

## 2. 输入契约

- 运行时原始输入在 `/input/case.json` 与 `/input/task.md`；材料与工件按题面提供，PDF 解析拿到真实 PDF（无预抽取答案层）。
- **先发现再假设**：不假设固定文件名/键名。先 `python3 -B scripts/main.py discover` 扫描 `/input`（含子目录）识别 case.json、task.md 与材料（.pdf/.md/.txt/.csv/.json/.jsonl/.xml/.html/.tex/.bib、图片、Office 等）。
- 文本按 `utf-8-sig` → `gb18030` → `latin-1` 降级读取；单文件 >2MB 截断并记入 `degradations`。
- 坏 JSON、坏编码、缺文件不崩溃：记入 `errors`/`degradations`，继续处理其余材料。
- 若 `case.json` 含 `output_contract`（或 `output_schema`/`result_schema`/`required_fields` 等价键），其全部字段必须**原样回填**到 `result.json`，缺值给 `null`/`[]`，不改名、不删未知键。

## 3. 输出契约

主产物必须写到 `/app/output/`，核心是 `/app/output/result.json`：

- `case_id`：取 case.json 的 `case_id/caseId/id/armiId`（缺则空串）。
- `status`：`ok | partial | insufficient_evidence | input_error`，诚实标注。
- `research_product`：`{type, question, summary, conclusions[]}`。
- `evidence_matrix[]`：每行必须带 `locator + quote`（逐字取自材料）。
- `uncertainty`：`{conflicts, biases, gaps, undetermined}`。
- 除题目 `output_contract` 全部字段外，必须再加 `sources`、`change_log`（以及 `limitations`/`errors`/`degradations`/`pending_human`/`runtime_seconds`）。

配套可复核产物：`report.md`（人类可读摘要）、`evidence_map.json`（逐条证据 + 拆解/检索记录）、`decomposition.md`、`search_plan.md`。中间证据写 `/app/work/evidence_cards.jsonl`（每行一个 JSON：id/subquestion/source/locator/quote/claim/judgment/reason/conclusion_ids）。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求，16384 tokens/响应）

**铁律：前 3 分钟必须先落盘一版合法结果骨架，之后只做增量改进。**

| 阶段 | 时间窗 | 请求预算 | 输入 → 输出 |
|---|---|---|---|
| P0 骨架 | 0–3 min | ~4 | 读 case.json/task.md + 扫描材料 → `skeleton` 落盘 result.json（status=partial）+ run_state.json |
| P1 拆解 | 3–12 min | ~8 | 读题面与材料 → 写 `/app/work/decomposition.md`（SQ1..SQn + 依赖边）与 `search_plan.md`（顺序/来源范围/停止条件） |
| P2 检索取证 | 12–35 min | ~32 | search/fetch/PDF 解析/表格抽取 → 逐条追加 `/app/work/evidence_cards.jsonl`（含 locator+quote） |
| P3 综合 | 35–50 min | ~10 | 对齐字段、处理冲突、写结论与缺口 → `merge --payload /app/work/payload.json` |
| P4 收尾 | 50–60 min | ~6 | `finalize` + `selfcheck` → 补缺口、修校验 |

预留 ~4 次请求作失败重试。中途异常一律保留已落盘的 partial 结果；**禁止**清空重建。

## 5. 脚本调用（相对路径，仅标准库）

```bash
python3 -B scripts/main.py discover
python3 -B scripts/main.py skeleton
python3 -B scripts/main.py merge --payload /app/work/payload.json
python3 -B scripts/main.py finalize
python3 -B scripts/main.py selfcheck
python3 -B scripts/selftest.py          # 本地离线自检
# 通用参数（缺省指向运行时路径）：--input /input --output /app/output --work /app/work
```

模型的实质内容写进 `/app/work/`（decomposition.md、search_plan.md、evidence_cards.jsonl、payload.json），由脚本确定性组装，避免手工改 JSON 出错。命令在 Skill 根目录（SKILL.md 所在目录）执行；脚本用 `os.path.dirname(__file__)` 定位自身，不依赖当前工作目录。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] 问题拆解：`decomposition.md` 含 ≥2 个可独立检索子问题并标注依赖。
- [ ] 检索计划：`search_plan.md` 写清顺序、来源范围、停止条件，无重复查同一批文献。
- [ ] 证据回链：每条结论 `evidence_ids` 命中 `evidence_matrix`，每行有 `locator + quote` 且逐字取自材料。
- [ ] 冲突证据：`uncertainty.conflicts` 逐条并列两个来源与解决说明，未静默择一。
- [ ] 综合结论：`research_product.conclusions` 非空且与研究问题、证据范围一致。
- [ ] 研究空白：`uncertainty.gaps`/`pending_human` 显式写出"缺什么"，不用套话。
- [ ] 完整合法：`python3 -B scripts/main.py selfcheck` 通过；`result.json` 可被 `json.load` 解析。
- [ ] 异常处理：空输入/坏 JSON/坏编码/PDF 解析失败均记入 `errors`/`degradations` 且退出码 0。

## 7. 降级策略

- 缺数据/材料不足 → `status=insufficient_evidence`，`pending_human` 列缺口，不编造。
- PDF 无文本层/低质量图 → `judgment=extraction_failed`，建议 OCR，不硬读像素值。
- 缺软件/缺权限 → 记入 `limitations`/`errors`，降级为最小合法产物，进程不崩溃。
- 冲突无法裁定 → 并列记录 + 标 `undetermined`，不强行取一方。
- 重复运行 → 固定证据 id、确定性布局，同一输入两次结果一致。

## 8. 边界

- 一个 Skill 只解决"深度研究"这一件事；不代写不存在的实验/数据；不生成虚假参考文献。
- 不预置正式测试用例答案、不伪造 benchmark 分数；真实分数与运行痕迹由平台记录。
- 文献结论只做"材料内是否有支持"的判定，不越界判定其真伪。

## 9. 最小调用示例（示意输出，非正式案例）

```bash
python3 -B scripts/main.py discover
python3 -B scripts/main.py skeleton
python3 -B scripts/main.py merge --payload /app/work/payload.json
python3 -B scripts/main.py finalize
python3 -B scripts/main.py selfcheck
```

示意 result.json（节选，仅说明接口，**非**任何正式案例答案）：

```json
{"case_id": "demo", "status": "partial",
 "research_product": {"type": "synthesis", "question": "…", "summary": "…",
   "conclusions": [{"id": "C1", "text": "…", "evidence_ids": ["E1"], "strength": "established"}]},
 "evidence_matrix": [{"id": "E1", "locator": "p3 §2.2", "quote": "逐字引用…",
   "judgment": "supports", "conclusion_ids": ["C1"]}],
 "uncertainty": {"conflicts": [], "gaps": [], "undetermined": []},
 "sources": [], "change_log": []}
```

