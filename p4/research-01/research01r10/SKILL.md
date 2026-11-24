---
name: deep-research-ledger
description: 深度研究 Agent 方法论与“证据台账”运行骨架：把科研问题拆为可独立检索的子问题并按依赖排序，逐条以带 locator+逐字 quote 的证据卡收集材料，再确定性组装出可复核的综述/筛选/趋势/选题评估结论与边界。当需要多源文献综合、证据回链、冲突并列与不确定性说明的深度研究任务时使用。
version: 1.0.0
---

# 深度研究证据台账 Skill

## 1. 角色与何时使用

把一个多步科研问题做成“可复核的研究任务”：**问题拆解 → 检索计划 → 工具取证 → 台账记录 → 确定性组装**。每条结论都必须回链到材料中的**逐字片段**（locator + quote）；冲突证据如实并列；材料没有的写 `pending_human` 或缺口；不编造数值、DOI、文献或运行结果。

**使用**：多源文献综述、系统筛选、趋势分析、选题评估等深度研究任务。
**不使用**：单篇逐句精读、文献元数据方法学审查、全文翻译/润色、一次性脚本问答（直接写脚本即可）。

## 2. 输入契约（先发现，再假设）

- 运行时原始输入在 `/input/case.json` 与 `/input/task.md`；材料与工件由平台按题面提供，PDF 解析拿到的是真实 PDF（无预抽取答案层）。
- 不假设固定文件名/键名：扫描 `/input`（含子目录）识别 `case.json`、`task.md` 与材料（`.pdf/.md/.txt/.csv/.json/.jsonl/.xml/.html/.tex/.bib`、图片、Office 等）。
- 先运行 `python3 -B scripts/main.py discover` 列出清单；文本按 `utf-8-sig → utf-8 → gb18030 → latin-1` 降级读取，单文件 >2MB 截断并记入 `degradations`。
- 坏 JSON / 坏编码 / 缺文件都不崩溃：记入 `errors`/`degradations`，继续处理其余材料。
- 若 `case.json` 含 `output_contract`（或 `output_schema`/`result_schema` 等价键），其**全部字段必须原样回填**到 `result.json`：缺值给 `null`/`[]`/`""`，不改名、不删未知键。

## 3. 输出契约（核心：`/app/output/result.json`）

```json
{
  "case_id": "…",
  "status": "ok | partial | insufficient_evidence | input_error",
  "research_product": {"type": "synthesis|screening|trend|proposal",
    "question": "研究问题", "summary": "综合结论",
    "conclusions": [{"id": "C1", "text": "结论句",
      "evidence_ids": ["E1"], "strength": "established|inferred|conjectural"}]},
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
- 除题目 `output_contract` 全部字段外，**必须**再加 `status`、`sources`、`uncertainty`、`change_log`。
- `evidence_matrix` 每行必须带 `locator + quote`，`quote` 逐字取自材料。
- 配套可复核产物：`report.md`（人类可读摘要）、`evidence_map.json`（逐条证据 + 结论→证据反向索引）、`decomposition.md`、`search_plan.md`。
- `status` 要诚实：材料不足就 `insufficient_evidence`，部分完成就 `partial`，不编造结论冒充 `ok`。

**关键机制（与“手改 JSON / payload 合并”方案不同）**：`result.json` 永远不手工编辑。模型只追加“证据卡”和写分立的 plan/product/conclusions/uncertainty 文件，由脚本做**三层校验后确定性组装**（见 §5）。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求，16384 tokens/响应）

**铁律：前 3 分钟必须先落盘一版合法 `result.json` 骨架，之后只做增量改进，禁止清空重建。**

| 阶段 | 时间窗 | 请求预算 | 输入 → 输出 |
|---|---|---|---|
| P0 骨架 | 0–3 min | ~4 | 读 `case.json/task.md` + 扫描材料 → `main.py skeleton` 落盘 result.json（status=partial）+ `run_state.json` |
| P1 拆解 | 3–12 min | ~8 | 读题面与材料 → 写 `/app/work/decomposition.md`（`## SQ1..SQn` + 依赖边）与 `search_plan.md`（顺序/来源范围/停止条件） |
| P2 取证 | 12–35 min | ~32 | 检索/抓取/PDF 解析/表格抽取 → 逐条 `main.py card` 追加证据卡（含 locator+quote），或写 `/app/work/evidence_cards.jsonl` 后 `card --file` 批量入库 |
| P3 综合 | 35–50 min | ~10 | 写 `/app/work/product.json`（type/question/summary）、`conclusions.jsonl`（结论）、`uncertainty.json`（冲突/偏倚/空白）→ `finalize` 组装 |
| P4 收尾 | 50–60 min | ~6 | `finalize` + `selfcheck` → 按报告补缺口、修引用错误 |

