---
name: latex-fact-first-write
description: 事实优先的 LaTeX 学术写作与编译 Skill。先扫描科研材料，抽取带来源定位与逐字引用的结构化事实库（factbase），校验事实与契约字段，再合成 .tex 稿件，随后循环编译、分类并根因化修复错误、校验交叉引用与模板必填项，产出可编辑 .tex/.bib 及可复算的 build_manifest.json 和 /app/output/result.json。当任务要求以 LaTeX 生成、改写、迁移模板、修复公式/表格/引用或可重复构建时使用。
version: 1.0.0
---

# 事实优先的 LaTeX 写作与编译 Skill

## 1. 角色与何时使用

把「科研材料 → 事实库 → 可编译 .tex 稿件 → 可复核结果」沉淀为可重复流程。适用：依据材料生成/改写论文章节、迁入指定模板、修复公式/表格/交叉引用/参考文献、定位编译错误并稳定构建。不适用：纯聊天问答、与 LaTeX 无关的写作。

核心原则：**先抽取事实、校验事实，再写 .tex；先编译，再谈对错；事实有出处，缺口显式标注，绝不编造**。数字、单位、引用逐字复用材料；材料没有的数值标 `MATERIAL GAP`，留待 `pending_human`，绝不补造。

架构取舍（区别于「编译优先」做法）：本 Skill 采用**中间表示 + 校验顺序反转**。所有事实一次性抽成带类型的 factbase（事实库），写作只消费事实库；事实与契约字段的缺口在写稿前就暴露；`build_manifest.json` 记录编译引擎、宏包、字体、命令、轮次与每轮 log 指纹，保证可复算。

## 2. 输入契约

运行时输入固定为：
- `/input/case.json`：案例 id、任务说明、材料路径、`output_contract`（须原样回填的字段清单）与证据范围；键名可能是 `case_id` 或 `id`，缺失用 `unknown` 并标 `input_error`。
- `/input/task.md`：任务与格式约束。
- `/input/` 下任意材料（`materials/`、`*.md`、`*.tex`、`*.bib`、`*.pdf`、图片等）。**不要假设固定文件名或键名**，先扫描发现。

PDF 材料必须用平台 `pdf-text-tool`（pdftotext）解析实际文件，不得按文件名/领域标签补造内容。工作目录 `/app/work`（中间产物），最终产物写 `/app/output/`。

## 3. 输出契约

必须产出 `/app/output/result.json`：

```json
{
  "case_id": "...",
  "<output_contract 的每个键>": "<实际值>",
  "status": "ok | partial | insufficient_evidence | input_error",
  "sources": [{"locator": "materials/x.md#L12", "quote": "逐字片段"}],
  "uncertainty": [],
  "pending_human": [],
  "change_log": [],
  "errors": [],
  "degradations": [],
  "runtime": {}
}
```

- `case_id` 取自 case.json；`output_contract` 的**全部键必须原样回填**：不改名、不删未知键；未完成值给 `null` 或 `[]`，不得省略。
- `status` 诚实：材料不足=`insufficient_evidence`；部分完成=`partial`；全部满足=`ok`；输入无法解析=`input_error`。
- `sources`：每个关键数值/引用/图元给 `locator` + 逐字 `quote`；冲突来源都保留并标 `source_conflict`。
- `uncertainty`/`pending_human`：缺口、冲突、待人工确认项；`change_log`：每步改动时间戳与说明。
- 可编辑主产物（`.tex`）及配套 `.bib`/`.cls` 放入 `/app/output/`；编译成功时随附 PDF 预览与 `build_manifest.json`。中途异常不得留下不完整 result.json（脚本用原子替换写盘）。

## 4. 执行流程（总预算：900 秒、64 次模型请求、单次 ≤16384 tokens）

**硬性要求：前 3 分钟内必须先落盘一版 result.json 骨架，之后只做增量改进。**

