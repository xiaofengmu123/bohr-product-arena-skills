---
name: latex-writing-compile
description: 面向 LaTeX 学术写作与编译的可复核 Skill：读取科研材料与任务约束，生成或修改可编译 .tex 稿件，循环编译并定位、分类、修复错误，校验公式/表格/引用/模板必填项，建立事实-来源映射，产出可编辑产物与 /app/output/result.json。当任务要求以 LaTeX 完成写作、改写、模板适配、公式或引用修复、可重复构建时使用。
version: 2.0.0
---

# LaTeX 写作与编译 Skill

## 1. 角色与何时使用

把「科研材料 → 可编译 .tex 稿件 → 可复核结果」沉淀为可重复流程。适用：依据材料生成/改写论文章节、迁入指定模板、修复公式/表格/交叉引用/参考文献、定位编译错误并稳定构建。不适用：纯聊天问答、与 LaTeX 无关的写作任务。

核心原则：**先编译，再谈对错；事实有出处，缺口显式标注，绝不编造**。数字、单位、引用逐字复用材料；材料没有的数值补 `MATERIAL GAP` 或 `pending_human`。每个关键结论必须有 `locator` + 逐字 `quote`，冲突来源全部保留并标 `source_conflict`。

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
  "sources": [],
  "uncertainty": [],
  "pending_human": [],
  "change_log": []
}
```

- `case_id` 取自 case.json（键名可能是 `case_id` 或 `id`，缺失用 `unknown` 并标 `input_error`）。
- `output_contract` 的**全部键必须原样回填**：不改名、不删未知键；未完成值给 `null` 或 `[]`，不得省略。
- `status` 诚实：材料不足=`insufficient_evidence`；部分完成=`partial`；全部满足=`ok`；输入无法解析=`input_error`。
- `sources`：每条含 `locator`（如 `materials/paper.md#L12`）+ 逐字 `quote`；冲突来源都保留并标 `source_conflict`。
- `uncertainty`：缺口、冲突、待人工确认项；`pending_human`：需要人介入的决策清单；`change_log`：每步改动时间戳与说明。
- 可编辑主产物（`.tex`）及配套 `.bib`/`.cls` 放入 `/app/output/`；编译成功时随附 PDF 预览。中途异常不得留下不完整 result.json。

## 4. 执行流程（总预算：900 秒、64 次模型请求、单次 ≤16384 tokens）

**硬性要求：前 3 分钟内必须先落盘一版 result.json 骨架，之后只做增量改进。**

| 阶段 | 时间 | 请求预算 | 动作与输入/输出 |
|---|---|---|---|
| P0 引导 | 0–60s | ≤3 | 运行 `python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work`。输入 `/input`，输出 result.json 骨架 + `/app/work/inventory.json`。再读 case.json/task.md，确认任务类型。 |
| P1 初稿 | 60–180s | ≤15 | 提取材料事实池（数字/单位/引用/图），确定体裁与模板。生成可编辑 `main.tex` + `.bib`，回填 result.json 各 contract 字段为初稿值。 |
| P2 编译-修复 | 180–600s | ≤30 | `python3 scripts/latex_tools.py compile --main ... --outdir /app/output` 循环编译；对每条报错定位 文件:行号 与根因再修，不做盲改。`refcheck` 校验 label/ref/cite。 |
| P3 核验与溯源 | 600–870s | ≤10 | `main.py selfcheck`；逐条核对关键事实与来源（`evidence.py quote` 取逐字片段），填 `sources`/`uncertainty`/`pending_human`/`change_log`；可编译则附 PDF。 |
| P4 收尾 | 870–900s | ≤6 | `python3 scripts/main.py finalize ...` + `selfcheck`；保证 result.json 合法、contract 键齐全。 |

降级触发：若剩余请求 <10 或剩余时间 <120s，立即 `finalize` 并诚实标 `partial`/`insufficient_evidence`，列 `pending_human`。脚本调用一律用相对路径 `scripts/evidence.py`；若 cwd 不是 skill 根目录，回退到 `/skills/weekly-skill/scripts/evidence.py`。

## 5. 自检清单（逐条勾选，对应评分维度）

**实际效果（编译得通/改得对/交得可用/复现得了）**
- [ ] main.tex 在目标模板下 `halt-on-error` 至少一次编译通过（或记录首个未被修复的根因）。
- [ ] 每条报错都有 文件:行号 + 分类 + 根因说明，未停留在第一条错误信息。
- [ ] 公式符号/上下标/正斜体、表格数值与列宽、`\ref`/`\cite` 与原文意图一致。
- [ ] 模板必填项（作者/标题/摘要/关键词/引用样式/文件结构）逐项覆盖。
- [ ] 交付物是可编辑 `.tex`，非 PDF/截图片段；记录 TeX 发行版、宏包、编译命令与轮次。

