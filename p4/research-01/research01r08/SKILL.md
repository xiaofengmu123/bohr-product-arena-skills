---
name: deep-research-rubric-aligned
description: 面向科研深度研究任务的运行骨架与评分维度对齐方法：把研究问题拆成带依赖的子问题、制定去重检索计划、多步调用检索/解析/计算工具并记录参数与返回、形成带 locator+逐字 quote 的证据矩阵与有边界结论，并把题面评分维度（40/25/15/10/10）逐条映射到流程步骤与自检项。适用于多源文献综述、系统筛选、趋势分析、选题评估等需要可复核证据回链的深度研究任务；不适用于单篇逐句精读、元数据方法学审查、全文翻译润色或一次性脚本问答。
version: 1.0.0
---

# 深度研究 Agent Skill（评分维度对齐版）

## 1. 角色与何时使用

把一段多步科研任务做成"可复核的研究流水线"：问题拆解 → 检索规划 → 多步工具调用 → 证据综合 → 有边界结论。每条结论必须回链到材料中的**逐字片段**（`locator` + `quote`），冲突证据并列呈现，材料没有的写 `pending_human` 或缺口，不编造数值、DOI、文献或运行结果。

**使用**：多源文献综述、系统筛选、趋势分析、选题评估等深度研究任务。
**不使用**：单篇逐句精读、文献元数据与事实的方法学审查、全文翻译/润色、一次性脚本问答。

## 2. 输入契约

- 运行时原始输入在 `/input/case.json` 与 `/input/task.md`；材料与工件由平台按题面提供，PDF 解析拿到的是真实 PDF（无预抽取答案层）。
- **先发现再假设**：不要假设固定文件名/键名。先扫描 `/input`（含子目录），识别 `case.json`、`task.md` 与材料（`.pdf/.md/.txt/.csv/.json/.jsonl/.xml/.html/.tex/.bib`、图片、Office 等）。
- 用 `python3 -B scripts/main.py discover` 列输入清单；文本按 `utf-8-sig` 优先、`gb18030`/`latin-1` 降级读取，单文件 >2MB 截断并记入 `degradations`。
- 坏 JSON、坏编码、缺文件均不崩溃：记入 `errors`/`degradations`，继续处理其余材料。
- 若 `case.json` 含 `output_contract`（或 `output_schema`/`result_schema` 等价键），其全部顶层字段必须**原样回填**到 `result.json`：缺值给 `null`/`[]`，不改名、不删未知键。

## 3. 输出契约

主产物写到 `/app/output/`，核心是 `/app/output/result.json`：

```json
{
  "case_id": "…",
  "status": "ok | partial | insufficient_evidence | input_error",
  "research_product": {"type": "synthesis|screening|trend|proposal",
    "question": "研究问题", "summary": "综合结论",
    "conclusions": [{"id": "C1", "text": "结论句", "evidence_ids": ["E1"],
      "strength": "established|inferred|conjectural"}]},
  "evidence_matrix": [{"id": "E1", "subquestion": "SQ1", "source": "materials/a.pdf",
    "locator": "p3 §2.2", "quote": "逐字引用", "claim": "支持的论断",
    "judgment": "supports|partial|unsupported|conflicts|extraction_failed",
    "reason": "理由", "conclusion_ids": ["C1"]}],
  "uncertainty": {"conflicts": [], "biases": [], "gaps": [], "undetermined": []},
  "sources": [], "change_log": [], "limitations": [],
  "errors": [], "degradations": [], "pending_human": [],
  "rubric_coverage": {}, "runtime_seconds": 0
}
```

- `case_id` 取 `case.json` 的 `case_id/caseId/id/armiId`（缺则空串）。
- 除题目 `output_contract` 全部字段外，**必须**再加 `status`、`sources`、`uncertainty`、`change_log`；`evidence_matrix` 每行必须带 `locator + quote`。
- 配套可复核产物：`report.md`（人类可读摘要）、`evidence_map.json`（结论↔证据映射）、`decomposition.md`、`search_plan.md`；中间记录落 `/app/work/`（`evidence_cards.jsonl`、`payload.json`、`trace.jsonl`）。
- `status` 诚实：材料不足 → `insufficient_evidence`，部分完成 → `partial`，不编造结论冒充 `ok`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求，16384 tokens/响应）

**铁律：前 3 分钟必须先落盘一版合法结果骨架，之后只做增量改进，禁止清空重建。**

| 阶段 | 时间窗 | 请求预算 | 输入 → 输出 | 对齐评分维度 |
|---|---|---|---|---|
| P0 骨架 | 0–3 min | ~4 | 读 `case.json/task.md` + 扫描材料 → `main.py skeleton` 落盘 result.json（status=partial）+ run_state.json | 可靠体验·稳定性 |
| P1 拆解 | 3–12 min | ~8 | 读材料与题面 → 写 `/app/work/decomposition.md`（SQ1..SQn + 依赖边）与 `search_plan.md`（顺序/来源范围/停止条件） | 实际效果·任务完成度、创新增益 |
| P2 检索取证 | 12–35 min | ~32 | 执行 search/fetch/PDF 解析/表格抽取，每步记参数+返回 → 追加 `/app/work/evidence_cards.jsonl`（含 locator+quote） | 实际效果·可核验、使用增益·效率 |
| P3 综合 | 35–50 min | ~10 | 对齐字段、处理冲突、写结论与缺口 → `main.py merge` 更新 result.json | 实际效果·正确性/完整性 |
| P4 收尾 | 50–60 min | ~6 | `main.py finalize` + `main.py selfcheck` → 补缺口、修 validation | 可靠体验·异常处理、生态贡献 |

