---
name: latex-writing-compile-verif
description: 面向 LaTeX 学术写作与编译的「降级与不确定性显式化」Skill：读取科研材料与任务约束，生成或修改可编译 .tex 稿件，循环编译并分类、定位、修复错误，校验公式/表格/引用/模板必填项，为每个关键事实建立 verified/inferred/unverified 三级证据与 locator+逐字 quote 映射；缺数据、缺软件时显式写 limitations 与 pending_human，绝不编造。适用：LaTeX 写作、改写、模板适配、公式/引用修复、可重复构建，或要求诚实标注材料不足与不确定性的场景。
version: 1.1.0
---

# LaTeX 写作与编译 Skill（降级与不确定性显式化）

## 1. 角色与何时使用

把「科研材料 → 可编译 .tex 稿件 → 可复核 result.json」沉淀为可重复流程。适用：依据材料生成/改写论文章节、迁入指定模板、修复公式/表格/交叉引用/参考文献、定位编译错误并稳定构建。不适用：纯聊天问答、与 LaTeX 无关的写作任务。

核心原则：**先编译再谈对错；事实有出处，缺口显式标注，绝不编造**。数字、单位、引用逐字复用材料；材料没有的数值一律不填，写 `MATERIAL GAP` 或 `pending_human`，绝不用推测值补位。

## 2. 输入契约

运行时输入固定为：
- `/input/case.json`：案例 id、任务说明、材料路径、`output_contract`（须原样回填的字段清单）与证据范围；
- `/input/task.md`：任务与格式约束；
- `/input/` 下任意材料（`materials/`、`*.md`、`*.tex`、`*.bib`、`*.pdf`、图片等）。**不要假设固定文件名或键名**，先扫描发现。

PDF 材料必须用平台 `pdf-text-tool` 解析实际文件，把抽取文本存为 UTF-8 `.txt/.md` 后再 `index`；不得按文件名或领域标签补造内容。工作目录 `/app/work`（中间产物），最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`（永远是合法 JSON）：

```json
{
  "case_id": "...",
  "<output_contract 的每个键>": "<实际值>",
  "status": "ok | partial | insufficient_evidence | input_error",
  "sources": [{"locator": "materials/x.md#L12", "quote": "逐字片段", "source_conflict": false}],
  "facts": [{"id": "f1", "statement": "...", "locator": "...", "quote": "...",
             "state": "verified | inferred | unverified", "note": ""}],
  "uncertainty": [],
  "pending_human": [{"fact_id": "f1", "severity": "blocker | minor", "question": "..."}],
  "limitations": [{"scope": "...", "impact": "...", "fallback": "..."}],
  "errors": [],
  "degradations": [],
  "change_log": [{"time": "...", "step": "...", "note": "..."}],
  "runtime": {"started_at": "...", "producer": "main.py"}
}
```

- `case_id` 取自 case.json（键名可能是 `case_id`/`id`/`challenge_id`/`benchmarkId`/`armiId`，缺失用 `unknown` 并标 `input_error`）。
- `output_contract` 的**全部键必须原样回填**：不改名、不删未知键；未完成给 `null` 或 `[]`。
- `status` 诚实：材料不足=`insufficient_evidence`；部分完成=`partial`；全部满足=`ok`；输入无法解析=`input_error`。
- **证据三级**：`verified`=有 locator+逐字 quote（quote 必须能在 source_index 中复现）；`inferred`=由材料推导，须在 `note` 说明推理依据；`unverified`=材料缺口，必须同时出现在 `pending_human`，禁止进入 .tex 正文。
- `sources`/`facts` 每条带 `locator`（`相对路径#L行号`）+ 逐字 `quote`；冲突来源都保留并标 `source_conflict`。
- `uncertainty`=缺口/冲突/待人工确认；`limitations`=本交付物的能力边界、缺失项影响与替代方案；`pending_human`=需人回答的具体问题。
- 可编辑主产物（`.tex`）及配套 `.bib`/`.cls` 放入 `/app/output/`；编译成功时随附 PDF 预览；中途异常不得留下不完整 result.json。

## 4. 执行流程（总预算：900 秒、64 次模型请求、单次 ≤16384 tokens）

**硬性要求：前 3 分钟内必须先落盘一版 result.json 骨架，之后只做增量改进。**

