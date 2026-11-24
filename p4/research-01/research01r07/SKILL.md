---
name: deep-research-robust
description: 深度研究 Agent 的鲁棒方法论与运行骨架：把科研问题拆解为可独立检索、带依赖的子问题，制定检索计划，多步取回并解析材料，形成有逐字来源定位（locator + quote）、有边界说明的综述/筛选/趋势/选题评估结论。当需要完成多源文献综合、证据回链、冲突证据处理与不确定性说明的深度研究任务时使用；本变体以"空输入、坏 JSON、坏编码、缺参数、超大文件均优雅降级不崩溃"为第一设计目标。
version: 1.1.0
---

# 深度研究 Agent Skill（鲁棒性变体）

## 1. 角色与何时使用

把一个多步推进的科研问题做成"可复核的研究任务"：问题拆解 → 检索计划 → 工具调用 → 证据综合 → 有边界结论。每条结论必须回链到材料中的**逐字片段**（`locator + quote`）；冲突证据如实并列，不静默择一；材料没有的写 `pending_human` 或明确缺口，不编造数值、DOI、文献或运行结果。

**使用**：多源文献综述、系统筛选、趋势分析、选题评估等深度研究任务。
**不使用**：单篇逐句精读、文献元数据与事实的方法学审查、全文翻译/润色、一次性脚本问答（直接写脚本即可）。

本变体的设计取舍：**先保证"永不崩溃 + 永远有合法产物"**，再在此前提下追求完整性。所有读文件、解析 JSON、写盘操作都由 `scripts/resultlib.py` 兜底，脚本层不向 Agent 抛异常。

## 2. 输入契约

- 运行时原始输入在 `/input/case.json` 与 `/input/task.md`；材料与工件由平台按题面提供，PDF 解析拿到的是真实 PDF（没有预抽取答案层）。
- **先发现再假设**：不假设固定文件名/键名。扫描 `/input`（含子目录）识别 `case.json`、`task.md` 与材料（`.pdf/.md/.txt/.csv/.json/.jsonl/.xml/.html/.tex/.bib`、图片、Office 等）。
- 用 `python3 -B scripts/main.py discover` 列出输入清单；文本按 `utf-8` 优先、`gb18030`/`latin-1` 降级读取；单文件 >2MB 截断并记入 `degradations`（不整文件丢弃，保证至少拿到前 2MB 的证据）。
- 坏 JSON、坏编码、缺文件都不崩溃：记入 `errors`/`degradations`，继续处理其余材料。
- 若 `case.json` 含 `output_contract`（或 `output_schema`/`result_schema`/`contract` 等价键），其全部字段必须**原样回填**到 `result.json`：缺值给 `null`/`[]`，不改名、不删未知键。

## 3. 输出契约

