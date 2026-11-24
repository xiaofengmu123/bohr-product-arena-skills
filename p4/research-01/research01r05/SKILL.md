---
name: sci-deep-research
description: 深度研究 Agent 方法论与运行骨架：把科研问题拆成可独立检索的子问题、制定带依赖与停止条件的检索计划、多步取证，并做逐字证据回链的综述/筛选/趋势/选题评估。运行时以 /input/case.json 的 output_contract 为唯一契约，把所有契约字段原样回填进 result.json，同时附带 status/sources/uncertainty/change_log 与契约审计。当需要完成多源文献综合、证据回链、冲突处理与不确定性说明时使用。
version: 1.1.0
---

# 深度研究 Agent Skill（契约优先版）

## 1. 角色与何时使用

把多步推进的科研任务做成"可复核的研究过程"：问题拆解 → 检索计划 → 工具取证 → 证据综合 → 有边界结论。每条结论回到材料的**逐字片段**（locator + quote）；冲突证据并列呈现，不静默择一；材料没有的写 `pending_human`/缺口；不编造数值、DOI、文献或运行结果。

**使用**：多源文献综述、系统筛选、趋势分析、选题评估。
**不使用**：单篇逐句精读、文献元数据的方法学审查、全文翻译/润色、一次性脚本问答。

**与通用方法的差异**：本版以 `output_contract` 为唯一契约来源。所有写盘都经脚本完成，脚本保证契约字段全量回填、缺值给 `null`/`[]`、每次变更追加 `change_log`，并在 finalize 输出 `contract_audit` 证明字段对齐。模型只写实质内容，不手工改 JSON。

## 2. 输入契约

- 运行时原始输入在 `/input/case.json` 与 `/input/task.md`；材料（`.pdf/.md/.txt/.csv/.json/.jsonl/.xml/.html/.tex/.bib`、图片、Office 等）由平台按题面提供；PDF 解析拿到的是真实 PDF，无预抽取答案层。
- **先发现再假设**：不假设固定文件名/键名。运行 `python3 -B scripts/main.py discover` 扫描 `/input`（含子目录）识别 `case.json`、`task.md` 与材料。
- 文本按 `utf-8-sig` 优先、`gb18030`/`latin-1` 降级读取；单文件 >2MB 截断并记入 `degradations`。
- 坏 JSON、坏编码、缺文件不崩溃：记入 `errors`/`degradations`，继续处理其余材料。
- `case.json` 的 `output_contract`（或 `output_schema`/`result_schema`/`output_spec`/`result_contract` 等价键）为唯一契约；其全部字段必须**原样回填**到 `result.json`，缺值给 `null`/`[]`，不改名、不删未知键。契约也可能内嵌在子对象中，脚本会深度查找。

## 3. 输出契约

主产物写到 `/app/output/`，核心是 `/app/output/result.json`：

```json
{
  "case_id": "…",
  "status": "partial",
  "research_product": {"type": "synthesis|screening|trend|proposal",
    "question": "研究问题", "summary": "综合结论",
    "conclusions": [{"id": "C1", "text": "结论句",
      "evidence_ids": ["E1"], "strength": "established|inferred|conjectural"}]},
  "evidence_matrix": [{"id": "E1", "subquestion": "SQ1",
    "source": "materials/a.pdf", "locator": "p3 §2.2", "quote": "逐字引用",
    "claim": "支持的论断", "judgment": "supports|partial|unsupported|conflicts",
    "reason": "理由", "conclusion_ids": ["C1"]}],
  "uncertainty": {"conflicts": [], "biases": [], "gaps": [], "undetermined": []},
  "sources": [], "change_log": [], "contract_audit": {},
  "limitations": [], "errors": [], "degradations": [],
  "pending_human": [], "runtime_seconds": 0
}
```

- `case_id` 取 `case.json` 的 `case_id/caseId/id/armiId`（缺则空串）。
- **除题目 `output_contract` 全部字段外**，必须再加 `status`、`sources`、`uncertainty`、`change_log`；`evidence_matrix` 每行必须带 `locator + quote`。
- `change_log`：`skeleton`→`merge`→`finalize` 每步追加一条 `{ts, action, detail}`；`contract_audit` 记录契约字段命中/缺失/额外字段，供复核。
- 配套可复核产物：`report.md`（人类可读摘要）、`evidence_map.json`（逐条证据）、`decomposition.md`、`search_plan.md`、`synthesis.md`（若模型产出）。
- `status` 诚实：材料不足 → `insufficient_evidence`；部分完成 → `partial`；不编造结论冒充 `ok`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求，16384 tokens/响应）

**铁律：前 3 分钟先落盘一版合法 partial 结果骨架，之后只做增量改进，禁止清空重建。**