| 阶段 | 时间 | 请求预算 | 动作与输入/输出 |
|---|---|---|---|
| P0 引导 | 0–60s | ≤3 | `python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work`；读 case.json/task.md，确认任务类型与 output_contract。 |
| P1 初稿 | 60–180s | ≤15 | `main.py index` 建证据索引；`main.py quote` 逐字取证；确定体裁与模板，生成可编辑 `main.tex`+`.bib`；把每个关键事实注册进 `facts`（三级证据）；回填 contract 字段为初稿值。 |
| P2 编译-修复 | 180–600s | ≤30 | `python3 scripts/latex_tools.py compile --main ... --outdir /app/output`；`classify-log` 对每条报错定位 文件:行号+分类+根因再修，不做盲改；`refcheck` 校验 label/ref/cite。 |
| P3 核验与溯源 | 600–870s | ≤10 | `main.py selfcheck`；逐条核对关键事实（verified 的 quote 必须能在 source_index 中找到）；填 `sources`/`uncertainty`/`pending_human`/`limitations`/`change_log`；可编译则附 PDF。 |
| P4 收尾 | 870–900s | ≤6 | `main.py finalize` + `main.py selfcheck`；保证 result.json 合法、contract 键齐全、状态诚实。 |

降级触发：剩余请求 <10 或剩余时间 <120s，立即 `finalize` 并诚实标 `partial`/`insufficient_evidence`，列出 `pending_human`。脚本调用一律用相对路径 `scripts/main.py`；若 cwd 不是 skill 根目录，回退到 `/skills/weekly-skill/scripts/main.py`。

## 5. 自检清单（逐条勾选，对应评分维度）

**实际效果（编译得通/改得对/交得可用/复现得了）**
- [ ] main.tex 在目标模板下 `-halt-on-error` 至少一次编译通过，或记录首个未被修复的根因（文件:行号）。
- [ ] 每条报错都有 文件:行号 + 分类 + 根因说明，未停留在第一条错误信息。
- [ ] 公式符号/上下标/正斜体、表格数值与列宽、`\ref`/`\cite` 与原文意图一致。
- [ ] 模板必填项（作者/标题/摘要/关键词/引用样式/文件结构）逐项覆盖。
- [ ] 交付物是可编辑 `.tex`，非 PDF/截图；记录 TeX 发行版、宏包、编译命令与轮次。

**结果可核验性 / 使用增益**
- [ ] result.json 含 case_id、status、sources、facts、uncertainty、pending_human、limitations、change_log，且 contract 键齐全。
- [ ] 每个关键数值/引用为 verified（locator+逐字 quote）或 inferred（note 说明），无 unverified 事实混入正文。
- [ ] 改动有 diff/说明可回滚；编译过程与产物可被第三方复核。

**可靠体验 / 异常处理**
- [ ] 空输入/坏 JSON/坏编码：退出码 0，写 `insufficient_evidence` 或 `input_error`，不崩溃。
- [ ] 缺引擎/缺宏包/缺字体/报错级联/模板类缺失：分类记录到 `degradations`/`limitations`，继续处理其余材料，**不得声称编译成功**。
- [ ] 重复运行结果一致（固定编译命令与轮次、确定性输出）。

## 6. 降级策略（本版重点：降级与不确定性显式化）

- **缺数据**：数值/引用/作者/DOI/实验结果缺失时，正文不写推测值；在 .tex 中留 `\GAP{...}` 或 `% MATERIAL GAP: ...`，同时在 result.json 写 `unverified` fact + `pending_human`（附具体问题），并给 `limitations` 说明缺失如何影响结论。
- **材料冲突**：同一指标两个来源不一致时，两个都保留并标 `source_conflict`，正文注明「存在冲突，待人工裁决」，绝不单方面取舍。
- **缺 LaTeX 引擎/宏包/字体**：`latex_tools.py` 记录 `engine_missing`/`missing_package`/`font_error`，result.json 写 `degradations`+`limitations`，**不得声称编译成功**；仍交付可编辑 `.tex` 与诊断报告（引擎探测结果、缺哪些宏包、可替换方案）。
- **无法解析 output_contract**：骨架仍含 case_id/status/sources/uncertainty/change_log，标 `input_error` 并说明缺失字段。
- **写盘失败**：降级到最小合法 result.json（元数据+缺口说明），不让进程崩溃。

## 7. 脚本接口（相对路径）

```bash
python3 scripts/main.py bootstrap  --input /input --output /app/output --work /app/work
python3 scripts/main.py discover   --input /input
python3 scripts/main.py index      --root /input --out /app/work/source_index.json
python3 scripts/main.py quote      --file PATH --start N --end M
python3 scripts/main.py finalize   --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck  --input /input --output /app/output --work /app/work
python3 scripts/latex_tools.py compile      --main MAIN.tex --outdir /app/output [--engine auto] [--passes 3]
python3 scripts/latex_tools.py classify-log --log LOG [--main MAIN.tex]
python3 scripts/latex_tools.py refcheck     --dir DIR [--bib BIB]
python3 scripts/selftest.py
```

全部仅用 Python 标准库；`--help` 可查看参数。示例输出仅出现在 `references/`，不得冒充正式案例结果。
