---
name: sci-deep-research
description: 深度研究 Agent 方法与运行骨架，用于多源文献综述、系统筛选、趋势分析与选题评估。在 900 秒 / 64 次模型请求预算内，把科研问题拆成带依赖的可检索子问题，制定检索计划并多步调用检索、解析、计算工具收集证据，产出有逐字来源定位（locator + quote）、冲突并列与边界说明的结论。铁律：前 3 分钟先落盘合法 partial 结果骨架，之后只做增量改进。适用于证据回链、冲突处理、研究空白识别与可复跑记录。
version: 1.1.0
---

# 深度研究 Agent Skill

## 1. 角色与何时使用

把一个多步推进的科研任务做成"可复核、可复跑、可恢复"的研究链路：问题拆解 → 检索计划 → 多步工具调用 → 证据综合 → 有边界结论。每条结论必须回到材料中的**逐字片段**（locator + quote）；冲突证据并列呈现并给出裁定或标 `undetermined`；材料缺失写 `pending_human` 或缺口，不编造数值、DOI、文献或运行结果。

**使用**：多源文献综述、系统筛选、趋势分析、选题评估等需要多步检索与证据综合的任务。
**不使用**：单篇逐句精读、文献元数据方法学审查、全文翻译/润色、一次性脚本问答（直接写脚本）。

## 2. 本版设计取向（时间预算与早落盘）

本包与"先研究、最后才写结果"的常见做法相反：**result.json 是研究过程的账本，不是终点**。三条核心取舍：

1. **预算可度量、可自检**：900 秒 / 64 次请求拆成带截止时刻的阶段预算；`checkpoint` 子命令记录每阶段实际耗时与剩余预算，超期标 `late`，让 Agent 主动降级而不是无限检索。
2. **前 3 分钟先落盘骨架**：P0 只做输入发现与写骨架，禁止任何检索；`checkpoint` 在离开 P0 时强制校验 `result.json` 已存在且合法，缺失即返回致命告警 `p0_ok=false`。
3. **只追加、不覆盖**：`merge` 对列表字段采用"追加 + 按 id 去重"，任何中途异常都保留已落盘的 partial 结果；同一输入重复运行证据 id 与布局一致。

## 3. 输入契约

- 运行时原始输入在 `/input/case.json` 与 `/input/task.md`；材料与工件由平台按题面提供；PDF 解析拿到真实 PDF，**没有预抽取答案层**。
- **先发现、再假设**：不假设固定文件名/键名。扫描 `/input`（含子目录）识别 `case.json`、`task.md` 与材料（`.pdf/.md/.txt/.csv/.json/.jsonl/.xml/.html/.tex/.bib`、图片、Office 等）。
- 文本按 `utf-8-sig` 优先、`gb18030`/`latin-1` 降级读取；单文件 >2MB 截断并记入 `degradations`。
- 坏 JSON、坏编码、缺文件都不崩溃：记入 `errors`/`degradations`，继续处理其余材料，进程退出码恒为 0。
- 若 `case.json` 含 `output_contract`（或 `output_schema`/`result_schema`/`contract` 等价键），其**全部字段必须原样回填**到 `result.json`（嵌套结构保留），缺值给 `null`/`[]`，不改名、不删未知键。

## 4. 输出契约

主产物写到 `/app/output/`，核心为 `/app/output/result.json`：

```json
{
  "case_id": "…",
  "status": "ok | partial | insufficient_evidence | input_error",
  "research_product": {
    "type": "synthesis|screening|trend|proposal",
    "question": "研究问题",
    "summary": "综合结论",
    "conclusions": [
      {"id": "C1", "text": "结论句", "evidence_ids": ["E1"],
       "strength": "established|inferred|conjectural"}
    ]
  },
  "evidence_matrix": [
    {"id": "E1", "subquestion": "SQ1", "source": "materials/a.pdf",
     "locator": "p3 §2.2", "quote": "逐字引用", "claim": "支持的论断",
     "judgment": "supports|partial|unsupported|conflicts|extraction_failed",
     "reason": "理由", "conclusion_ids": ["C1"],
     "trace": {"tool": "pdf_text", "params": {}, "outcome": "ok"}}
  ],
  "uncertainty": {"conflicts": [], "biases": [], "gaps": [], "undetermined": []},
  "sources": [],
  "change_log": [],
  "limitations": [],
  "errors": [],
  "degradations": [],
  "pending_human": [],
  "runtime_seconds": 0
}
```

- `case_id` 取 `case.json` 的 `case_id/caseId/id/armiId`（缺则空串）。
- **除题目 `output_contract` 全部字段外，必须再加** `status`、`sources`、`uncertainty`、`change_log`、`pending_human`；`evidence_matrix` 每行必须带 `locator + quote`。
- 配套可复核产物：`report.md`（人类可读摘要）、`evidence_map.json`（逐条证据）、`decomposition.md`、`search_plan.md`、`tool_ledger.jsonl`。
- `status` 诚实：材料不足写 `insufficient_evidence`，部分完成写 `partial`，不编造结论冒充 `ok`。

## 5. 执行流程（900 秒 / 64 次请求，16384 tokens/响应）

**铁律：前 3 分钟必须落盘一版合法结果骨架（`skeleton`），之后只做增量改进；任何异常都保留 partial 结果，禁止清空重建。**

