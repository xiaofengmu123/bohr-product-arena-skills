---
name: deep-research-deterministic
description: 深度研究 Agent 方法学与确定性运行骨架：把科研问题拆成可独立检索的子问题，规划有依赖顺序的检索，多步调用检索/解析工具收集证据，形成带逐字定位(locator+quote)与边界说明的综合结论；脚本层保证同一输入与同一工作笔记两次组装产物字节一致，供多源综述、系统筛选、趋势分析与选题评估使用。
version: 1.1.0
---

# 深度研究 Agent Skill（确定性版）

## 1. 角色与何时使用

把一个多步推进的科研问题做成"可复核的研究任务"：问题拆解 → 检索计划 → 工具调用 → 证据综合 → 有边界结论。每条结论都要能回到材料中的**逐字片段**（`locator` + `quote`），冲突证据如实并列，材料没有的写 `pending_human` 或缺口，不编造数值、DOI、文献或运行结果。

**使用**：多源文献综述、系统筛选、趋势分析、选题评估等深度研究任务。
**不使用**：单篇逐句精读、文献元数据与事实的方法学审查、全文翻译/润色、一次性脚本问答。

## 2. 输入契约

- 运行时原始输入在 `/input/case.json` 与 `/input/task.md`；材料与工件由平台按题面提供，PDF 解析拿到的是真实 PDF（没有预抽取答案层）。
- **先发现再假设**：不假设固定文件名/键名。扫描 `/input`（含子目录）识别 `case.json`、`task.md` 与材料（`.pdf/.md/.txt/.csv/.json/.jsonl/.xml/.html/.tex/.bib`、图片、Office 等）。
- 用 `python3 -B scripts/main.py discover` 列出输入清单；文本按 `utf-8-sig` 优先、`gb18030`/`latin-1` 降级读取，单文件 >2MB 截断并记入 `degradations`。
- 坏 JSON、坏编码、缺文件都不崩溃：记入 `errors`/`degradations`，继续处理其余材料。
- 若 `case.json` 含 `output_contract`（或 `output_schema`/`result_schema` 等价键），其**全部字段必须原样回填**到 `result.json`：缺值给 `null`/`[]`，不改名、不删未知键。

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
    "judgment": "supports|partial|unsupported|conflicts",
    "reason": "理由", "conclusion_ids": ["C1"]}],
  "uncertainty": {"conflicts": [], "biases": [], "gaps": [], "undetermined": []},
  "sources": [], "change_log": [], "limitations": [], "errors": [],
  "degradations": [], "pending_human": []
}
```

- `case_id` 取 `case.json` 的 `case_id/caseId/id/armiId`（缺则空串）。
- 除题目 `output_contract` 全部字段外，**必须**再加 `status`、`sources`、`uncertainty`、`change_log`；`evidence_matrix` 每行必须带 `locator + quote`。
- 配套可复核产物：`report.md`（人类可读摘要）、`evidence_map.json`（逐条证据）。
- `status` 要诚实：材料不足就 `insufficient_evidence`，部分完成就 `partial`，不编造结论冒充 `ok`。
- `change_log` 用 `seq`（自增序号）而非墙钟时间，保证同输入两次运行字节一致；墙钟/主机/进程元数据由 `finalize` 写入 `/app/work/run_metadata.json`，不进 `result.json`。

## 4. 执行流程（总预算 900 秒 / 64 次模型请求，16384 tokens/响应）

**铁律：前 3 分钟必须先落盘一版合法结果骨架，之后只做增量改进。**

| 阶段 | 时间窗 | 请求预算 | 输入 → 输出 |
|---|---|---|---|
| P0 骨架 | 0–3 min | ~4 | 读 `case.json/task.md` + 扫描材料 → `main.py skeleton` 落盘 result.json（status=partial）+ run_state.json |
| P1 拆解 | 3–12 min | ~8 | 读材料与题面 → 写 `/app/work/decomposition.md`（子问题 SQ1..SQn + 依赖边）与 `search_plan.md`（顺序/来源范围/停止条件） |
| P2 检索取证 | 12–35 min | ~32 | 执行 search/fetch/PDF 解析/表格抽取 → 逐条追加 `/app/work/evidence_cards.jsonl`（含 locator+quote） |
| P3 综合 | 35–50 min | ~10 | 对齐字段、处理冲突、写结论与缺口 → `main.py merge` 更新 result.json |
| P4 收尾 | 50–60 min | ~6 | `main.py finalize` + `main.py selfcheck` → 补缺口、修 validation 错误 |

预留 ~4 次请求作失败重试缓冲。中途任何异常都保留已落盘的 partial 结果；**禁止**清空重建。

## 5. 脚本调用（相对路径，仅标准库）

```bash
python3 -B scripts/main.py discover                       # 输入清单
python3 -B scripts/main.py skeleton                       # 早期骨架 + run_state
python3 -B scripts/main.py merge --payload /app/work/payload.json
python3 -B scripts/main.py finalize                       # 汇总 + report.md + evidence_map.json
python3 -B scripts/main.py selfcheck                      # 六点覆盖自检
python3 -B scripts/main.py selftest                       # SKILL.md/确定性/降级自检
# 通用参数（缺省指向运行时路径）：--input /input --output /app/output --work /app/work
```

模型产出的实质内容写进 `/app/work/`（decomposition.md、search_plan.md、evidence_cards.jsonl、payload.json），由脚本确定性组装，避免手工改 JSON 出错。

命令在 Skill 根目录（SKILL.md 所在目录）执行；脚本内部用 `os.path.dirname(__file__)` 定位自身并导入 `resultlib`，不依赖当前工作目录。

## 6. 自检清单（提交/交付前逐条勾选，对应评分维度）

- [ ] 问题拆解：`decomposition.md` 含 ≥2 个可独立检索子问题并标注依赖（实际效果：拆解/检索覆盖）。
- [ ] 检索计划：`search_plan.md` 写清顺序、来源范围、停止条件，无重复查同一批文献（使用增益：效率）。
- [ ] 证据回链：每条结论 `evidence_ids` 命中 `evidence_matrix`，每行有 `locator + quote` 且逐字取自材料（实际效果：可核验/证据回链）。
- [ ] 冲突证据：`uncertainty.conflicts` 逐条并列两个来源与解决说明，未静默择一（实际效果：冲突证据）。
- [ ] 综合结论：`research_product.conclusions` 非空且与研究问题、证据范围一致（实际效果：综合结论）。
- [ ] 研究空白：`uncertainty.gaps`/`pending_human` 显式写出"缺什么"，不用套话（实际效果：研究空白）。
- [ ] 完整合法：`main.py selfcheck` 通过；`result.json` 可被 `json.load` 解析（可靠体验：运行稳定）。
- [ ] 异常处理：空输入/坏 JSON/坏编码/PDF 解析失败均记录到 `errors`/`degradations` 且退出码 0（可靠体验：异常处理）。
- [ ] 确定性：`main.py selftest` 通过，同一输入两次组装 `result.json` 字节一致（可靠体验：重复运行）。
- [ ] 契约回填：`output_contract` 全部字段原样出现，未知键不删不改名（实际效果：结果完整性）。

## 7. 降级策略

- 缺数据/材料不足 → `status=insufficient_evidence`，`pending_human` 列出缺口，不编造。
- PDF 无文本层/低质量图 → `judgment=extraction_failed`，建议 OCR 或人工，不硬读像素值。
- 缺软件/缺权限 → 记录到 `limitations`/`errors`，降级为最小合法产物，进程不崩溃。
- 冲突无法裁定 → 并列记录 + 标 `undetermined`，不强行取一方。
- 重复运行 → 固定证据 id、确定性布局，同一输入两次运行结果一致。

## 8. 确定性保证（本版侧重点）

- `result.json` 不含墙钟时间：`change_log` 用 `seq` 序号，运行时长/主机名写入 `/app/work/run_metadata.json`。
- JSON 统一 `sort_keys=True` 序列化；`finalize` 把 `evidence_matrix`、`sources`、`uncertainty.*` 排成规范顺序，证据 id 按 `source → locator → quote` 稳定重编号。
- 保证边界：**相同输入文件 + 相同 `/app/work/` 工作笔记**下，脚本组装产物字节一致；模型每次自行生成的内容差异不属于脚本层的确定性范围。
- `selftest.py` 内置"两次运行字节一致"校验与空输入/坏 JSON 降级校验，交付前必须通过。

## 9. 边界

- 一个 Skill 只解决"深度研究"这一件事；不代写不存在的实验/数据；不生成虚假参考文献。
- 不预置正式测试用例答案、不伪造 benchmark 分数；真实分数与运行痕迹由平台记录。
- 文献结论只做"材料内是否有支持"的判定，不越界判定其真伪。
