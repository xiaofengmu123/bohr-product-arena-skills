---
name: latex-writing-ir
description: 面向 LaTeX 学术写作与编译的可复核 Skill。读取科研材料与任务约束，先落盘结构化中间表示（plan.json / fact_pool.json / compile_report.json），再由中间表示确定性归约生成可编辑 .tex 稿件与 /app/output/result.json；循环编译、按 文件:行号+分类+根因 修复错误，并对公式/表格/引用/模板必填项逐项核验，事实-来源逐字可回溯。当任务要求以 LaTeX 完成写作、改写、模板适配、公式或引用修复、可重复构建时使用。
version: 1.0.0
---

# LaTeX 写作与编译 Skill（中间表示 + 可复核归约）

## 1. 角色与何时使用

把「科研材料 → 结构化中间表示 → 可编译 .tex 稿件 → 可复核 result.json」沉淀为可重复流程。适用：依据材料生成/改写论文章节、迁入指定模板、修复公式/表格/交叉引用/参考文献、定位编译错误并稳定构建。不适用：纯聊天问答、与 LaTeX 无关的写作任务。

核心原则：**先编译，再谈对错；事实有出处，缺口显式标注，绝不编造**。数字、单位、引用逐字复用材料；材料没有的数值标 `MATERIAL GAP` 或写入 `pending_human`。

**设计取舍（与常见做法不同）**：不直接由材料写 `.tex`，而是先落三个结构化中间表示（IR），再确定性归约出终产物。任何数字、改动、报错都能从 IR 反查来源与决策，第三方可脱离 Agent 重放核验。

## 2. 输入契约

运行时输入固定为：
- `/input/case.json`：案例 id、任务说明、材料路径、`output_contract`（须原样回填的字段清单）与证据范围；
- `/input/task.md`：任务与格式约束；
- `/input/` 下任意材料（`materials/`、`*.md`、`*.tex`、`*.bib`、`*.pdf`、图片等）。**不要假设固定文件名或键名**，先扫描发现。

PDF 材料必须用平台 `pdf-text-tool`（pdftotext）解析实际文件，不得按文件名/领域标签补造内容。工作目录 `/app/work`（中间产物），最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`：

```json
{
  "case_id": "...",
  "<output_contract 的每个键>": "<实际值>",
  "status": "ok | partial | insufficient_evidence | input_error",
  "artifacts": [],
  "sources": [],
  "uncertainty": [],
  "pending_human": [],
  "change_log": [],
  "errors": [],
  "degradations": [],
  "runtime": {}
}
```

- `case_id` 取自 case.json（键名可能是 `case_id`/`id`/`challenge_id`/`armiId`，缺失用 `unknown` 并标 `input_error`）。
- `output_contract` 的**全部键原样回填**：不改名、不删未知键；未完成值给 `null` 或 `[]`，不得省略。
- `status` 诚实：材料不足=`insufficient_evidence`；部分完成=`partial`；全部满足=`ok`；输入无法解析=`input_error`。
- `sources`：每条含 `locator`（如 `materials/paper.md#L12`）+ 逐字 `quote`；冲突来源都保留并标 `source_conflict`。
- `uncertainty`/`pending_human`：缺口、冲突、待人工确认项；`change_log`：每步改动时间戳与说明。
- `artifacts`：可编辑主产物（`.tex`）及配套 `.bib`/`.cls`/图片路径；编译成功时随附 PDF 预览。中途异常不得留下不完整 result.json（见 §8 写盘失败降级）。

## 4. 中间表示（本版核心）

三个 IR 始终落盘在 `/app/work/`，是 result.json 的**唯一事实来源**；result.json 由 `ir.py reduce` 从 IR 确定性归约，不手工另造字段。

| IR 文件 | 内容 | 对应评分 |
|---|---|---|
| `plan.json` | 体裁/模板/受众判定、任务分解、contract 键清单、步骤与时间预算、决策及理由 | 完整性/效率增益/创新证据 |
| `fact_pool.json` | 每条事实：claim + category + status + locator + 逐字 quote + used_in | 正确性/可核验性 |
| `compile_report.json` | 引擎版本、每轮编译 exit_code、错误分类与根因、refcheck 结果、修复记录 | 编译得通/复现得了 |

归约规则：`sources`←fact_pool 已确认来源；`uncertainty`←conflict/pending_human/not_reported；`errors`/`degradations`←compile_report 与引擎状态；`status`←三个 IR 与 contract 完成度共同判定。改一处 IR，重跑 `reduce` 即得一致的新 result.json。

## 5. 执行流程（总预算：900 秒、64 次模型请求、单次 ≤16384 tokens）

**硬性要求：前 3 分钟内必须先落盘一版 result.json 骨架，之后只做增量改进。**

