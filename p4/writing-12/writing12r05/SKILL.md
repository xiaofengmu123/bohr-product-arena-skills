---
name: latex-writing-contract
description: 面向 LaTeX 学术写作与编译的「契约先行」Skill：扫描 /input 发现 case.json/task.md/材料，立即落盘 output_contract 全字段的结果骨架，逐字段增量回填并自动记录 change_log，循环编译定位分类修复错误，建立事实-来源映射，产出可编辑 .tex 与 /app/output/result.json。当任务要求以 LaTeX 完成写作、改写、模板适配、公式/引用修复、可重复构建时使用。
version: 2.0.0
---

# LaTeX 写作与编译 Skill（契约先行版）

## 1. 角色与何时使用

把「科研材料 → 可编译 .tex 稿件 → 契约完整的结果」沉淀为可重复流程。适用：依据材料生成/改写论文章节、迁入指定模板、修复公式/表格/交叉引用/参考文献、定位编译错误并稳定构建。不适用：纯聊天问答、与 LaTeX 无关的写作任务。

核心原则：**先落盘契约骨架，再增量回填；事实有出处，缺口显式标注，绝不编造**。数字、单位、引用逐字复用材料；材料缺失的数值标 `MATERIAL GAP` 或写进 `pending_human`。

本版设计取舍（相比通用编译 Skill）：**契约完整性优先**。result.json 的每个 `output_contract` 字段逐一对齐、可机器核验，每次字段变更都留下 `change_log`，确保"交得可用、可核验、可复现"。

## 2. 输入契约

运行时输入固定为：
- `/input/case.json`：案例 id、任务说明、材料路径、`output_contract`（须原样回填的字段清单，键名可能是 `output_contract` 或 `outputContract`）与证据范围；
- `/input/task.md`：任务与格式约束；
- `/input/` 下任意材料（`materials/`、`*.md`、`*.tex`、`*.bib`、`*.pdf`、图片等）。**不要假设固定文件名或键名**，先扫描发现。

PDF 材料必须用平台 `pdf-text-tool`（pdftotext）解析实际文件，不得按文件名/领域标签补造内容。工作目录 `/app/work`（中间产物），最终产物写 `/app/output/`。

## 3. 输出契约（契约先行，逐字段对齐）

必须产出 `/app/output/result.json`，结构如下：

```json
{
  "case_id": "...",
  "<output_contract 的每个键>": "<实际值 | null | []>",
  "status": "ok | partial | insufficient_evidence | input_error",
  "artifacts": [{"path": "...", "kind": "tex|bib|cls|pdf|log", "editable": true}],
  "sources": [{"locator": "materials/paper.md#L12", "quote": "逐字片段"}],
  "uncertainty": [],
  "pending_human": [],
  "change_log": [{"time": "...", "step": "...", "note": "..."}],
  "errors": [],
  "degradations": [],
  "limitations": [],
  "contract_report": {"key": {"state": "filled|gap", "set_at": "...", "source": "..."}},
  "runtime": {"started_at": "...", "ended_at": "...", "producer": "main.py"}
}
```

- `case_id` 取自 case.json（键名可能是 `case_id`/`id`/`challenge_id`/`benchmarkId`/`armiId`，缺失用 `unknown` 并标 `input_error`）。
- `output_contract` 的**全部键必须原样回填**：不改名、不删未知键；未完成值给 `null` 或 `[]`，不得省略。契约键支持 dict（字段名→规格）、list[str]、list[dict]（`name`/`key`/`field`）三种形态。
- 每个契约字段的填入必须走 `scripts/main.py contract-set`，该命令自动更新 `contract_report` 并追加 `change_log`；禁止绕过脚本直接改 result.json 后不留记录。
- `status` 诚实：材料不足=`insufficient_evidence`；部分完成=`partial`；全部满足=`ok`；输入无法解析=`input_error`。
- `sources`：每条含 `locator`（`材料路径#L行号`）+ 逐字 `quote`；冲突来源都保留并标 `source_conflict`。
- `change_log`：每一步改动（bootstrap、字段回填、编译修复、finalize）都要有时间戳与说明。
- 可编辑主产物（`.tex`）及配套 `.bib`/`.cls` 放入 `/app/output/`；编译成功时随附 PDF 预览并写入 `artifacts`。中途异常不得留下不完整 result.json。

## 4. 执行流程（总预算：900 秒、64 次模型请求、单次 ≤16384 tokens）

**硬性要求：前 3 分钟内必须先落盘一版 result.json 骨架（含全部契约字段=null），之后只做增量改进。**

| 阶段 | 时间 | 请求预算 | 动作与输入/输出 |
|---|---|---|---|
| P0 引导 | 0–60s | ≤3 | `python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work`。输出 result.json 骨架 + `/app/work/inventory.json`。再读 case.json/task.md，确认任务类型与契约字段清单。 |
| P1 初稿 | 60–180s | ≤15 | 提取材料事实池（数字/单位/引用/图），确定体裁与模板。生成可编辑 `main.tex` + `.bib`，用 `contract-set` 逐字段回填初稿值。 |
| P2 编译-修复 | 180–600s | ≤30 | 运行编译（`pdflatex -interaction=nonstopmode -halt-on-error` 或平台 latex-compiler）循环编译；对每条报错定位 `文件:行号` 与根因再修，不做盲改。每条修复用 `contract-set` 记录进 change_log。 |
| P3 核验与溯源 | 600–870s | ≤10 | `main.py selfcheck`；逐条核对关键事实与来源（`evidence.py quote` 取逐字片段），填 `sources`/`uncertainty`/`pending_human`；可编译则附 PDF。 |
| P4 收尾 | 870–900s | ≤6 | `python3 scripts/main.py finalize ...` + `selfcheck`；保证 result.json 合法、契约键齐全、change_log 完整。 |