| 阶段 | 时间窗 | 请求预算 | 输入 → 输出 |
|---|---|---|---|
| P0 骨架 | 0–3 min | ~4 | 读 `case.json/task.md` + 扫描材料 → `scripts/main.py skeleton` 落盘 result.json（status=partial）+ run_state.json |
| P1 拆解 | 3–12 min | ~8 | 读材料与题面 → 写 `/app/work/decomposition.md`（子问题 SQ1..SQn + 依赖边）与 `search_plan.md`（顺序/来源范围/停止条件） |
| P2 检索取证 | 12–35 min | ~32 | 执行 search/fetch/PDF 解析/表格抽取 → 逐条追加 `/app/work/evidence_cards.jsonl`（含 locator+quote） |
| P3 综合 | 35–50 min | ~10 | 对齐字段、处理冲突、写结论与缺口 → 写 `/app/work/payload.json`，`scripts/main.py merge --payload` 更新 result.json |
| P4 收尾 | 50–60 min | ~6 | `scripts/main.py finalize` + `scripts/main.py selfcheck` → 补缺口、修 validation 错误 |

预留 ~4 次请求作失败重试缓冲。任何异常都保留已落盘的 partial 结果。

## 5. 脚本调用（相对路径，仅标准库）

```bash
python3 -B scripts/main.py discover       # 输入清单
python3 -B scripts/main.py contract       # 打印规范化 output_contract
python3 -B scripts/main.py skeleton       # 早期骨架 + run_state
python3 -B scripts/main.py merge --payload /app/work/payload.json
python3 -B scripts/main.py finalize       # 汇总 + report.md + evidence_map.json + contract_audit
python3 -B scripts/main.py selfcheck      # 契约校验 + 六点覆盖自检
python3 -B scripts/selftest.py            # 本地自检（demo fixture + 空输入 + 坏 JSON）
# 通用参数（缺省指向运行时路径）：--input /input --output /app/output --work /app/work
```

模型产出的实质内容写进 `/app/work/`（decomposition.md、search_plan.md、synthesis.md、evidence_cards.jsonl、payload.json），由脚本确定性组装。命令在 Skill 根目录执行；脚本用 `os.path.dirname(__file__)` 定位自身，不依赖当前工作目录。重复运行保持字段顺序、证据 id、结论排序确定性；时间戳与 `runtime_seconds` 属运行事实，允许随运行变化。

## 6. 自检清单（对应评分维度，逐条勾选）

- [ ] 问题拆解：`decomposition.md` 含 ≥2 个可独立检索子问题并标注依赖（实际效果：拆解/检索覆盖）。
- [ ] 检索计划：`search_plan.md` 写清顺序、来源范围、停止条件，不重复查同一批文献（使用增益：效率）。
- [ ] 证据回链：每条结论 `evidence_ids` 命中 `evidence_matrix`，每行有 `locator + quote` 且逐字取自材料（实际效果：可核验/证据回链）。
- [ ] 冲突证据：`uncertainty.conflicts` 逐条并列两个来源与解决说明，未静默择一（实际效果：冲突证据）。
- [ ] 综合结论：`research_product.conclusions` 非空且与研究问题、证据范围一致（实际效果：综合结论）。
- [ ] 研究空白：`uncertainty.gaps`/`pending_human` 显式写"缺什么"，不用套话（实际效果：研究空白）。
- [ ] 契约完整：`main.py selfcheck` 报 `validation_issues=[]` 且 `missing_contract_fields=[]`；`contract_audit` 记录字段对齐（本版侧重）。
- [ ] 完整合法：`result.json` 可被 `json.load` 解析，`report.md`/`evidence_map.json` 存在（可靠体验：运行稳定）。
- [ ] 异常处理：空输入/坏 JSON/坏编码/PDF 解析失败均记入 `errors`/`degradations` 且退出码 0（可靠体验：异常处理）。

## 7. 降级策略

- 缺数据/材料不足 → `status=insufficient_evidence`，`pending_human` 列缺口，不编造。
- PDF 无文本层/低质量图 → `judgment=extraction_failed`，建议 OCR 或人工，不硬读像素值。
- 缺软件/缺权限 → 记入 `limitations`/`errors`，降级为最小合法产物，进程不崩溃。
- 冲突无法裁定 → 并列记录 + 标 `undetermined`，不强行取一方。
- 契约缺失/未知键 → 缺值给 `null`/`[]`，未知键保留不删；`contract_audit` 记录。

## 8. 边界

- 一个 Skill 只解决"深度研究"这一件事；不代写不存在的实验/数据；不生成虚假参考文献。
- 不预置正式测试用例答案、不伪造 benchmark 分数；真实分数与运行痕迹由平台记录。
- 文献结论只做"材料内是否有支持"的判定，不越界判定其真伪。
- `references/` 下文档为规则与检查清单，`selftest.py` 的 demo fixture 仅为示意输入，不冒充正式案例结果。