| 阶段 | 时间 | 请求预算 | 动作与输入/输出 |
|---|---|---|---|
| P0 引导 | 0–60s | ≤3 | `python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work`。读 case.json/task.md，写 result.json 骨架 + `work/inventory.json`，再用 `ir.py plan-init` 建 plan 骨架。 |
| P1 提取与规划 | 60–180s | ≤15 | 读材料建事实池（`ir.py fact-add`，数字/单位/引用/公式逐条带 quote），确定体裁与模板，把任务分解写入 plan.json。 |
| P2 成稿与编译 | 180–600s | ≤30 | 依 plan 生成可编辑 `main.tex` + `.bib`，回填 contract 字段；`latex_tools.py compile` 循环编译，每条报错定位 `文件:行号` + 分类 + 根因再修，不做盲改；`refcheck` 校验 label/ref/cite。每轮写 compile_report.json。 |
| P3 核验与归约 | 600–870s | ≤10 | `main.py selfcheck`；逐条核对关键事实与来源，补齐 `pending_human`/`source_conflict`；可编译则附 PDF；`ir.py reduce` 生成终版 result.json。 |
| P4 收尾 | 870–900s | ≤6 | `main.py finalize` + `selfcheck`；保证 result.json 合法、contract 键齐全、IR 与结果一致。 |

降级触发：若剩余请求 <10 或剩余时间 <120s，立即 `finalize` 并诚实标 `partial`/`insufficient_evidence`，列 `pending_human`。脚本调用一律用相对路径 `scripts/ir.py`；若 cwd 不是 skill 根目录，回退到 `/skills/weekly-skill/scripts/ir.py`。

## 6. 自检清单（逐条勾选，对应评分维度）

**实际效果（编译得通/改得对/交得可用/复现得了）**
- [ ] main.tex 在目标模板下 `-halt-on-error` 至少一次编译通过（或记录首个未被修复的根因文件:行号）。
- [ ] 每条报错都有 文件:行号 + 分类 + 根因说明，未停留在第一条错误信息。
- [ ] 公式符号/上下标/正斜体、表格数值与列宽、`\ref`/`\cite` 与原文意图一致。
- [ ] 模板必填项（作者/标题/摘要/关键词/引用样式/文件结构）逐项覆盖。
- [ ] 交付物是可编辑 `.tex`，非 PDF/截图；记录 TeX 发行版、宏包、编译命令与轮次。

**结果可核验性 / 使用增益 / 创新证据**
- [ ] result.json 含 case_id、status、artifacts、sources、uncertainty、pending_human、change_log，且 contract 键齐全。
- [ ] 每个关键数值/引用在 fact_pool.json 有 locator + 逐字 quote；result.json 的 sources 可由 `reduce` 从 IR 复现。
- [ ] plan.json 记录每个决策的理由与备选，改动有 diff/说明可回滚；编译过程可被第三方复核。

**可靠体验 / 异常处理**
- [ ] 空输入/坏 JSON/坏编码：退出码 0，写 `insufficient_evidence` 或 `input_error`，不崩溃。
- [ ] 缺宏包/缺字体/报错级联/模板类缺失：分类记录到 `errors`/`degradations`，继续处理其余材料。
- [ ] 重复运行结果一致（固定编译命令与轮次、确定性输出）。

## 7. 降级策略

- 缺 LaTeX 引擎/宏包/字体：`latex_tools.py` 记录 `engine_missing`，result.json 写 `degradations` 与 `limitations`，**不得声称编译成功**；仍交付可编辑 `.tex` 与诊断报告。
- 材料不足/冲突：写 `insufficient_evidence` 或 `source_conflict` + `pending_human`，绝不补造数值、DOI、作者或实验结果。
- 无法解析 case.json 的 output_contract：骨架仍含 case_id/status/sources/uncertainty/change_log，标 `input_error` 并说明缺失字段。
- 写盘失败：降级到最小合法 result.json（元数据 + 缺口说明），不让进程崩溃。
- 中间表示写失败：result.json 的 `runtime.ir_status` 标注 `ir_degraded`，字段改为直接落盘并说明降级原因。

## 8. 脚本接口（相对路径，仅标准库）

```bash
python3 scripts/main.py bootstrap   --input /input --output /app/output --work /app/work
python3 scripts/main.py discover    --input /input
python3 scripts/main.py finalize    --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck   --input /input --output /app/output --work /app/work
python3 scripts/ir.py plan-init     --work /app/work [--case-id ID] [--contract-keys K1,K2]
python3 scripts/ir.py plan-step     --work /app/work --id P2 --status done --note "..."
python3 scripts/ir.py fact-add      --work /app/work --id F001 --claim "..." --category number --locator "materials/x.md#L12" --quote "..."
python3 scripts/ir.py reduce        --input /input --output /app/output --work /app/work
python3 scripts/ir.py validate      --work /app/work
python3 scripts/latex_tools.py compile    --main MAIN.tex --outdir /app/output --work /app/work [--passes 3]
python3 scripts/latex_tools.py refcheck   --dir DIR [--bib BIB]
python3 scripts/latex_tools.py classify-log --log LOG [--main MAIN.tex]
```

全部仅用 Python 标准库；`--help` 可查看参数。示例输出仅出现在 `references/`，不得冒充正式案例结果。