主产物必须写到 `/app/output/`，核心是 `/app/output/result.json`：

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
  "sources": [], "change_log": [], "limitations": [], "errors": [],
  "degradations": [], "pending_human": [], "runtime_seconds": 0
}
```

- `case_id` 取 `case.json` 的 `case_id/caseId/id/armiId`（缺则空串）。
- 除题目 `output_contract` 全部字段外，**必须**再加 `status`、`sources`、`uncertainty`、`change_log`；`evidence_matrix` 每行必须带 `locator + quote`（`judgment=extraction_failed` 时可无 quote，但必须说明提取失败）。
- 配套可复核产物：`report.md`（人类可读摘要）、`evidence_map.json`（逐条证据）、`decomposition.md`、`search_plan.md`。
- `status` 要诚实：材料不足就 `insufficient_evidence`，部分完成就 `partial`，不编造结论冒充 `ok`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求，16384 tokens/响应）

**铁律：前 3 分钟必须先落盘一版合法结果骨架，之后只做增量改进。**

| 阶段 | 时间窗 | 请求预算 | 输入 → 输出 |
|---|---|---|---|
| P0 骨架 | 0–3 min | ~4 | 读 `case.json/task.md` + 扫描材料 → `main.py skeleton` 落盘 result.json（status=partial）+ run_state.json |
| P1 拆解 | 3–12 min | ~8 | 读材料与题面 → 写 `/app/work/decomposition.md`（子问题 SQ1..SQn + 依赖边）与 `search_plan.md`（顺序/来源范围/停止条件） |
| P2 检索取证 | 12–35 min | ~32 | 执行 search/fetch/PDF 解析/表格抽取 → 逐条追加 `/app/work/evidence_cards.jsonl`（含 locator+quote） |
| P3 综合 | 35–50 min | ~10 | 对齐字段、处理冲突、写结论与缺口 → `main.py merge` 更新 result.json |
| P4 收尾 | 50–60 min | ~6 | `main.py finalize` + `main.py selfcheck` → 补缺口、修 validation 错误 |

预留 ~4 次请求作失败重试缓冲。中途任何异常都保留已落盘的 partial 结果；**禁止**清空重建。每次写盘都是原子替换（临时文件 + `os.replace`），中断也不会留下坏 JSON。

## 5. 脚本调用（相对路径，仅标准库）

```bash
python3 -B scripts/main.py discover                       # 输入清单
python3 -B scripts/main.py skeleton                       # 早期骨架 + run_state
python3 -B scripts/main.py merge --payload /app/work/payload.json
python3 -B scripts/main.py finalize                       # 汇总 + report.md + evidence_map.json
python3 -B scripts/main.py selfcheck                      # 校验 + 六点覆盖
python3 -B scripts/selftest.py                            # 本地自检（10 个鲁棒性场景）
# 通用参数（缺省指向运行时路径）：--input /input --output /app/output --work /app/work
```

模型产出的实质内容写进 `/app/work/`（decomposition.md、search_plan.md、evidence_cards.jsonl、payload.json），由脚本确定性组装，避免手工改 JSON 出错。命令在 Skill 根目录（SKILL.md 所在目录）执行；脚本内部用 `os.path.dirname(__file__)` 定位自身并导入 `resultlib`，不依赖当前工作目录。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] 问题拆解：`decomposition.md` 含 ≥2 个可独立检索子问题并标注依赖（实际效果：拆解/检索覆盖）。
- [ ] 检索计划：`search_plan.md` 写清顺序、来源范围、停止条件，无重复查同一批文献（使用增益：效率）。
- [ ] 证据回链：每条结论 `evidence_ids` 命中 `evidence_matrix`，每行有 `locator + quote` 且逐字取自材料（实际效果：可核验/证据回链）。
- [ ] 冲突证据：`uncertainty.conflicts` 逐条并列两个来源与解决说明，未静默择一（实际效果：冲突证据）。
- [ ] 综合结论：`research_product.conclusions` 非空且与研究问题、证据范围一致（实际效果：综合结论）。
- [ ] 研究空白：`uncertainty.gaps`/`pending_human` 显式写出"缺什么"，不用套话（实际效果：研究空白）。
- [ ] 完整合法：`main.py selfcheck` 通过；`result.json` 可被 `json.load` 解析（可靠体验：运行稳定）。
- [ ] 异常处理：空输入/坏 JSON/坏编码/缺参数/超大文件/写盘失败均记录到 `errors`/`degradations` 且进程不崩（可靠体验：异常处理）。

## 7. 降级策略（本变体核心）

| 场景 | 处置 | 退出码 |
|---|---|---|
| 空输入目录 | `status=insufficient_evidence`，`pending_human` 列缺口，不编造 | 0 |
| 坏 JSON case.json | `status=input_error`，契约回退默认字段，继续处理其余材料 | 0 |
| 坏编码文本 | `utf-8 → gb18030 → latin-1` 降级解码，记入 `degradations` | 0 |
| 缺参数 | `--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work` | 0 |
| 单文件 >2MB | 截断保留前 2MB，记 `degradations.truncated`，不整文件丢弃 | 0 |
| PDF 无文本层/低质量图 | `judgment=extraction_failed`，建议 OCR 或人工，不硬读像素值 | 0 |
| 写盘失败 | 降级为最小合法产物，记录错误，进程不崩 | 0 |
| 冲突无法裁定 | 并列记录 + 标 `undetermined`，不强行取一方 | 0 |
| 缺软件/缺权限 | 记录到 `limitations`/`errors`，不伪造软件状态或数据 | 0 |
| 重复运行 | 固定证据 id、排序确定性、merge 幂等，同一输入两次运行结构一致 | 0 |

## 8. 边界

- 一个 Skill 只解决"深度研究"这一件事；不代写不存在的实验/数据；不生成虚假参考文献。
- 不预置正式测试用例答案、不伪造 benchmark 分数；真实分数与运行痕迹由平台记录。
- 文献结论只做"材料内是否有支持"的判定，不越界判定其真伪。
- 详细字段说明见 `references/contract.md`，异常场景矩阵见 `references/robustness-checklist.md`。
