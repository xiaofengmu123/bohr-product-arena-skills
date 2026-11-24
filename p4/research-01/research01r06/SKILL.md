---
name: sci-deep-research
description: 深度研究 Agent 方法论与运行骨架：把科研问题拆解为带依赖关系、可独立检索的子问题，制定有停止条件的检索计划，编排检索/解析/抽取/计算等多步工具调用并留痕，将证据整理为带逐字定位（locator+quote）的证据矩阵，形成有边界与冲突说明的综述、筛选、趋势或选题评估结论。核心卖点是结构化中间产物（plan.json、figure_model.json、evidence_cards.jsonl、tool_calls.jsonl）由确定性脚本组装与自检，整条路径可复核可复跑。适用于多源文献综合、证据回链、冲突证据处理与不确定性说明的深度研究任务。
version: 1.0.0
---

# 深度研究 Agent Skill

## 1. 角色与何时使用

把一个多步推进的科研问题做成“可复核的研究任务”：问题拆解 → 检索计划 → 工具调用 → 证据综合 → 有边界结论。每条结论都要回到材料中的**逐字片段**（locator + quote）；冲突证据并列呈现，不静默择一；材料没有的写 `pending_human` 或缺口，不编造数值、DOI、文献或运行结果。

**使用**：多源文献综述、系统筛选、趋势分析、选题评估等深度研究任务。
**不使用**：单篇逐句精读、文献元数据与事实的方法学审查、全文翻译/润色、一次性脚本问答。

## 2. 输入契约

- 运行时原始输入在 `/input/case.json` 与 `/input/task.md`；材料与工件由平台按题面提供，PDF 解析拿到的是真实 PDF（没有预抽取答案层），必须读实际文件，不依据标签补造数据。
- **先发现再假设**：先扫描 `/input`（含子目录）识别 `case.json`、`task.md` 与材料（`.pdf/.md/.txt/.csv/.json/.jsonl/.xml/.html/.tex/.bib`、图片、Office 等），不假设固定文件名/键名。
- 用 `python3 -B scripts/main.py discover` 列清单；文本按 `utf-8-sig` 优先、`gb18030`/`latin-1` 降级读取，单文件 >2MB 截断并记入 `degradations`。
- 坏 JSON、坏编码、缺文件都不崩溃：记入 `errors`/`degradations`，继续处理其余材料。
- 若 `case.json` 含 `output_contract`（或 `output_schema`/`result_schema` 等价键），其全部字段必须**原样回填**到 `result.json`，缺值给 `null`/`[]`，不改名、不删未知键。

## 3. 输出契约

主产物写到 `/app/output/`，核心是 `/app/output/result.json`，必须同时满足：

1. 题目 `output_contract` 的全部字段原样回填（缺值 `null`/`[]`）；
2. `case_id`（取 `case.json` 的 `case_id/caseId/id/armiId`，缺则空串）；
3. 附加稳定信封：`status`、`sources`、`uncertainty`、`change_log`，以及 `limitations`、`errors`、`degradations`、`pending_human`、`runtime_seconds`。

```json
{
  "case_id": "…",
  "status": "ok | partial | insufficient_evidence | input_error",
  "research_product": {"type": "synthesis|screening|trend|proposal",
    "question": "研究问题", "summary": "综合结论",
    "conclusions": [{"id": "C1", "text": "结论句", "evidence_ids": ["E1"], "strength": "established|inferred|conjectural"}]},
  "evidence_matrix": [{"id": "E1", "subquestion": "SQ1", "source": "materials/a.pdf",
    "locator": "p3 §2.2", "quote": "逐字引用", "claim": "支持的论断",
    "judgment": "supports|partial|unsupported|conflicts|extraction_failed",
    "reason": "理由", "conclusion_ids": ["C1"], "figure_ids": []}],
  "uncertainty": {"conflicts": [], "biases": [], "gaps": [], "undetermined": []},
  "sources": [], "change_log": [], "limitations": [], "errors": [],
  "degradations": [], "pending_human": [], "runtime_seconds": 0
}
```

- `evidence_matrix` 每行必须带 `locator + quote`（逐字取自材料）；每条结论的 `evidence_ids` 必须命中矩阵。
- 配套可复核产物（由脚本确定性生成，不手改 JSON）：`report.md`（人类可读摘要）、`evidence_map.json`（证据全量映射）、以及从 `/app/work` 复制的审计痕迹 `plan.json`、`figure_model.json`、`decomposition.md`、`search_plan.md`、`evidence_cards.jsonl`、`tool_calls.jsonl`。
- `status` 要诚实：材料不足 `insufficient_evidence`，部分完成 `partial`，不编造结论冒充 `ok`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求，16384 tokens/响应）

**铁律：前 3 分钟先落盘一版合法 partial 结果骨架，之后只做增量改进；任何异常保留已落盘的 partial 产物，禁止清空重建。**

