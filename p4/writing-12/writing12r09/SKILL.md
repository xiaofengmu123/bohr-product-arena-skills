---
name: latex-writing-compile
description: 精简、可复核的 LaTeX 学术写作与编译 Skill：扫描科研材料与任务约束，生成或修改可编译 .tex 稿件，循环编译并定位、分类、修复错误，校验公式、表格、交叉引用与模板必填项，为事实与数字建立来源映射，产出可编辑产物与 /app/output/result.json。当任务要求以 LaTeX 完成写作、改写、模板适配、公式或引用修复、可重复构建时使用。
version: 1.1.0
---

# LaTeX 写作与编译 Skill

## 1. 角色与何时使用

把「科研材料 → 可编译 .tex → 可复核结果」沉淀为可重复流程。适用：依据材料生成或改写论文章节、迁入指定模板、修复公式/表格/交叉引用/参考文献、定位编译错误并稳定构建。不适用：纯聊天问答、与 LaTeX 无关的写作。

核心原则：**先编译，再谈对错；事实有出处，缺口显式标注，绝不编造**。数字、单位、引用逐字复用材料；材料缺失补 `MATERIAL GAP` 并写 `pending_human`。

## 2. 输入契约

运行时输入固定为：
- `/input/case.json`：案例 id、任务说明、材料路径、`output_contract`（须原样回填的字段清单）与证据范围；
- `/input/task.md`：任务与格式约束；
- `/input/` 下任意材料（`materials/`、`*.md`、`*.tex`、`*.bib`、`*.pdf`、图片等）。