降级触发：若剩余请求 <10 或剩余时间 <120s，立即 `finalize` 并诚实标 `partial`/`insufficient_evidence`，列 `pending_human`。脚本调用一律用相对路径 `scripts/*.py`；若 cwd 不是 skill 根目录，回退到 `/skills/weekly-skill/scripts/*.py`。

## 5. 契约回填协议（本版核心）

1. bootstrap 阶段读取 `output_contract`，把**每个键以 null 写入 result.json**，并在 `contract_report` 登记 `state=gap`。
2. 每个契约字段只有在拿到真实值（来自材料或编译产物）后才 `contract-set`，`state` 转为 `filled` 并记来源 locator；确实无法完成的字段保留 `null`，`state` 记 `gap`，同时写入 `uncertainty` 或 `pending_human`。
3. `contract-set` 每次调用自动追加一条 `change_log`：`{time, step, key, value_summary, source}`，保证字段级可追溯。
4. `finalize` 复核：契约键是否齐全、`contract_report` 是否与 result.json 一致、`change_log` 是否覆盖所有字段变更。
5. `selfcheck` 对任何"契约键缺失 / 契约键有值但无 change_log / status 非法 / JSON 无法解析"返回非零退出码，供调用方决定是否继续。

## 6. 自检清单（逐条勾选，对应评分维度）

**实际效果（40 分）**
- [ ] main.tex 在目标模板下 `-halt-on-error` 至少一次编译通过（或记录首个未被修复的根因，含 文件:行号）。
- [ ] 每条报错都有 文件:行号 + 分类 + 根因说明，未停留在第一条错误信息。
- [ ] 公式符号/上下标/正斜体、表格数值与列宽、`\ref`/`\cite` 与原文意图一致。
- [ ] 模板必填项（作者/标题/摘要/关键词/引用样式/文件结构）逐项覆盖。
- [ ] 交付物是可编辑 `.tex`，非 PDF/截图；记录 TeX 发行版、宏包、编译命令与轮次。

**结果可核验性（契约完整性，本版重点）**
- [ ] result.json 含 case_id、status、artifacts、sources、uncertainty、pending_human、change_log。
- [ ] `output_contract` 全部键原样回填，`contract_report` 与 result.json 逐字段一致。
- [ ] 每个关键数值/引用有 locator + 逐字 quote；无材料外新事实。
- [ ] 每个契约字段的变更都有对应 change_log 记录，可回滚、可复核。

**使用/创新增益（25+15 分）**
- [ ] 报错分类为 missing_package/undefined_control_sequence/math_error/undefined_reference/…，非只报一行。
- [ ] 编译验证闭环：写稿 → 编译 → 解析 log → 修复 → 再编译，直至稳定或明确止损。
- [ ] 相同输入重复运行得到一致的产物与状态（确定性）。

**可靠体验 / 生态贡献（10+10 分）**
- [ ] 空输入/坏 JSON/坏编码：退出码 0，写 `insufficient_evidence` 或 `input_error`，不崩溃。
- [ ] 缺宏包/缺字体/报错级联/模板类缺失：分类记录到 `errors`/`degradations`，继续处理其余材料。
- [ ] `scripts/selftest.py` 可独立运行并通过；脚本仅用标准库，`--help` 可执行。

## 7. 降级策略

- 缺 LaTeX 引擎/宏包/字体：记录 `engine_missing`，result.json 写 `degradations` 与 `limitations`，**不得声称编译成功**；仍交付可编辑 `.tex` 与诊断报告。
- 材料不足/冲突：写 `insufficient_evidence` 或 `source_conflict` + `pending_human`，绝不补造数值、DOI、作者或实验结果。
- 无法解析 case.json 的 output_contract：骨架仍含 case_id/status/sources/uncertainty/pending_human/change_log，标记 `input_error` 并说明缺失字段。
- 写盘失败：降级到最小合法 result.json（元数据 + 缺口说明），不让进程崩溃。

## 8. 脚本接口（相对路径）

```bash
python3 scripts/main.py bootstrap     --input /input --output /app/output --work /app/work
python3 scripts/main.py discover      --input /input
python3 scripts/main.py contract-set  --key KEY --value '...' [--source locator] --output /app/output --work /app/work
python3 scripts/main.py status        --input /input --output /app/output --work /app/work
python3 scripts/main.py finalize      --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck     --input /input --output /app/output --work /app/work
python3 scripts/evidence.py index     --root /input --out /app/work/source_index.json
python3 scripts/evidence.py quote     --file PATH --start N --end M
python3 scripts/evidence.py match     --file PATH --pattern 'REGEX'
python3 scripts/selftest.py           # 空目录/坏 JSON/完整契约三场景自检
```

全部仅用 Python 标准库；`--help` 可查看参数。示例输出仅出现在 `references/`，不得冒充正式案例结果。