预留 ~4 次请求作失败重试缓冲。任何中途异常都保留已落盘的 partial 结果。

## 5. 脚本调用（相对路径，仅标准库；命令在 SKILL.md 所在目录执行）

```bash
python3 -B scripts/main.py discover                      # 输入清单（只读）
python3 -B scripts/main.py skeleton                      # 早期骨架 + run_state.json
python3 -B scripts/main.py card --card '{"id":"E1", ...}'   # 追加并校验单张证据卡
python3 -B scripts/main.py card --file /app/work/cards.jsonl # 批量追加（JSON 数组或 JSONL）
python3 -B scripts/main.py finalize                      # 三层校验后组装 + report.md + evidence_map.json
python3 -B scripts/main.py selfcheck                     # 六点覆盖自检
# 通用参数：--input /input --output /app/output --work /app/work（缺省即运行时路径）
```

**三层校验（validate-then-write）**：① 语法（字段、类型、受控词表 judgment/strength/type）；② 引用（`conclusion.evidence_ids` 必须命中证据卡，`card.conclusion_ids` 必须命中结论）；③ 契约（`output_contract` 全字段物化回填）。校验失败仍写出合法 partial 结果并把错误记入 `errors`，绝不崩溃或产出坏 JSON。

模型产出的实质内容写进 `/app/work/`（decomposition.md、search_plan.md、evidence_cards.jsonl、product.json、conclusions.jsonl、uncertainty.json），由脚本确定性组装，避免手改 JSON 出错。脚本用 `os.path.dirname(__file__)` 定位 `ledger.py`，不依赖当前工作目录。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] 问题拆解：`decomposition.md` 含 ≥2 个 `## SQ` 子问题并标注依赖（实际效果：拆解/检索覆盖）。
- [ ] 检索计划：`search_plan.md` 写清顺序、来源范围、停止条件，无重复查同一批文献（使用增益：效率）。
- [ ] 证据回链：每条结论 `evidence_ids` 命中 `evidence_matrix`，每行有 `locator + quote` 且逐字取自材料（实际效果：可核验/证据回链）。
- [ ] 冲突证据：`uncertainty.conflicts` 逐条并列两个来源与解决说明，未静默择一（实际效果：冲突证据）。
- [ ] 综合结论：`research_product.conclusions` 非空且与研究问题、证据范围一致（实际效果：综合结论）。
- [ ] 研究空白：`uncertainty.gaps`/`pending_human` 显式写出“缺什么”，不用套话（实际效果：研究空白）。
- [ ] 完整合法：`main.py selfcheck` 通过；`result.json` 可被 `json.load` 解析（可靠体验：运行稳定）。
- [ ] 异常处理：空输入/坏 JSON/坏编码/PDF 解析失败均记录到 `errors`/`degradations` 且退出码 0（可靠体验：异常处理）。

提交前运行 `python3 -B scripts/selftest.py`，应打印 `14/14 passed`。

## 7. 降级策略

- 缺数据/材料不足 → `status=insufficient_evidence`，`pending_human` 列出缺口，不编造。
- PDF 无文本层/低质量图 → `judgment=extraction_failed`，建议 OCR 或人工，不硬读像素值。
- 缺软件/缺权限 → 记录到 `limitations`/`errors`，降级为最小合法产物，进程不崩溃。
- 冲突无法裁定 → 并列记录 + 标 `undetermined`，不强行取一方。
- 重复运行 → 固定证据 id、确定性布局，同一输入两次运行结果一致（时间戳字段除外）。

## 8. 边界

- 一个 Skill 只解决“深度研究”这一件事；不代写不存在的实验/数据；不生成虚假参考文献。
- 不预置正式测试用例答案、不伪造 benchmark 分数；真实分数与运行痕迹由平台记录。
- 文献结论只做“材料内是否有支持”的判定，不越界判定其真伪。