预留 ~4 次请求作失败重试。中途任何异常都保留已落盘的 partial 结果；每步产出落到 `/app/work/` 供下一步确定性复用。

## 5. 脚本调用（相对路径，仅标准库）

```bash
python3 -B scripts/main.py discover            # 输入清单
python3 -B scripts/main.py skeleton            # 早期骨架 + run_state
python3 -B scripts/main.py merge --payload /app/work/payload.json
python3 -B scripts/main.py finalize            # 汇总 + report.md + evidence_map.json
python3 -B scripts/main.py selfcheck           # 六点覆盖 + 评分维度对齐自检
python3 -B scripts/selftest.py                 # 包结构/空输入/坏输入/全流程自检
# 通用参数（缺省指向运行时路径）：--input /input --output /app/output --work /app/work
```

命令在 Skill 根目录执行；脚本内部用 `os.path.dirname(__file__)` 定位，不依赖当前工作目录。`--input` 等参数既可写在子命令前、也可写在子命令后。模型产出的实质内容写进 `/app/work/`，由脚本确定性组装，避免手工改 JSON 出错。

## 6. 评分维度对齐（本版侧重点）

把题面五维评分逐条落到流程与自检项，使每一步都对应可勾选的评分点：

| 评分维度 | 分值 | 流程落点 | 自检项（见 §7） | 可核验产物 |
|---|---|---|---|---|
| 实际效果 | 40 | P1 拆解 + P2 取证 + P3 综合 | 六类要点全覆盖、证据回链率 100% | result.json + evidence_map.json |
| 使用增益 | 25 | P0 骨架 + P2 检索去重 | 骨架先落盘、检索式去重、trace.jsonl 留痕 | trace.jsonl + search_plan.md |
| 创新增益 | 15 | 证据卡片机 + 冲突并列 | conflicts 双来源并列、不静默择一 | uncertainty.conflicts |
| 可靠体验 | 10 | 确定性脚本 + 降级 | selfcheck 通过、异常退出码 0、重复运行一致 | scripts/main.py + selftest.py |
| 生态贡献 | 10 | 模板/文档沉淀 | selftest.py 可跑、模板可复用 | references/*.md |
| GitHub 加分 | +5 | 提交时 | 根目录 `GITHUB_URL.txt` | GITHUB_URL.txt |

自检输出含 `rubric_coverage`（`six_key_points` + `scoring_dimensions` + `metrics`），把六类要点与五维评分量化成可读的布尔/比率。

## 7. 自检清单（对应评分维度，逐条勾选）

- [ ] 问题拆解：`decomposition.md` 含 ≥2 个可独立检索子问题并标注依赖（实际效果·任务完成度）。
- [ ] 检索计划：`search_plan.md` 写清顺序、来源范围、停止条件，无重复查同一批文献（使用增益·效率）。
- [ ] 证据回链：每条结论 `evidence_ids` 命中 `evidence_matrix`，每行有 `locator + quote` 且逐字取自材料（实际效果·可核验）。
- [ ] 冲突证据：`uncertainty.conflicts` 逐条并列两个来源与解决说明，未静默择一（创新增益）。
- [ ] 综合结论：`research_product.conclusions` 非空且与研究问题、证据范围一致（实际效果·正确性）。
- [ ] 研究空白：`uncertainty.gaps`/`pending_human` 显式写出"缺什么"，不用套话（实际效果·完整性）。
- [ ] 完整合法：`main.py selfcheck` 通过；`result.json` 可被 `json.load` 解析（可靠体验·稳定性）。
- [ ] 异常处理：空输入/坏 JSON/坏编码/PDF 解析失败均记录到 `errors`/`degradations` 且退出码 0（可靠体验·异常处理）。
- [ ] 可复跑：`scripts/selftest.py` 通过；同一输入两次运行结果一致（生态贡献·可复用）。

## 8. 降级策略

- 缺数据/材料不足 → `status=insufficient_evidence`，`pending_human` 列出缺口，不编造。
- PDF 无文本层/低质量图 → `judgment=extraction_failed`，建议 OCR 或人工，不硬读像素值。
- 缺软件/缺权限 → 记录到 `limitations`/`errors`，降级为最小合法产物，进程不崩溃。
- 冲突无法裁定 → 并列记录 + 标 `undetermined`，不强行取一方。
- 检索为空/工具报错或限流 → 记入 `errors` 与 `trace.jsonl`，不把"检索为空"当成"证据不存在"。

## 9. 边界

- 一个 Skill 只解决"深度研究"这一件事；不代写不存在的实验/数据；不生成虚假参考文献。
- 不预置正式测试用例答案、不伪造 benchmark 分数；真实分数与运行痕迹由平台记录。
- 文献结论只做"材料内是否有支持"的判定，不越界判定其真伪；`selftest.py` 中的合成输入是**示意示例**，明确标注，非任何正式案例答案。