| 阶段 | 时间窗 | 请求 | 输入 → 输出 | 停止条件 |
|---|---|---|---|---|
| P0 骨架 | 0–3 min | ≤4 | 读 `case.json/task.md` + 扫描材料 → `skeleton` 落盘 result.json（partial）+ run_state | 骨架落盘且 JSON 可解析 |
| P1 拆解 | 3–12 min | ≤8 | 读材料与题面 → 写 `decomposition.md`（SQ1..SQn + 依赖边）与 `search_plan.md`（顺序/来源范围/停止条件） | ≥2 个 SQ，依赖已标 |
| P2 检索取证 | 12–35 min | ≤32 | 执行 search/fetch/PDF 解析/表格抽取，逐条追加 `evidence_cards.jsonl`（含 locator+quote+trace） | 每条结论有证据或显式缺口 |
| P3 综合 | 35–50 min | ≤10 | 对齐字段、并列冲突、写结论与缺口 → `merge` 增量更新 result.json | conclusions 与 evidence 对齐 |
| P4 收尾 | 50–60 min | ≤6 | `finalize` + `selfcheck` → 补缺口、修校验错误 | selfcheck 通过或缺口显式 |
| 预留 | — | ~4 | 失败重试缓冲 | — |

- 每阶段结束调用一次 `checkpoint --phase P1..P4 --requests N` 记账；它返回剩余时间/请求并校验阶段顺序与 P0 骨架。
- P2 中**每次工具调用**用 `trace` 子命令或在证据卡 `trace` 字段记录输入参数与返回结果，供他人复跑。

## 6. 脚本调用（相对路径，仅标准库）

```bash
python3 -B scripts/main.py discover                 # 输入清单 + output_contract 字段
python3 -B scripts/main.py skeleton                 # 早期骨架 + run_state（含阶段截止时刻）
python3 -B scripts/main.py checkpoint --phase P1 --requests 12 [--note "…"]   # 阶段记账
python3 -B scripts/main.py trace --action search --params '{"query":"…"}' --outcome ok [--summary "…"]
python3 -B scripts/main.py merge --payload /app/work/payload.json   # 增量合并（列表只追加）
python3 -B scripts/main.py finalize                 # 汇总 + report.md + evidence_map.json + 拷贝工作笔记
python3 -B scripts/main.py selfcheck                # 六点覆盖自检，输出 JSON 报告
# 通用参数（缺省指向运行时路径）：--input /input --output /app/output --work /app/work
```

- 模型产出的实质内容写进 `/app/work/`（decomposition.md、search_plan.md、synthesis.md、evidence_cards.jsonl、payload.json），由脚本确定性组装，避免手工改 JSON 出错。
- 命令在 Skill 根目录（SKILL.md 所在目录）执行；脚本用 `os.path.dirname(__file__)` 定位自身并导入 `core_impl`，不依赖当前工作目录。
- 交付前在开发机跑 `python3 -B scripts/selftest.py`，用临时目录自建 demo 输入验证全链路与降级路径。

## 7. 自检清单（对应评分维度，逐条勾选）

- [ ] 问题拆解：`decomposition.md` 含 ≥2 个可独立检索子问题并标注依赖（实际效果：拆解/检索覆盖）。
- [ ] 检索计划：`search_plan.md` 写清顺序、来源范围、停止条件，无重复查同一批文献（使用增益：效率）。
- [ ] 证据回链：每条结论 `evidence_ids` 命中 `evidence_matrix`，每行有 `locator + quote` 且逐字取自材料（实际效果：可核验/证据回链）。
- [ ] 冲突证据：`uncertainty.conflicts` 逐条并列 ≥2 个来源与解决说明，未静默择一（实际效果：冲突证据）。
- [ ] 综合结论：`research_product.conclusions` 非空且与研究问题、证据范围一致（实际效果：综合结论）。
- [ ] 研究空白：`uncertainty.gaps`/`pending_human` 显式写出"缺什么"，不用套话（实际效果：研究空白）。
- [ ] 完整合法：`main.py selfcheck` 通过；`result.json` 可被 `json.load` 解析（可靠体验：运行稳定）。
- [ ] 异常处理：空输入/坏 JSON/坏编码/PDF 解析失败均记录到 `errors`/`degradations` 且退出码 0（可靠体验：异常处理）。
- [ ] 预算纪律：`checkpoint` 记录各阶段耗时与剩余请求，超期标 `late` 并降级，不无限检索。

## 8. 降级策略

- 缺数据/材料不足 → `status=insufficient_evidence`，`pending_human` 列出缺口，不编造。
- PDF 无文本层/低质量图 → `judgment=extraction_failed`，建议 OCR 或人工，不硬读像素值。
- 缺软件/缺权限 → 记录到 `limitations`/`errors`，降级为最小合法产物，进程不崩溃。
- 冲突无法裁定 → 并列记录 + 标 `undetermined`，不强行取一方。
- 超预算 → 停检索，把已有证据合成 partial 结论，缺口入 `pending_human`。
- 重复运行 → 固定证据 id、确定性布局，同一输入两次运行证据 id 与顺序一致。

## 9. 边界

- 一个 Skill 只解决"深度研究"这一件事；不代写不存在的实验/数据；不生成虚假参考文献。
- 不预置正式测试用例答案、不伪造 benchmark 分数；真实分数与运行痕迹由平台记录。
- 文献结论只做"材料内是否有支持"的判定，不越界判定其真伪；不把平台白板当参赛者答案。
- 详细规则与质量标准见 `references/checklists.md`；可复用模板与最小调用示例见 `references/templates.md`。