**不要假设固定文件名或键名**，先运行 `discover` 扫描。PDF 材料用平台 `pdf-text-tool` 解析实际文件，不得按文件名/领域标签补造内容。工作目录 `/app/work`，最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`：`case_id` 与 `output_contract` 的**全部键原样回填**，再加本 Skill 元数据字段：

```json
{
  "case_id": "...",
  "<output_contract 的每个键>": "<实际值>",
  "status": "ok | partial | insufficient_evidence | input_error",
  "artifacts": ["main.tex", "refs.bib"],
  "main_artifact": "main.tex",
  "sources": [{"locator": "materials/paper.md#L12", "quote": "逐字片段", "role": "数值|引用|论断"}],
  "uncertainty": ["缺口/冲突/待确认说明"],
  "pending_human": ["需人工确认项"],
  "change_log": [{"time": "…", "step": "…", "note": "…"}],
  "errors": [], "degradations": [], "limitations": [],
  "runtime": {"started_at": "…", "ended_at": "…", "engine": "pdflatex", "passes": 3}
}
```

- `case_id` 取自 case.json（键名可为 `case_id`/`id`/`challenge_id`/`benchmarkId`/`armiId`；缺失用 `unknown` 并标 `input_error`）。
- `output_contract` 全部键**不改名、不删除**；未完成给 `null` 或 `[]`。
- `status` 诚实：全部满足=`ok`；部分=`partial`；材料不足=`insufficient_evidence`；输入无法解析=`input_error`。
- `sources` 每条 `locator` + 逐字 `quote`；冲突来源都保留并标 `source_conflict`。
- 可编辑主产物 `.tex` 及配套 `.bib`/`.cls`/图片放入 `/app/output/`；编译成功随附 PDF。中途异常不得留下不完整 result.json。

## 4. 执行流程（总预算：900 秒、64 次模型请求、单次 ≤16384 tokens）

**硬性要求：前 3 分钟内先落盘一版 result.json 骨架（status=partial），之后只做增量改进。**

| 阶段 | 时间 | 请求 | 动作与输入/输出 |
|---|---|---|---|
| P0 引导 | 0–60s | ≤3 | `python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work`，落盘 result.json 骨架 + `/app/work/inventory.json`，再读 case.json/task.md 确认任务类型。 |
| P1 初稿 | 60–180s | ≤15 | 提取事实池（数字/单位/引用/图表），确定体裁与模板；生成可编辑 `main.tex` + `.bib`，把 contract 字段回填为初稿值。 |
| P2 编译修复 | 180–600s | ≤30 | `python3 scripts/latex_tools.py compile --main … --outdir /app/output` 循环编译；每条报错定位 文件:行号 + 根因再修，不做盲改；`refcheck` 校验 ref/cite。 |
| P3 核验溯源 | 600–870s | ≤10 | `main.py selfcheck`；用 `evidence quote` 取逐字片段填 `sources`/`uncertainty`；可编译则附 PDF。 |
| P4 收尾 | 870–900s | ≤6 | `main.py finalize` + `selfcheck`；保证 result.json 合法、contract 键齐全。 |

降级触发：剩余请求 <10 或剩余时间 <120s 时，立即 `finalize` 并诚实标 `partial`/`insufficient_evidence`，列 `pending_human`。

## 5. 自检清单（P3/P4 逐条勾选，对应评分维度）

**实际效果（40 分：编译得通 / 改得对 / 交得可用 / 复现得了）**
- [ ] `main.tex` 在目标模板下至少一次编译通过（或记录首个未被修复的根因：文件:行号）。
- [ ] 每条报错有 文件:行号 + 分类 + 根因，未停留在第一条错误信息。
- [ ] 公式符号/上下标/正斜体、表格数值与列宽、`\ref`/`\cite` 与原文意图一致。
- [ ] 模板必填项（标题/作者/摘要/关键词/引用样式/文件结构）逐项覆盖。
- [ ] 交付物是可编辑 `.tex`（非 PDF/截图）；记录 TeX 发行版、宏包、编译命令与轮次。

**结果可核验 / 使用增益（25 分）**
- [ ] result.json 含 case_id、status、artifacts、main_artifact、sources、uncertainty、pending_human、change_log，contract 键齐全。
- [ ] 每个关键数值/引用有 locator + 逐字 quote；无材料外新事实。
- [ ] 改动有 diff/说明可回滚；编译过程与产物可被第三方复核。

**创新增益（15 分：错误分类 / 修复策略 / 验证闭环）**
- [ ] 报错被分类（missing_package / undefined_control_sequence / math_error / undefined_reference / …）。
- [ ] 报错级联做根因判定，给出与分类对应的修复策略。
- [ ] 写稿 → 编译 → 解析 log → 修复 → 再编译闭环，直至稳定或明确止损。

**可靠体验 / 生态（20 分）**
- [ ] 空输入/坏 JSON/坏编码：退出码 0，写 `insufficient_evidence`/`input_error`，不崩溃。
- [ ] 缺宏包/缺字体/模板类缺失：分类记录到 `errors`/`degradations`/`limitations`，继续处理其余材料。
- [ ] 重复运行结果一致（固定编译命令与轮次、确定性输出）。
- [ ] SKILL.md 写清用途/调用/输入输出/异常处理；脚本仅标准库，`--help` 可执行。

## 6. 降级策略

- 缺 LaTeX 引擎/宏包/字体：`latex_tools.py` 记录 `engine_missing` 或错误分类，result.json 写 `degradations`/`limitations`，**不得声称编译成功**；仍交付可编辑 `.tex` 与诊断报告。
- 材料不足/冲突：写 `insufficient_evidence` 或 `source_conflict` + `pending_human`，绝不补造数值、DOI、作者或实验结果。
- 无法解析 case.json 的 output_contract：骨架仍含 case_id/status/sources/uncertainty/change_log，标 `input_error` 并说明缺失字段。
- 写盘失败：降级到最小合法 result.json（元数据 + 缺口说明），不让进程崩溃。

## 7. 脚本接口（相对路径，仅标准库）

```bash
python3 scripts/main.py bootstrap  --input /input --output /app/output --work /app/work
python3 scripts/main.py discover   --input /input
python3 scripts/main.py finalize   --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck  --input /input --output /app/output --work /app/work
python3 scripts/main.py evidence index  --root /input --out /app/work/source_index.json
python3 scripts/main.py evidence quote  --file PATH --start N --end M
python3 scripts/latex_tools.py compile    --main MAIN.tex --outdir /app/output [--passes 3] [--halt]
python3 scripts/latex_tools.py refcheck   --dir DIR [--log LOG]
python3 scripts/latex_tools.py classify-log --log LOG
python3 scripts/selftest.py
```

`--help` 查看参数。示例输出仅出现在 `references/`，不得冒充正式案例结果。