| 阶段 | 时间窗 | 请求预算 | 输入 → 输出 |
|---|---|---|---|
| P0 骨架 | 0–3 min | ~4 | 读 `case.json/task.md` + 扫描材料 → `main.py skeleton` 落盘 result.json（partial）+ run_state.json |
| P1 拆解 | 3–12 min | ~8 | 读题面与材料 → 写 `/app/work/plan.json`（子问题 SQ1..SQn + 依赖 + 检索式 + 停止条件），`main.py plan` 校验并派生 decomposition.md / search_plan.md |
| P2 检索取证 | 12–35 min | ~32 | 执行 search/fetch/PDF 解析/表格抽取，逐条调用 `main.py evidence --card` 追加 evidence_cards.jsonl（含 locator+quote）；图/表另记 figure_model.json；每步工具调用记 tool_calls.jsonl |
| P3 综合 | 35–50 min | ~10 | 对齐字段、并列冲突、写结论与缺口 → 写 `/app/work/payload.json`，`main.py merge` 增量更新 result.json |
| P4 收尾 | 50–60 min | ~6 | `main.py finalize` + `main.py selfcheck` → 按校验错误补缺口 |

预留 ~4 次请求作失败重试缓冲。中间产物是结构化 JSON（见下），由脚本校验组装，避免手工改 JSON 出错。

**结构化中间产物（本版侧重点：中间表示与可复核）**

- `plan.json`：拆解 + 检索计划的机器可读单一事实源；`decomposition.md`/`search_plan.md` 由它确定性派生，杜绝两处不一致。
- `figure_model.json`：材料中图/表/附录清单（id/source/locator/kind/caption/claims），供证据回链与人工复核。
- `evidence_cards.jsonl`：逐条证据卡（id/source/locator/quote/claim/judgment/reason/conclusion_ids/figure_ids），由 `evidence` 子命令校验必填字段后追加。
- `tool_calls.jsonl`：每步工具调用的输入参数、输出引用、成功/失败与重试记录，支撑“整条路径可复跑”。

## 5. 脚本调用（相对路径，仅标准库）

```bash
python3 -B scripts/main.py discover                       # 输入清单
python3 -B scripts/main.py skeleton                       # 早期骨架 + run_state
python3 -B scripts/main.py plan --payload /app/work/plan.json
python3 -B scripts/main.py figure --payload /app/work/figure_model.json
python3 -B scripts/main.py evidence --card /app/work/card_E1.json
python3 -B scripts/main.py merge --payload /app/work/payload.json
python3 -B scripts/main.py finalize                       # 汇总 + report.md + evidence_map.json + 复制痕迹
python3 -B scripts/main.py selfcheck                      # 合法性 + 六点覆盖
python3 -B scripts/selftest.py                            # 本地四场景自检（空输入/坏JSON/坏编码/正常）
# 通用参数（缺省指向运行时路径）：--input /input --output /app/output --work /app/work
```

模型实质内容写进 `/app/work/`（plan.json、figure_model.json、evidence_cards.jsonl、tool_calls.jsonl、payload.json），由脚本确定性组装。命令在 Skill 根目录执行；脚本用 `os.path.dirname(__file__)` 定位自身并导入 `resultlib`，不依赖当前工作目录。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] 问题拆解：`plan.json` 含 ≥2 个可独立检索子问题，各带 id/text/depends_on/search（覆盖“问题拆解”）。
- [ ] 检索规划：`search_order` 与 `stop_conditions` 非空，子问题检索式无重复查同一批文献（覆盖“检索规划/检索覆盖”）。
- [ ] 证据回链：每条结论 `evidence_ids` 命中 `evidence_matrix`，每行有 `locator + quote` 且逐字取自材料（覆盖“证据回链/结果可核验”）。
- [ ] 冲突证据：`uncertainty.conflicts` 逐条并列 ≥2 个来源与解决说明，未静默择一（覆盖“冲突证据”）。
- [ ] 综合结论：`research_product.conclusions` 非空、`summary` 非空，且与研究问题、证据范围一致（覆盖“综合结论/结果正确性”）。
- [ ] 研究空白：`uncertainty.gaps`/`pending_human` 显式写出“缺什么”，不用套话（覆盖“研究空白/结果完整性”）。
- [ ] 完整合法：`main.py selfcheck` 返回 `valid: true`；`result.json` 可被 `json.load` 解析且含全部 contract 字段。
- [ ] 异常处理：空输入/坏 JSON/坏编码/PDF 解析失败均记入 `errors`/`degradations` 且退出码 0（可靠体验：异常处理）。
- [ ] 可复跑：同一输入两次运行结果一致，证据 id 固定、布局确定（可靠体验：运行稳定性）。

## 7. 降级策略

- 缺数据/材料不足 → `status=insufficient_evidence`，`pending_human` 列出缺口，不编造。
- PDF 无文本层/低质量图 → `judgment=extraction_failed`，建议 OCR 或人工复核，不硬读像素值；记入 `figure_model.json`。
- 检索为空/工具报错/限流 → 记录到 `tool_calls.jsonl` 与 `errors`，重试一次后降级为最小合法产物，进程不崩溃。
- 缺软件/缺权限 → 记录到 `limitations`/`errors`，降级为最小合法产物，不伪造软件状态。
- 冲突无法裁定 → 并列记录 + 标 `undetermined`，不强行取一方。
- 任何中途异常 → 保留已落盘 partial 结果，退出码 0，不抛异常中断。

## 8. 边界

- 一个 Skill 只解决“深度研究”这一件事；不代写不存在的实验/数据；不生成虚假参考文献。
- 不预置正式测试用例答案、不伪造 benchmark 分数；真实分数与运行痕迹由平台记录。
- 文献结论只做“材料内是否有支持”的判定，不越界判定其真伪；示意输出须标明为示例。