| 阶段 | 时间 | 请求 | 动作与输入/输出 |
|---|---|---|---|
| P0 引导 | 0–60s | ≤3 | `python3 scripts/main.py bootstrap --input /input --output /app/output --work /app/work`。输出 result.json 骨架 + `/app/work/inventory.json`。读 case.json/task.md 确认任务类型。 |
| P1 事实抽取 | 60–180s | ≤12 | `python3 scripts/main.py extract ...` 构建 `/app/work/factbase.json` 与 `source_index.json`。**先校验**：逐条核对契约字段所需事实是否有 locator+quote，缺口写入 `uncertainty`/`pending_human`；冲突标 `source_conflict`。 |
| P2 初稿合成 | 180–300s | ≤15 | 依据事实库（不重新解析材料）确定体裁/模板，写可编辑 `main.tex` + `.bib`。用 `scripts/main.py patch --set 键=值 --note ...` 增量回填契约字段。 |
| P3 编译-修复 | 300–600s | ≤30 | `python3 scripts/latex_tools.py compile --main main.tex --outdir /app/output --passes 2` 循环编译；对每条报错定位 文件:行号 + 分类 + 根因再修，不做盲改。`refcheck` 校验 label/ref/cite。 |
| P4 核验与溯源 | 600–870s | ≤10 | `scripts/main.py selfcheck`；逐条核对关键事实与来源，填 `sources`/`uncertainty`/`change_log`；可编译则附 PDF。取逐字片段用 `scripts/main.py quote --file REL --start N --end M`。 |
| P5 收尾 | 870–900s | ≤6 | `python3 scripts/main.py finalize ...` + `selfcheck`；保证 result.json 合法、contract 键齐全、status 诚实。 |

降级触发：若剩余请求 <10 或剩余时间 <120s，立即 `finalize` 并诚实标 `partial`/`insufficient_evidence`，列 `pending_human`。脚本调用一律用相对路径 `scripts/main.py`；若 cwd 不是 skill 根目录，回退到 `/skills/weekly-skill/scripts/main.py`。

## 5. 自检清单（逐条勾选，对应评分维度；可用 `python3 scripts/selftest.py` 自动跑）

**实际效果（编译得通/改得对/交得可用/复现得了）**
- [ ] main.tex 在目标模板下 `-halt-on-error` 至少一次编译通过（或记录首个未被修复的根因 文件:行号）。
- [ ] 每条报错都有 文件:行号 + 分类 + 根因说明，且报错级联已做根因判定，未停留在第一条错误。
- [ ] 公式符号/上下标/正斜体、表格数值与列宽、`\ref`/`\cite` 与原文意图一致（逐字复用）。
- [ ] 模板必填项（作者/标题/摘要/关键词/引用样式/文件结构）逐项覆盖。
- [ ] 交付物是可编辑 `.tex`（非 PDF/截图）；`build_manifest.json` 记录引擎、宏包、字体、命令与轮次。

**结果可核验性 / 使用增益**
- [ ] result.json 含 case_id、status、sources、uncertainty、pending_human、change_log，且 contract 键齐全。
- [ ] 每个关键数值/引用有 locator + 逐字 quote；无材料外新事实；冲突标 `source_conflict`。
- [ ] 改动有 diff/说明可回滚；相同输入重复运行得到一致产物与状态（确定性）。

**可靠体验 / 异常处理**
- [ ] 空输入/坏 JSON/坏编码：退出码 0，写 `insufficient_evidence` 或 `input_error`，不崩溃。
- [ ] 缺引擎/宏包/字体/报错级联/模板类缺失：分类记录到 `errors`/`degradations`，继续处理其余材料。
- [ ] result.json 永远是合法 JSON（可被 `json.load` 解析）。

## 6. 降级策略

- 缺 LaTeX 引擎/宏包/字体：`latex_tools.py` 记录 `engine_missing`，result.json 写 `degradations` 与 `limitations`，**不得声称编译成功**；仍交付可编辑 `.tex` 与诊断报告。
- 材料不足/冲突：写 `insufficient_evidence` 或 `source_conflict` + `pending_human`，绝不补造数值、DOI、作者或实验结果。
- 无法解析 case.json 的 output_contract：骨架仍含 case_id/status/sources/uncertainty/change_log，标记 `input_error` 并说明缺失字段。
- 写盘失败：降级到最小合法 result.json（元数据 + 缺口说明），不让进程崩溃。

## 7. 脚本接口（相对路径，仅 Python 标准库）

```bash
python3 scripts/main.py bootstrap   --input /input --output /app/output --work /app/work
python3 scripts/main.py discover    --input /input
python3 scripts/main.py extract     --input /input --output /app/output --work /app/work
python3 scripts/main.py patch       --output /app/output --set key=value --note "..." [--source LOC QUOTE] [--status ok]
python3 scripts/main.py finalize    --input /input --output /app/output --work /app/work
python3 scripts/main.py selfcheck   --input /input --output /app/output --work /app/work
python3 scripts/main.py selftest
python3 scripts/selftest.py         # 包结构 + 组件自检
python3 scripts/latex_tools.py compile   --main MAIN.tex --outdir /app/output [--passes 2]
python3 scripts/latex_tools.py classify-log --log LOG [--main MAIN.tex]
python3 scripts/latex_tools.py refcheck  --tex MAIN.tex [--log LOG] [--bib BIB]
```

`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`；`--help` 可查看参数。示例输出仅出现在 `references/`，不得冒充正式案例结果。