**使用增益（质量/效率/增益可验证）**
- [ ] 修改集中在根因处，未做无依据的全局替换；改动有 diff/说明，可回滚。
- [ ] 编译-修复循环有次序：先分类、再定位、后修复、再编译验证，减少盲改次数。
- [ ] 每次编译产出 `compile_report.json` 供第三方复核；相同输入重复运行结果一致。

**创新增益（本版强化）**
- [ ] 报错按 `missing_package / undefined_control_sequence / math_error / undefined_reference / missing_file / environment_error / template_conflict` 七类归类，而非只报一行。
- [ ] 对报错级联做了根因判定（取最先未修复类别），并给出与分类对应的修复策略。
- [ ] 编译验证闭环：写稿 → 编译 → 解析 log → 修复 → 再编译，直至稳定或明确止损。

**可靠体验（易用/稳定/异常处理）**
- [ ] 空输入/坏 JSON/坏编码：退出码 0，写 `insufficient_evidence` 或 `input_error`，不崩溃。
- [ ] 缺宏包/缺字体/报错级联/模板类缺失：分类记录到 `errors`/`degradations`，继续处理其余材料。
- [ ] 脚本仅用标准库、相对路径调用、`--help` 可执行；result.json 始终为合法 JSON。

**生态贡献（文档/复用/推广）**
- [ ] SKILL.md 写清用途、适用条件、调用方式、输入输出、异常处理、时间与请求预算。
- [ ] `references/` 沉淀查错方法与自检清单；示例输出明确标注「示例」，不冒充正式结果。
- [ ] 交付前运行 `python3 scripts/selftest.py` 且退出码 0。

## 6. 降级策略

- 缺 LaTeX 引擎/宏包/字体：`latex_tools.py` 会记录 `engine_missing`，result.json 写 `degradations` 与 `limitations`，**不得声称编译成功**；仍交付可编辑 `.tex` 与诊断报告。
- 材料不足/冲突：写 `insufficient_evidence` 或 `source_conflict` + `pending_human`，绝不补造数值、DOI、作者或实验结果。
- 无法解析 case.json 的 output_contract：骨架仍含 case_id/status/sources/uncertainty/change_log，标记 `input_error` 并说明缺失字段。
- 写盘失败：降级到最小合法 result.json（元数据 + 缺口说明），不让进程崩溃。

## 7. 脚本接口（相对路径）

```bash
python3 scripts/main.py bootstrap   --input /input --output /app/output --work /app/work
python3 scripts/main.py discover    --input /input
python3 scripts/main.py finalize    --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck   --input /input --output /app/output --work /app/work
python3 scripts/latex_tools.py compile   --main MAIN.tex --outdir /app/output [--passes 3]
python3 scripts/latex_tools.py refcheck  --dir DIR [--bib BIB]
python3 scripts/latex_tools.py classify-log --log LOG [--main MAIN.tex]
python3 scripts/evidence.py index --root /input --out /app/work/source_index.json
python3 scripts/evidence.py quote --file PATH --start N --end M
python3 scripts/selftest.py
```

全部仅用 Python 标准库；`--help` 可查看参数。示例输出仅出现在 `references/`，不得冒充正式案例结果。

## 8. 最小调用示例（示意，非正式案例）

```bash
# 1. 引导：扫描 /input，落盘 result.json 骨架 + inventory.json
python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work
# 输出（示意）：{"case_id":"demo","status":"partial","contract_keys":["title","body"],...}

# 2. 证据索引与逐字引用（供 sources 回填）
python3 scripts/evidence.py index --root /input --out /app/work/source_index.json
python3 scripts/evidence.py quote --file /input/materials/paper.md --start 10 --end 14

# 3. 编译与引用校验
python3 scripts/latex_tools.py compile --main /app/output/main.tex --outdir /app/output --passes 3
python3 scripts/latex_tools.py refcheck --dir /app/output

# 4. 收尾与自检
python3 scripts/main.py finalize --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck --input /input --output /app/output --work /app/work
```

## 9. 边界

- 一个 Skill 只解决一类任务：本 Skill 聚焦「材料 → .tex 稿件 → 编译修复 → 可复核结果」，不做通用大而全的写作系统。
- 没有真实材料时输出「材料不足」而非示例填充；不虚构引用、DOI、数值、作者或实验结果。
- 交付给他人前，先在不同 Python 版本 / 目录下运行一次 `selftest.py`。
