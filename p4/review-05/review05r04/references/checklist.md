# 可执行检查清单与降级矩阵

本文件给审查 Agent 一份"照着做即可"的清单；每一条对应 SKILL.md 的自检项与评分维度。运行脚本（`scripts/main.py` / `scripts/validate.py`）负责可机械核对的项，Agent 负责判定与证据绑定。

## 1. 执行期检查清单（按顺序）

- [ ] **0. 确认契约**：读 `/input/case.json` 记下 `case_id`、`output_contract`、检查范围、材料清单。
- [ ] **1. 前 3 分钟落盘骨架**：`python3 -B scripts/main.py run --input /input --output /app/output --work /app/work`。此后任何中断都已有合法 `result.json`。
- [ ] **2. 读证据索引**：读 `/app/work/evidence.json` 的 `sources`（段落索引）与 `hits`（候选观察），先读原文再看命中。
- [ ] **3. 逐检查面审查**：按 `references/audit-rules.md` 的 10 个 facet 逐项核对；每个 facet 写 `ok|issues|pending|not_checked` 结论，不得只查一项。
- [ ] **4. 写 findings**：每条写全 `id/type/severity/category/facet/title/file/locator/quote/evidence/basis/basis_rule/revision`；quote 必须能逐字回查。
- [ ] **5. 增量落盘**：把 findings 写入 `/app/work/findings.json`，运行 `python3 -B scripts/main.py merge ...`；重复"写→merge"直到收敛。
- [ ] **6. 最后自检**：`python3 -B scripts/validate.py ...`；修掉所有 FAIL；再 `python3 -B scripts/main.py finalize ...` 定 status。
- [ ] **7. 诚实收尾**：材料缺失/解析失败/工具不可用写入 `errors`/`degradations`；无法确认写 `pending_human`；预算不足如实标 `partial`。

## 2. 一条 finding 的写法（示意，非正式案例结果）

```json
{
  "id": "F001",
  "type": "fact_error",
  "severity": "major",
  "category": "fact",
  "facet": "quality",
  "title": "摘要与正文样本量不一致",
  "file": "materials/paper.txt",
  "locator": "materials/paper.txt#p2",
  "quote": "A total of 120 patients were enrolled",
  "evidence": "摘要段自报入组 120 例，而方法段 p5 自报 118 例，二者同一指标互相矛盾。",
  "basis": "材料内部一致性要求：同一指标在不同章节应一致（本 Skill references/audit-rules.md §3.7）。",
  "basis_rule": "quality.internal-consistency",
  "revision": "将 p2 的 120 与 p5 的 118 核对原始数据后统一为真实入组数，并同步更新结果表。"
}
```

要点：`quote` 是原文逐字片段（仅折叠空白）；`locator` 指向具体段落；`basis` 写判断依据而非泛泛提醒；`revision` 能直接执行。

## 3. 降级矩阵

| 场景 | 处置 | 结果字段 |
|---|---|---|
| 缺 PDF 文本层 | 依次 pdftotext → 平台 pdf-text-tool → OCR；仍不可得 | `degradations` + `errors`；只覆盖已解析文本，status 至少 `partial` |
| 坏 JSON / 坏编码 | 记录后继续处理其余材料；result.json 仍写合法 JSON | `errors` + `degradations` |
| 缺 `output_contract` | 用标准 schema，标 `contract_missing=true` | `contract_missing` |
| 单文件 >2MB | 跳过并记录 | `degradations` |
| 缺软件/工具 | 如实标 limitations，不伪造运行结果/评测分数 | `degradations` |
| 空输入目录 | 退出码 0，`status=insufficient_evidence` | `errors` + `sources=[]` |
| 受限数据 | 只标位置与类型，不复述内容，拿不准标 `pending_human` | `pending_human` |
| 引用存在性无法联网核实 | 标"存在性未核实"，不得断言伪造 | `uncertainty` |

## 4. 预算使用（900 秒 / 64 次模型请求）

- 读材料与证据索引：约 8–15 次请求。
- 逐 facet 判定与写 finding：约 25–35 次请求。
- 高价值核实（承重引用、术语、条款）：约 5–10 次请求。
- 保留最后 5–8 次给 validate/finalize 与修复。
- 前 3 分钟必须完成"骨架落盘"；此后只做增量，任何时刻 `result.json` 都是合法 JSON。
